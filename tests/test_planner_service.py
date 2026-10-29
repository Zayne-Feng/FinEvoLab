"""Check planner routing, identity binding, budgets, and completed-phase recovery."""

import json
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from finevolab.config import ModelConfig
from finevolab.grounded import RequestPlan
from finevolab.planning import PlannerClient, PlannerServiceError
from finevolab.runtime import RunBudget
from finevolab.workflow import run_question
from scripts.serve_planner import create_app

ROOT = Path(__file__).resolve().parents[1]


def plan():
    return RequestPlan(intent="metric", companies=["MSFT"], fiscal_year=2024, metrics=["revenue"])


def prediction(client, raw=None):
    return {"identity": client.identity("lora_bf16"), "raw": raw or plan().model_dump_json(),
        "usage": {"requests": 1, "input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
        "elapsed_seconds": 0.01, "peak_allocated_bytes": 100}


class PlannerClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_is_not_retried_or_reported_as_zero_usage(self):
        client = PlannerClient()
        original = httpx.AsyncClient
        calls = []
        def timeout(request):
            calls.append(request)
            raise httpx.ReadTimeout("Read timed out", request=request)
        candidate = json.loads((ROOT / "prompts/benchmark.json").read_text())
        with patch("finevolab.planning.httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(timeout))):
            with self.assertRaises(PlannerServiceError) as context:
                await client.plan("lora_bf16", "Give MSFT FY2024 revenue.", [], candidate, 1)
        self.assertIsNone(context.exception.usage)
        self.assertEqual(len(calls), 1)

    async def test_response_identity_and_invalid_schema_are_rejected(self):
        client = PlannerClient()
        original = httpx.AsyncClient
        candidate = json.loads((ROOT / "prompts/benchmark.json").read_text())
        for body, error in (({**prediction(client), "identity": {}}, "identity"), (prediction(client, "{}"), "invalid request plan")):
            transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
            with patch("finevolab.planning.httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=transport)):
                with self.assertRaisesRegex(PlannerServiceError, error):
                    await client.plan("lora_bf16", "Give MSFT FY2024 revenue.", [], candidate, 10)

    async def test_lora_usage_is_counted_and_completed_recovery_needs_no_service(self):
        client = PlannerClient()
        client.plan = AsyncMock(return_value=(plan(), prediction(client)))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data/processed").mkdir(parents=True)
            shutil.copyfile(ROOT / "data/processed/reports.json", root / "data/processed/reports.json")
            settings = ModelConfig("example", "https://example.com/", "unused")
            candidate = json.loads((ROOT / "prompts/benchmark.json").read_text())
            with patch("finevolab.planning.PlannerClient", return_value=client), patch("finevolab.workflow.save_task"):
                first = await run_question("lora1", "Give MSFT FY2024 revenue.", settings, candidate, root,
                                           render=False, planner_backend="lora_bf16")
                client.plan.side_effect = PlannerServiceError("Service offline")
                resumed = await run_question("lora1", "Give MSFT FY2024 revenue.", settings, candidate, root,
                                             render=False, planner_backend="lora_bf16")
                self.assertEqual(first["phases"], resumed["phases"])
                self.assertEqual(first["phases"]["analyze"]["result"]["usage"]["total_tokens"], 20)
                self.assertEqual(first["phases"]["analyze"]["result"]["usage"]["requests"], 1)
                planning = next(op for op in first["operations"] if op["operation"] == "RequestPlan")
                self.assertEqual(planning["tokens"], 20)
                self.assertEqual(planning["phase"], "analyze")
                self.assertEqual(planning["model"], client.identity("lora_bf16")["model"])
                client.plan.assert_awaited_once()
                with self.assertRaises(ValueError):
                    await run_question("lora1", "Give MSFT FY2024 revenue.", settings, candidate, root,
                                       render=False, planner_backend="lora_nf4")

    async def test_empty_budget_rejects_before_remote_generation(self):
        client = PlannerClient()
        client.plan = AsyncMock()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data/processed").mkdir(parents=True)
            shutil.copyfile(ROOT / "data/processed/reports.json", root / "data/processed/reports.json")
            with patch("finevolab.planning.PlannerClient", return_value=client), patch("finevolab.workflow.save_task"):
                with self.assertRaises(ValueError):
                    await run_question("budget", "Give MSFT FY2024 revenue.", ModelConfig("x", "https://example.com/", "x"),
                        {}, root, render=False, planner_backend="lora_bf16", budget=RunBudget(max_model_calls=0))
                client.plan.assert_not_awaited()


class PlannerEndpointTests(unittest.TestCase):
    def test_busy_planner_rejects_without_a_second_generation(self):
        lock = threading.Lock()
        lock.acquire()
        generator = AsyncMock()
        engine = SimpleNamespace(contract_sha256="a" * 64, lock=lock,
                                 backend=SimpleNamespace(generate=generator))
        try:
            with TestClient(create_app(engine)) as client:
                response = client.post("/v1/plans", json={"question": "Revenue", "backend": "lora_nf4",
                                                           "contract_sha256": "a" * 64})
            self.assertEqual(response.status_code, 429)
            generator.assert_not_called()
        finally:
            lock.release()

    def test_contract_mismatch_never_calls_the_model(self):
        generator = AsyncMock()
        engine = SimpleNamespace(contract_sha256="a" * 64, lock=threading.Lock(), active="lora_bf16",
            identity=lambda backend: {}, backend=SimpleNamespace(generate=generator))
        with TestClient(create_app(engine)) as client:
            response = client.post("/v1/plans", json={"question": "Revenue", "backend": "lora_bf16",
                                                       "contract_sha256": "b" * 64})
            self.assertEqual(response.status_code, 409)
            generator.assert_not_called()

    def test_web_request_has_explicit_supported_backends(self):
        from finevolab.web import QuestionRequest
        self.assertEqual(QuestionRequest(question="Revenue").planner_backend, "api")
        with self.assertRaises(ValueError):
            QuestionRequest(question="Revenue", planner_backend="unknown")


if __name__ == "__main__":
    unittest.main()
