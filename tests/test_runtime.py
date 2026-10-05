"""Verify durable phase recovery and execution limits."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from finevolab.runtime import BudgetExceeded, BudgetHooks, PhaseRunner, RunBudget, read_json, write_json


class AtomicWriteTests(unittest.TestCase):
    def test_concurrent_checkpoint_readers_preserve_atomic_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_json(path, {"iteration": -1})
            stop = Event()
            def read_until_stopped():
                while not stop.is_set():
                    self.assertIsInstance(read_json(path)["iteration"], int)
            with ThreadPoolExecutor(max_workers=4) as executor:
                readers = [executor.submit(read_until_stopped) for _ in range(4)]
                try:
                    for index in range(20):
                        write_json(path, {"iteration": index, "payload": "x" * 10000})
                finally:
                    stop.set()
                    for reader in readers:
                        reader.result()
            self.assertEqual(read_json(path)["iteration"], 19)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_windows_reader_lock_retries_without_exposing_partial_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_json(path, {"status": "pending"})
            replace = Path.replace
            attempts = []
            error = PermissionError("The destination is open by another reader")
            error.winerror = 5
            def locked_once(source, target):
                attempts.append(source)
                if len(attempts) == 1:
                    self.assertEqual(json.loads(path.read_text()), {"status": "pending"})
                    raise error
                return replace(source, target)
            with patch.object(Path, "replace", locked_once), patch("finevolab.runtime.time.sleep"):
                write_json(path, {"status": "complete", "value": 42})
            self.assertEqual(json.loads(path.read_text()), {"status": "complete", "value": 42})
            self.assertEqual(len(attempts), 2)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_persistent_windows_denial_preserves_existing_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_json(path, {"value": 42})
            error = PermissionError("Access remains denied")
            error.winerror = 5
            with patch.object(Path, "replace", side_effect=error) as replace, patch("finevolab.runtime.time.sleep"):
                with self.assertRaises(PermissionError):
                    write_json(path, {"value": 100})
            self.assertGreater(replace.call_count, 1)
            self.assertLessEqual(replace.call_count, 10)
            self.assertEqual(json.loads(path.read_text()), {"value": 42})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_non_windows_permission_error_is_propagated_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(Path, "replace", side_effect=PermissionError("Access denied")) as replace:
                with self.assertRaises(PermissionError):
                    write_json(Path(directory) / "state.json", {})
            self.assertEqual(replace.call_count, 1)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_phase_resumes_without_repeating_complete_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PhaseRunner(Path(directory), "task", {"data": "v1"})
            calls = []
            async def first(state):
                calls.append("first")
                return {"value": 42}
            async def failing(state):
                raise ConnectionError("Transient failure")
            with self.assertRaises(ConnectionError):
                await runner.run({"first": first, "second": failing})
            async def second(state):
                self.assertEqual(state["phases"]["first"]["result"]["value"], 42)
                return {"recovered": True}
            state = await runner.run({"first": first, "second": second})
            self.assertEqual(state["status"], "complete")
            self.assertEqual(calls, ["first"])

    async def test_version_mismatch_rejects_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            await PhaseRunner(Path(directory), "task", {"model": "v1"}).run({})
            with self.assertRaisesRegex(ValueError, "versions differ"):
                await PhaseRunner(Path(directory), "task", {"model": "v2"}).run({})

    async def test_phase_timeout_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PhaseRunner(Path(directory), "task", {})
            async def slow(state):
                await asyncio.sleep(1)
                return {}
            with self.assertRaises(TimeoutError):
                await runner.run({"slow": slow}, timeout_seconds=0.01)
            state = json.loads(runner.path.read_text())
            self.assertEqual(state["phases"]["slow"]["status"], "failed")

    async def test_actual_process_exit_can_resume_running_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            code = """
import asyncio, os, sys
from pathlib import Path
from finevolab.runtime import PhaseRunner
async def first(state):
    return {'persisted': 42}
async def interrupted(state):
    os._exit(17)
asyncio.run(PhaseRunner(Path(sys.argv[1]), 'task', {}).run({'first':first,'second':interrupted}))
"""
            process = subprocess.run([sys.executable, "-c", code, directory], capture_output=True)
            self.assertEqual(process.returncode, 17)
            runner = PhaseRunner(Path(directory), "task", {})
            async def forbidden(state):
                raise AssertionError("A completed phase was repeated")
            async def resumed(state):
                return {"recovered": True}
            state = await runner.run({"first": forbidden, "second": resumed})
            self.assertEqual(state["phases"]["first"]["result"]["persisted"], 42)

    async def test_model_call_and_tool_budgets_stop_execution(self):
        hooks = BudgetHooks(RunBudget(max_model_calls=1, max_tool_calls=1), [])
        await hooks.on_llm_start(None, None, "", [])
        with self.assertRaises(BudgetExceeded):
            await hooks.on_llm_start(None, None, "", [])
        await hooks.on_tool_start(None, None, SimpleNamespace(name="test"))
        with self.assertRaises(BudgetExceeded):
            await hooks.on_tool_start(None, None, SimpleNamespace(name="test"))


if __name__ == "__main__":
    unittest.main()
