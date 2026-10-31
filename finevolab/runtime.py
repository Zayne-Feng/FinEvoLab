"""Persist business phases and enforce bounded agent execution."""

import asyncio
from dataclasses import dataclass
from functools import cache
import hashlib
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import time
from threading import RLock
from uuid import uuid4

from finevolab.tracing import ACTIVE_TRACE, span, trace_scope

from agents import RunHooks
from filelock import FileLock


ACTIVE_PHASE = ContextVar("execution_phase", default="analyze")
ACTIVE_ATTEMPT = ContextVar("execution_attempt", default=1)
ACTIVE_OPERATIONS = ContextVar("execution_operations", default=None)


@contextmanager
def observe(events: list, kind: str, operation: str, **attributes):
    record = {"event": "operation", "kind": kind, "operation": operation,
              "phase": ACTIVE_PHASE.get(), "attempt": ACTIVE_ATTEMPT.get(), "status": "running", **attributes}
    events.append(record)
    operations = ACTIVE_OPERATIONS.get()
    if operations is not None and operations is not events:
        operations.append(record)
    started = time.perf_counter()
    try:
        with span(operation, kind=kind, phase=record["phase"], attempt=record["attempt"]) as traced:
            if traced:
                record.update(trace_id=traced["trace_id"], span_id=traced["span_id"],
                              parent_span_id=traced["parent_span_id"])
            try:
                yield record
            finally:
                if traced:
                    traced["attributes"].update({key: record[key] for key in ("cache_hit", "tokens", "backend", "model") if key in record})
    except BaseException as error:
        record.update(status="failed", error_type=type(error).__name__)
        raise
    else:
        record["status"] = "complete"
    finally:
        record["duration_seconds"] = round(time.perf_counter() - started, 6)


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@cache
def _json_lock(path: Path):
    """Serialize JSON reads and replacements for the same resolved path."""
    return RLock()


def read_json(path: Path):
    with _json_lock(path.resolve()):
        return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    with _json_lock(path.resolve()):
        _write_json(path, value)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(6):
            try:
                temporary.replace(path)
                break
            except PermissionError as error:
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 5:
                    raise
                time.sleep(min(0.01 * 2 ** attempt, 0.1))
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class RunBudget:
    max_model_calls: int = 6
    max_tool_calls: int = 10
    max_total_tokens: int = 20000
    max_context_chars: int = 24000
    timeout_seconds: int = 180


class BudgetExceeded(ValueError):
    pass


class ToolExecutor:
    def __init__(self, budget: RunBudget, events: list, version: dict, cache_root: Path | None = None, coalesce: bool = True, on_call=None):
        self.budget, self.events, self.version = budget, events, version
        self.cache_root, self.calls, self.started = cache_root, 0, time.monotonic()
        self.coalesce, self.on_call = coalesce, on_call

    def call(self, name: str, parameters: dict, function, schema=None):
        with observe(self.events, "tool", name, call=self.calls + 1) as record:
            result = self._call(name, parameters, function, schema)
            record["cache_hit"] = self.events[-1].get("cache_hit", False)
            return result

    def _call(self, name: str, parameters: dict, function, schema=None):
        if self.calls >= self.budget.max_tool_calls:
            self.events.append({"event": "tool_rejected", "tool": name, "parameters": parameters,
                                "reason": "tool_budget", "completed_calls": self.calls})
            raise BudgetExceeded("Tool call budget exceeded")
        if time.monotonic() - self.started >= self.budget.timeout_seconds:
            self.events.append({"event": "tool_rejected", "tool": name, "parameters": parameters,
                                "reason": "time_budget", "completed_calls": self.calls})
            raise BudgetExceeded("Tool execution time budget exceeded")
        self.calls += 1
        if self.on_call:
            self.on_call(self.calls)
        key = fingerprint({"tool": name, "parameters": parameters, "version": self.version})
        path = self.cache_root / (key + ".json") if self.cache_root else None
        hit, value = False, None
        if path and path.exists():
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
                if entry["key"] == key and fingerprint({"value": entry["value"]}) == entry["value_hash"]:
                    value = schema.model_validate(entry["value"]) if schema else entry["value"]
                    hit = True
            except (ValueError, KeyError):
                pass
        coalesced = False
        if not hit:
            def compute():
                if path and path.exists():
                    try:
                        cached = read_json(path)
                        if cached["key"] == key and fingerprint({"value": cached["value"]}) == cached["value_hash"]:
                            return schema.model_validate(cached["value"]) if schema else cached["value"]
                    except (ValueError, KeyError):
                        pass
                result = function()
                if path:
                    payload = result.model_dump(mode="json", exclude_unset=True) if hasattr(result, "model_dump") else result
                    write_json(path, {"key": key, "value": payload, "value_hash": fingerprint({"value": payload})})
                return result
            try:
                if self.coalesce and path:
                    from finevolab.singleflight import SINGLEFLIGHT
                    value, coalesced = SINGLEFLIGHT.run(str(path.resolve()), compute,
                        timeout=max(0.001, self.budget.timeout_seconds - (time.monotonic() - self.started)))
                else:
                    value = compute()
            except Exception as error:
                self.events.append({"event": "tool_execution", "tool": name, "call": self.calls,
                    "parameters": parameters, "cache_hit": False, "cache_key": key,
                    "status": "failed", "error_type": type(error).__name__})
                raise
        self.events.append({"event": "tool_execution", "tool": name, "call": self.calls,
                            "parameters": parameters, "cache_hit": hit, "coalesced": coalesced, "cache_key": key})
        if time.monotonic() - self.started >= self.budget.timeout_seconds:
            raise BudgetExceeded("Tool execution time budget exceeded")
        return value


class BudgetHooks(RunHooks):
    def __init__(self, budget: RunBudget, events: list):
        self.budget, self.events = budget, events
        self.started = time.monotonic()
        self.model_calls = self.tool_calls = self.total_tokens = 0

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        if self.model_calls >= self.budget.max_model_calls:
            raise BudgetExceeded("Model call budget exceeded")
        if time.monotonic() - self.started >= self.budget.timeout_seconds:
            raise BudgetExceeded("Runtime budget exceeded")
        if self.total_tokens >= self.budget.max_total_tokens:
            raise BudgetExceeded("Token budget exceeded")
        size = len(system_prompt or "") + len(json.dumps(input_items, default=str))
        if size > self.budget.max_context_chars:
            raise BudgetExceeded("Context budget exceeded")
        self.model_calls += 1
        self.events.append({"event": "model_start", "call": self.model_calls, "context_chars": size})

    async def on_llm_end(self, context, agent, response):
        self.total_tokens += response.usage.total_tokens
        self.events.append({"event": "model_end", "tokens": response.usage.total_tokens})
        if self.total_tokens > self.budget.max_total_tokens:
            raise BudgetExceeded("Token budget exceeded")

    async def on_tool_start(self, context, agent, tool):
        if self.tool_calls >= self.budget.max_tool_calls:
            raise BudgetExceeded("Tool call budget exceeded")
        self.tool_calls += 1
        self.events.append({"event": "tool_start", "tool": tool.name})


class PhaseRunner:
    def __init__(self, root: Path, task_id: str, versions: dict):
        if not task_id.isalnum() or len(task_id) > 64:
            raise ValueError("Invalid task ID")
        self.directory = root / task_id
        self.path = self.directory / "state.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.signature = fingerprint(versions)
        self.versions = versions
        self.lock = FileLock(str(self.directory / "run.lock"))

    async def run(self, phases: dict, timeout_seconds: int = 240) -> dict:
        with trace_scope("workflow", self.directory / "traces") as (_, operation):
            operation["attributes"]["task_id"] = self.directory.name
            return await self._run(phases, timeout_seconds)

    async def _run(self, phases: dict, timeout_seconds: int) -> dict:
        with self.lock.acquire(timeout=0):
            state = read_json(self.path) if self.path.exists() else {
                "task_id": self.directory.name, "signature": self.signature,
                "versions": self.versions, "status": "pending", "phases": {},
            }
            if state["signature"] != self.signature:
                raise ValueError("Task versions differ from the saved checkpoint")
            if state["status"] == "complete" and all(
                    state["phases"].get(name, {}).get("status") == "complete" for name in phases):
                return state
            started = time.perf_counter()
            elapsed = state.get("elapsed_seconds", 0.0)
            state["trace_id"] = ACTIVE_TRACE.get().trace_id
            state.setdefault("history", [])
            state["status"] = "running"
            write_json(self.path, state)
            try:
                for name, function in phases.items():
                    saved = state["phases"].get(name, {})
                    if saved.get("status") == "complete":
                        continue
                    attempt = saved.get("attempts", 0) + 1
                    phase_elapsed = saved.get("duration_seconds", 0.0)
                    state["phases"][name] = {"status": "running", "attempts": attempt}
                    state["history"].append({"phase": name, "status": "running", "attempt": attempt})
                    write_json(self.path, state)
                    phase_started = time.perf_counter()
                    token = ACTIVE_PHASE.set(name)
                    attempt_token = ACTIVE_ATTEMPT.set(attempt)
                    operations_token = ACTIVE_OPERATIONS.set(state.setdefault("operations", []))
                    try:
                        remaining = timeout_seconds - (time.perf_counter() - started)
                        if remaining <= 0:
                            raise TimeoutError("Task timeout exceeded")
                        with span("phase." + name, kind="phase", phase=name, attempt=attempt, recovered=attempt > 1):
                            result = await asyncio.wait_for(function(state), timeout=remaining)
                    except BaseException as error:
                        state["status"] = "failed"
                        state["phases"][name].update(status="failed", error_type=type(error).__name__)
                        raise
                    else:
                        state["phases"][name].update(status="complete", result=result)
                    finally:
                        ACTIVE_PHASE.reset(token)
                        ACTIVE_ATTEMPT.reset(attempt_token)
                        ACTIVE_OPERATIONS.reset(operations_token)
                        duration = time.perf_counter() - phase_started
                        phase = state["phases"][name]
                        phase.update(attempt_seconds=round(duration, 6),
                                     duration_seconds=round(phase_elapsed + duration, 6))
                        state["history"].append({"phase": name, "status": phase["status"],
                            "attempt": attempt, "duration_seconds": phase["attempt_seconds"],
                            **({"error_type": phase["error_type"]} if "error_type" in phase else {})})
                        write_json(self.path, state)
                state["status"] = "complete"
            finally:
                state["elapsed_seconds"] = round(elapsed + time.perf_counter() - started, 6)
                write_json(self.path, state)
            return state
