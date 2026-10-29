"""Verify duration accounting, recovery, and public operation fields."""

import asyncio
from pathlib import Path
import tempfile
import unittest

from finevolab.experience_data import public_result
from finevolab.runtime import PhaseRunner, RunBudget, ToolExecutor, observe, read_json


class ObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_and_recovery_preserve_elapsed_durations(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PhaseRunner(Path(directory), "task", {})
            events = []
            async def prepare(state):
                with observe(events, "tool", "prepare_input"):
                    await asyncio.sleep(0.002)
                return {}
            async def fail(state):
                with observe(events, "model", "RequestPlan"):
                    await asyncio.sleep(0.002)
                    raise ConnectionError("Unavailable")
            with self.assertRaises(ConnectionError):
                await runner.run({"prepare": prepare, "analyze": fail})
            failed = read_json(runner.path)
            self.assertEqual(public_result(failed)["operations"][-1]["status"], "failed")
            self.assertEqual(public_result(failed)["operations"][-1]["attempt"], 1)
            elapsed = failed["elapsed_seconds"]
            async def analyze(state):
                return {}
            state = await runner.run({"prepare": prepare, "analyze": analyze})
            self.assertGreater(state["elapsed_seconds"], elapsed)
            self.assertEqual(state["phases"]["prepare"]["attempts"], 1)
            self.assertEqual(state["phases"]["analyze"]["attempts"], 2)
            self.assertEqual(events[0]["phase"], "prepare")
            self.assertEqual(events[1]["phase"], "analyze")
            self.assertEqual(events[1]["status"], "failed")
            self.assertEqual(events[1]["error_type"], "ConnectionError")
            self.assertTrue(all("at" not in item for item in state["history"]))
            saved = runner.path.read_bytes()
            self.assertEqual(await runner.run({"prepare": prepare, "analyze": analyze}), state)
            self.assertEqual(runner.path.read_bytes(), saved)

    async def test_cancelled_phase_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PhaseRunner(Path(directory), "task", {})
            async def cancel(state):
                raise asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await runner.run({"analyze": cancel})
            state = read_json(runner.path)
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["phases"]["analyze"]["error_type"], "CancelledError")
            self.assertGreaterEqual(state["phases"]["analyze"]["duration_seconds"], 0)

    async def test_tool_cache_and_public_trace_do_not_expose_transcripts(self):
        with tempfile.TemporaryDirectory() as directory:
            events, calls = [], []
            executor = ToolExecutor(RunBudget(), events, {"data": "v1"}, Path(directory))
            def calculate():
                calls.append(1)
                return {"value": 42}
            first = executor.call("calculate", {}, calculate)
            self.assertEqual(executor.call("calculate", {}, calculate), first)
            self.assertEqual(calls, [1])
            operations = [item for item in events if item["event"] == "operation"]
            self.assertEqual([item["cache_hit"] for item in operations], [False, True])
            operations[0]["messages"] = "private transcript"
            state = {"task_id": "task", "status": "complete", "phases": {
                "analyze": {"status": "complete", "duration_seconds": 1.0,
                    "result": {"events": events, "messages": "private transcript"}}}}
            result = public_result(state)
            self.assertEqual(len(result["operations"]), 2)
            self.assertNotIn("private transcript", str(result))
            self.assertEqual(result["phases"]["analyze"]["duration_seconds"], 1.0)


if __name__ == "__main__":
    unittest.main()
