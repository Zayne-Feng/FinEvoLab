"""Verify trace isolation, propagation, privacy, failures, and workflow recovery."""

import asyncio
import json
from pathlib import Path
import tempfile
import shutil
from uuid import uuid4
import unittest
from types import SimpleNamespace
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from finevolab.mcp_client import call_financial_tool
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI
from finevolab.config import ModelConfig
from finevolab.tools import FinancialStore
from finevolab.grounded import RequestPlan
from finevolab.workflow import run_question
from fastapi import FastAPI

from finevolab.runtime import PhaseRunner, observe
from finevolab.service_metrics import summarize
from finevolab.tracing import ACTIVE_TRACE, TraceMiddleware, parse_traceparent, span, trace_scope, traceparent


class TraceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from finevolab.tracing import tracing_mode
        scope = tracing_mode("file")
        scope.__enter__()
        self.addCleanup(scope.__exit__, None, None, None)

    def test_traceparent_validation(self):
        for value in (None, "bad", "00-" + "0" * 32 + "-" + "1" * 16 + "-01",
                      "00-" + "1" * 32 + "-" + "0" * 16 + "-01"):
            self.assertIsNone(parse_traceparent(value))
        self.assertEqual(parse_traceparent("00-" + "1" * 32 + "-" + "2" * 16 + "-01"), ("1" * 32, "2" * 16))

    async def test_nested_spans_cross_threads_and_privacy(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            with trace_scope("request", Path(directory)) as (trace, root):
                with observe(events, "model", "RequestPlan", question="PRIVATE", api_key="SECRET") as event:
                    event["tokens"] = 42
                    parent = traceparent()
                    await asyncio.to_thread(lambda: self.assertEqual(traceparent(), parent))
            payload = json.loads(next(Path(directory).glob("*.json")).read_text())
            self.assertEqual(len(payload["spans"]), 2)
            self.assertEqual(payload["spans"][1]["parent_span_id"], root["span_id"])
            self.assertEqual(payload["spans"][1]["attributes"]["tokens"], 42)
            self.assertNotIn("SECRET", json.dumps(payload))
            self.assertNotIn("PRIVATE", json.dumps(payload))
            self.assertEqual(events[0]["trace_id"], trace.trace_id)
            self.assertIsNone(ACTIVE_TRACE.get())

    async def test_concurrent_context_isolation(self):
        async def run():
            with trace_scope("job") as (trace, _):
                await asyncio.sleep(0)
                self.assertEqual(ACTIVE_TRACE.get(), trace)
                return trace.trace_id
        self.assertEqual(len(set(await asyncio.gather(*(run() for _ in range(12))))), 12)

    async def test_failures_and_timeout_recovery_keep_phase_parentage(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PhaseRunner(Path(directory), "task", {"data": "fixed"})
            calls = []
            async def prepare(state):
                calls.append("prepare")
                return {"ready": True}
            async def slow(state):
                await asyncio.sleep(0.05)
            with self.assertRaises(TimeoutError):
                await runner.run({"prepare": prepare, "analyze": slow}, timeout_seconds=0.01)
            async def analyze(state):
                with observe([], "tool", "get_financial_metrics"):
                    return {"value": 42}
            state = await runner.run({"prepare": prepare, "analyze": analyze})
            self.assertEqual(calls, ["prepare"])
            self.assertEqual(state["status"], "complete")
            spans = [span for path in (runner.directory / "traces").glob("*.json")
                     for span in json.loads(path.read_text())["spans"]]
            self.assertTrue(any(s["status"] == "timeout" for s in spans))
            self.assertTrue(any(s["attributes"].get("recovered") for s in spans))
            self.assertTrue(all(s["duration_seconds"] >= 0 for s in spans))

    async def test_http_traceparent_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            app = FastAPI()
            app.add_middleware(TraceMiddleware, directory=Path(directory))
            @app.get("/health")
            async def health():
                with span("retrieve", question="PRIVATE"):
                    return {"status": "ok"}
            incoming = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/health", headers={"traceparent": incoming})
            self.assertEqual(parse_traceparent(response.headers["traceparent"])[0], "a" * 32)
            data = json.loads(next(Path(directory).glob("*.json")).read_text())
            self.assertEqual(data["spans"][0]["parent_span_id"], "b" * 16)
            self.assertEqual(data["spans"][0]["attributes"]["status_code"], 200)

    async def test_mcp_timeout_is_classified_and_parent_context_restored(self):
        async def timed_out(*args, **kwargs):
            raise McpError(ErrorData(code=408, message="Timeout"))
        with trace_scope("request") as (trace, _):
            parent = traceparent()
            with self.assertRaises(TimeoutError):
                await call_financial_tool(SimpleNamespace(call_tool=timed_out), "get_financial_metrics", {})
            self.assertEqual(traceparent(), parent)
        self.assertEqual(trace.spans[1]["status"], "timeout")

    async def test_background_completion_updates_the_http_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            app = FastAPI()
            app.add_middleware(TraceMiddleware, directory=Path(directory))
            jobs = []
            @app.get("/submit")
            async def submit():
                async def job():
                    with span("background.workflow"):
                        await asyncio.sleep(0.02)
                        with span("background.tool"):
                            pass
                jobs.append(asyncio.create_task(job()))
                await asyncio.sleep(0)
                return {"status": "accepted"}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/submit")
                await asyncio.gather(*jobs)
            payload = json.loads(next(Path(directory).glob("*.json")).read_text())
            self.assertEqual(len(payload["spans"]), 3)
            self.assertEqual(payload["trace_id"], parse_traceparent(response.headers["traceparent"])[0])
            self.assertTrue(all(s["status"] == "complete" for s in payload["spans"]))

    async def test_http_sdk_tools_validation_and_report_share_one_trace(self):
        from finevolab.web import app
        root = Path(__file__).resolve().parents[1]
        bodies = []
        async def respond(request):
            bodies.append(json.loads(request.content))
            plan = RequestPlan(intent="growth", companies=["MSFT"], fiscal_year=2024,
                               report_year=2024, metrics=["revenue"])
            return httpx.Response(200, json={"id": "local-trace", "object": "chat.completion",
                "created": 0, "model": "fixture", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": plan.model_dump_json()}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
        def client(settings):
            return AsyncOpenAI(api_key="local-fixture", base_url="https://fixture.invalid/", max_retries=0,
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        settings = ModelConfig("fixture", "https://fixture.invalid/", "local-fixture")
        incoming = "00-" + uuid4().hex + "-" + "b" * 16 + "-01"
        task_directory = None
        traces = []
        try:
            with patch("finevolab.workflow.save_task"), patch("finevolab.web.ModelConfig.load", return_value=settings), patch("finevolab.grounded.async_client", side_effect=client):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                    response = await http.post("/api/questions", json={"question": "Calculate Microsoft FY2024 revenue growth."},
                                               headers={"traceparent": incoming})
            self.assertEqual(response.status_code, 200, response.text[:200])
            state = response.json()
            task_directory = root / "output/tasks" / state["task_id"]
            self.assertEqual(state["trace_id"], parse_traceparent(incoming)[0])
            self.assertEqual(state["phases"]["validate"]["result"]["status"], "passed")
            self.assertTrue(bodies)
            traces = list((root / "output/traces").glob(state["trace_id"] + ".*.json"))
            spans = [item for path in traces for item in json.loads(path.read_text())["spans"]]
            names = {item["name"] for item in spans}
            self.assertTrue({"http.request", "workflow", "phase.prepare", "phase.analyze", "RequestPlan",
                             "calculate_metrics", "phase.validate", "phase.report", "build_report"} <= names, names)
            self.assertTrue(all(item["trace_id"] == state["trace_id"] for item in spans))
            growth = state["phases"]["analyze"]["result"]["answer"]["calculations"][0]
            expected = FinancialStore(root / "data/processed/reports.json").growth("MSFT", "revenue", 2024)
            self.assertEqual(growth, expected.model_dump(mode="json"))
        finally:
            if task_directory is not None:
                if not task_directory.resolve().is_relative_to((root / "output/tasks").resolve()):
                    raise ValueError("Task cleanup escaped the task directory")
                shutil.rmtree(task_directory)
            for path in traces:
                path.unlink()

    def test_export_failure_does_not_replace_business_result(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("finevolab.runtime.write_json", side_effect=OSError("Disk unavailable")):
                with trace_scope("request", Path(directory)) as (trace, _):
                    value = 42
            self.assertEqual(value, 42)
            self.assertEqual(trace.export_errors, 1)


class ServiceMetricTests(unittest.TestCase):
    def test_nearest_rank_failures_and_timeout_denominator(self):
        rows = [{"elapsed_seconds": i / 100, "status": "complete"} for i in range(1, 21)]
        rows[-1]["status"] = "timeout"
        rows[-2]["status"] = "failed"
        result = summarize(rows, 2)
        self.assertEqual(result["p50_seconds"], 0.1)
        self.assertEqual(result["p95_seconds"], 0.19)
        self.assertEqual(result["requests"], 20)
        self.assertEqual(result["timeouts"], 1)
        self.assertAlmostEqual(result["failure_rate"], 0.1)
        self.assertEqual(result["throughput_requests_per_second"], 10)

    def test_invalid_measurements_are_rejected(self):
        for rows, wall in (([], 1), ([{"elapsed_seconds": -1, "status": "complete"}], 1),
                           ([{"elapsed_seconds": float("nan"), "status": "complete"}], 1),
                           ([{"elapsed_seconds": 1, "status": "complete"}], 0)):
            with self.assertRaises(ValueError):
                summarize(rows, wall)


class ExportModeTests(unittest.TestCase):
    def test_off_and_memory_modes_preserve_context_without_export(self):
        from finevolab.tracing import tracing_mode
        with tempfile.TemporaryDirectory() as directory:
            for mode, count in (("off", 0), ("memory", 2)):
                with tracing_mode(mode), trace_scope("request", Path(directory)) as (trace, _):
                    with span("tool"):
                        self.assertIsNotNone(traceparent())
                self.assertEqual(len(trace.spans), count)
            self.assertEqual(list(Path(directory).glob("*.json")), [])

    def test_batch_flush_persists_final_spans(self):
        from finevolab.tracing import tracing_mode, flush_exports
        with tempfile.TemporaryDirectory() as directory:
            with tracing_mode("batch"), trace_scope("request", Path(directory)):
                with span("tool"):
                    pass
            stats = flush_exports()
            self.assertEqual(stats["pending"], 0)
            payload = json.loads(next(Path(directory).glob("*.json")).read_text())
            self.assertEqual(len(payload["spans"]), 2)
            self.assertTrue(all(s["status"] == "complete" for s in payload["spans"]))

    def test_batch_failure_is_counted_without_replacing_result(self):
        from finevolab.tracing import tracing_mode, flush_exports
        before = flush_exports()["failed"]
        with tempfile.TemporaryDirectory() as directory:
            with patch("finevolab.runtime.write_json", side_effect=OSError("Disk unavailable")):
                with tracing_mode("batch"), trace_scope("request", Path(directory)) as (trace, _):
                    result = 42
                stats = flush_exports()
            self.assertEqual(result, 42)
            self.assertEqual(trace.export_errors, 1)
            self.assertEqual(stats["failed"], before + 1)

    def test_bounded_export_queue_applies_backpressure(self):
        from finevolab.tracing import TraceExporter
        from threading import Event, Thread
        started, release, submitted = Event(), Event(), Event()
        def write(*args):
            started.set()
            release.wait(timeout=2)
        owner = SimpleNamespace(export_errors=0)
        with patch("finevolab.runtime.write_json", side_effect=write):
            queue = TraceExporter(capacity=1)
            try:
                queue.submit(Path("unused.json"), {}, owner)
                self.assertTrue(started.wait(timeout=1))
                queue.submit(Path("unused.json"), {}, owner)
                def third():
                    queue.submit(Path("unused.json"), {}, owner)
                    submitted.set()
                thread = Thread(target=third)
                thread.start()
                self.assertFalse(submitted.wait(timeout=0.02))
                release.set()
                thread.join(timeout=2)
                stats = queue.flush()
                self.assertEqual(stats["written"], 3)
                self.assertEqual(stats["pending"], 0)
                self.assertGreaterEqual(stats["backpressure"], 1)
            finally:
                release.set()
                queue.close()
