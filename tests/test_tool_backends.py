"""Verify MCP business routing, snapshot identity, and private-document isolation."""

import asyncio
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from openai import AsyncOpenAI

from finevolab.config import ModelConfig
from finevolab.grounded import RequestPlan, assemble_numeric
from finevolab.runtime import ToolExecutor, RunBudget
from finevolab.tools import FinancialStore, BoundedFinancialStore
from finevolab.tool_backends import financial_backend, MCPFinancialStore
from finevolab.tracing import trace_scope

ROOT = Path(__file__).resolve().parents[1]


class ToolBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_mcp_business_objects_equal_direct_tools(self):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        plans = [RequestPlan(intent=intent, companies=companies, fiscal_year=2024, metrics=["revenue"])
                 for intent, companies in (("growth", ["MSFT"]), ("comparison", ["AAPL", "MSFT"]),
                                            ("alignment", ["AAPL", "MSFT"]))]
        with trace_scope("business.test"):
            async with financial_backend(store, ROOT, "mcp", 60) as remote:
                bounded = BoundedFinancialStore(remote, ToolExecutor(RunBudget(), [], {"data": store.version}))
                for plan in plans:
                    actual = await asyncio.to_thread(assemble_numeric, plan, bounded, [])
                    self.assertEqual(actual, assemble_numeric(plan, store, []))
                result = await asyncio.to_thread(remote.derive, "MSFT", "operating_margin_change", 2024)
                self.assertEqual(result, store.derive("MSFT", "operating_margin_change", 2024))
                self.assertGreaterEqual(remote.remote_calls, 4)

    async def test_snapshot_mismatch_is_rejected(self):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        remote = MCPFinancialStore(store, None, asyncio.get_running_loop(), 30)
        with patch("finevolab.tool_backends.call_financial_tool", AsyncMock(return_value={"data": {}, "data_version": "wrong"})):
            with self.assertRaisesRegex(ValueError, "identity"):
                await asyncio.to_thread(remote.get_metric, "MSFT", "revenue", 2024)

    async def test_private_documents_and_unknown_backends_are_rejected(self):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        store.upload_ids = ["private"]
        for name in ("mcp", "invalid"):
            with self.assertRaises(ValueError):
                async with financial_backend(store, ROOT, name, 30):
                    self.fail("Invalid scope was accepted")

    async def test_bridge_rejects_event_loop_blocking(self):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        remote = MCPFinancialStore(store, None, asyncio.get_running_loop(), 30)
        with self.assertRaisesRegex(RuntimeError, "worker thread"):
            remote.get_metric("MSFT", "revenue", 2024)

    async def test_http_sdk_plan_executes_mcp_and_builds_report(self):
        from finevolab.web import app
        from scripts.benchmark_tool_backends import UncachedToolExecutor
        plan = RequestPlan(intent="growth", companies=["MSFT"], fiscal_year=2023, metrics=["revenue"])
        async def respond(request):
            return httpx.Response(200, json={"id": "local-plan", "object": "chat.completion", "created": 0,
                "model": "fixture", "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": plan.model_dump_json()}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
        def client(settings):
            return AsyncOpenAI(api_key="local-fixture", base_url="https://fixture.invalid/", max_retries=0,
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        settings = ModelConfig("fixture", "https://fixture.invalid/", "local-fixture")
        task = None
        try:
            with patch("finevolab.grounded.ToolExecutor", UncachedToolExecutor), patch("finevolab.workflow.save_task"), patch("finevolab.web.ModelConfig.load", return_value=settings), patch("finevolab.grounded.async_client", side_effect=client):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                    response = await http.post("/api/questions", json={"question": "Calculate Microsoft FY2023 revenue growth.", "tool_backend": "mcp"})
            self.assertEqual(response.status_code, 200, response.text[:300])
            state = response.json()
            task = ROOT / "output/tasks" / state["task_id"]
            output = state["phases"]["analyze"]["result"]
            self.assertEqual(output["tool_backend"], "mcp")
            self.assertEqual(output["remote_tool_calls"], 1)
            self.assertFalse(next(event["cache_hit"] for event in output["events"]
                                  if event.get("event") == "tool_execution" and event.get("tool") == "calculate_metrics"))
            self.assertEqual(state["phases"]["validate"]["result"]["status"], "passed")
            self.assertEqual(output["answer"]["calculations"][0]["company"], "MSFT")
            self.assertTrue(list(task.rglob("*.pdf")))
            self.assertTrue(any(event.get("operation") == "calculate_metrics" for event in output["events"]))
        finally:
            if task is not None:
                if not task.resolve().is_relative_to((ROOT / "output/tasks").resolve()):
                    raise ValueError("Task cleanup escaped the task directory")
                shutil.rmtree(task)
