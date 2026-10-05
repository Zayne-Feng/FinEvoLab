"""Persist business phases and enforce bounded agent execution."""

import asyncio
from dataclasses import dataclass
from functools import cache
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import time
from threading import RLock
from uuid import uuid4

from agents import RunHooks
from filelock import FileLock


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
    def __init__(self, budget: RunBudget, events: list, version: dict, cache_root: Path | None = None):
        self.budget, self.events, self.version = budget, events, version
        self.cache_root, self.calls, self.started = cache_root, 0, time.monotonic()

    def call(self, name: str, parameters: dict, function, schema=None):
        if self.calls >= self.budget.max_tool_calls:
            self.events.append({"event": "tool_rejected", "tool": name, "parameters": parameters,
                                "reason": "tool_budget", "completed_calls": self.calls})
            raise BudgetExceeded("Tool call budget exceeded")
        if time.monotonic() - self.started >= self.budget.timeout_seconds:
            self.events.append({"event": "tool_rejected", "tool": name, "parameters": parameters,
                                "reason": "time_budget", "completed_calls": self.calls})
            raise BudgetExceeded("Tool execution time budget exceeded")
        self.calls += 1
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
        if not hit:
            try:
                value = function()
            except Exception as error:
                self.events.append({"event": "tool_execution", "tool": name, "call": self.calls,
                    "parameters": parameters, "cache_hit": False, "cache_key": key,
                    "status": "failed", "error_type": type(error).__name__})
                raise
            if path:
                payload = value.model_dump(mode="json", exclude_unset=True) if hasattr(value, "model_dump") else value
                write_json(path, {"key": key, "value": payload, "value_hash": fingerprint({"value": payload})})
        self.events.append({"event": "tool_execution", "tool": name, "call": self.calls,
                            "parameters": parameters, "cache_hit": hit, "cache_key": key})
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
        with self.lock.acquire(timeout=0):
            state = read_json(self.path) if self.path.exists() else {
                "task_id": self.directory.name, "signature": self.signature,
                "versions": self.versions, "status": "pending", "phases": {},
            }
            if state["signature"] != self.signature:
                raise ValueError("Task versions differ from the saved checkpoint")
            started = time.monotonic()
            state.setdefault("history", [])
            state["status"] = "running"
            write_json(self.path, state)
            for name, function in phases.items():
                saved = state["phases"].get(name, {})
                if saved.get("status") == "complete":
                    continue
                state["phases"][name] = {"status": "running"}
                state["history"].append({"phase": name, "status": "running",
                                         "at": datetime.now(timezone.utc).isoformat()})
                write_json(self.path, state)
                try:
                    remaining = timeout_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError("Task timeout exceeded")
                    result = await asyncio.wait_for(function(state), timeout=remaining)
                except Exception as error:
                    state["status"] = "failed"
                    state["phases"][name] = {"status": "failed", "error_type": type(error).__name__}
                    state["history"].append({"phase": name, "status": "failed",
                                             "error_type": type(error).__name__,
                                             "at": datetime.now(timezone.utc).isoformat()})
                    write_json(self.path, state)
                    raise
                state["phases"][name] = {"status": "complete", "result": result}
                state["history"].append({"phase": name, "status": "complete",
                                         "at": datetime.now(timezone.utc).isoformat()})
                write_json(self.path, state)
            state["status"] = "complete"
            state["elapsed_seconds"] = round(time.monotonic() - started, 3)
            write_json(self.path, state)
            return state
