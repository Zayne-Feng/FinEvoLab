"""Measure durable recovery and invariant rejection under injected faults."""

from uuid import uuid4
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest
from finevolab.runtime import BudgetExceeded, BudgetHooks, PhaseRunner, RunBudget, write_json
from finevolab.tools import FinancialStore


def increment(path, name):
    counts = json.loads(path.read_text()) if path.exists() else {}
    counts[name] = counts.get(name, 0) + 1
    write_json(path, counts)


async def worker(directory: Path, mode: str, fault: str):
    directory.mkdir(parents=True, exist_ok=True)
    counter, marker = directory / "counts.json", directory / "fault.json"
    async def prepare(state):
        increment(counter, "prepare")
        return {"value": "245122000000"}
    async def analyze(state):
        increment(counter, "analyze")
        if not marker.exists():
            write_json(marker, {"fault": fault})
            if fault == "process_exit":
                os._exit(17)
            if fault == "timeout":
                await asyncio.sleep(10)
            if fault == "connection":
                raise ConnectionError("Injected connection interruption")
        return {"value": "245122000000", "source": "prepared"}
    if mode == "durable":
        await PhaseRunner(directory, "task", {"fixture": "v1"}).run(
            {"prepare": prepare, "analyze": analyze}, timeout_seconds=3 if fault == "timeout" and not marker.exists() else 20)
    else:
        async def execute():
            await prepare({})
            await analyze({})
        await asyncio.wait_for(execute(), timeout=3 if fault == "timeout" and not marker.exists() else 20)


async def rejection_checks():
    records = []
    for name in ("missing", "zero_baseline", "unit_conflict", "period_conflict", "model_budget", "tool_budget"):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        started = time.monotonic()
        try:
            if name == "missing":
                store.get_metric("ZZZZ", "revenue", 2024)
            elif name in {"zero_baseline", "unit_conflict", "period_conflict"}:
                field, value = {"zero_baseline": ("value", 0), "unit_conflict": ("unit", "EUR"),
                                "period_conflict": ("period_kind", "instant")}[name]
                setattr(store.get_metric("MSFT", "revenue", 2023), field, value)
                store.growth("MSFT", "revenue", 2024)
            elif name in {"model_budget", "tool_budget"}:
                hooks = BudgetHooks(RunBudget(max_model_calls=1, max_tool_calls=1), [])
                if name == "model_budget":
                    await hooks.on_llm_start(None, None, "", [])
                    await hooks.on_llm_start(None, None, "", [])
                else:
                    await hooks.on_tool_start(None, None, SimpleNamespace(name="fixture"))
                    await hooks.on_tool_start(None, None, SimpleNamespace(name="fixture"))
        except (ValueError, BudgetExceeded) as error:
            records.append({"scenario": name, "expected_rejection": True, "passed": True,
                            "error_type": type(error).__name__, "elapsed_seconds": time.monotonic() - started})
        else:
            records.append({"scenario": name, "expected_rejection": True, "passed": False})
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--mode", choices=("durable", "stateless"))
    parser.add_argument("--fault", choices=("process_exit", "timeout", "connection"))
    args = parser.parse_args()
    if args.worker:
        try:
            asyncio.run(worker(args.worker, args.mode, args.fault))
        except (TimeoutError, ConnectionError):
            raise SystemExit(18)
        return
    run_id = uuid4().hex[:12] + "-fault-injection"
    target = ROOT / "project_notes/experiments" / run_id
    write_json(target / "manifest.json", manifest(ROOT, run_id, {
        "repeats": 3, "modes": ["durable", "stateless"],
        "faults": ["process_exit", "timeout", "connection"], "model_calls": 0,
        "fault_source": "controlled_local_injection", "financial_value": "245122000000"}))
    rows = []
    for repeat in range(1, 4):
        for fault in ("process_exit", "timeout", "connection"):
            for mode in ("durable", "stateless"):
                directory = ROOT / "output/fault_experiments" / run_id / f"{repeat}_{fault}_{mode}"
                command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(directory), "--mode", mode, "--fault", fault]
                started = time.monotonic()
                first = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30)
                second = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30)
                counts = json.loads((directory / "counts.json").read_text()) if (directory / "counts.json").exists() else {}
                passed = first.returncode == (17 if fault == "process_exit" else 18) and second.returncode == 0
                if mode == "durable" and (directory / "task/state.json").exists():
                    state = json.loads((directory / "task/state.json").read_text())
                    passed = passed and state["status"] == "complete" and state["phases"]["analyze"]["result"]["value"] == "245122000000"
                rows.append({"repeat": repeat, "fault": fault, "mode": mode, "first_exit": first.returncode,
                             "resume_exit": second.returncode, "passed": passed, "phase_calls": counts,
                             "stderr": [item.stderr.decode(errors="replace")[-1200:] for item in (first, second) if item.stderr],
                             "elapsed_seconds": round(time.monotonic() - started, 4)})
                write_json(target / "results.json", {"recovery": rows})
                print(json.dumps(rows[-1]), flush=True)
    rejections = []
    for repeat in range(1, 4):
        rejections.extend({"repeat": repeat, **row} for row in asyncio.run(rejection_checks()))
        directory = ROOT / "output/fault_experiments" / run_id / ("version_" + str(repeat))
        async def mismatch():
            await PhaseRunner(directory, "task", {"model": "v1"}).run({})
            try:
                await PhaseRunner(directory, "task", {"model": "v2"}).run({})
            except ValueError:
                return True
            return False
        rejections.append({"repeat": repeat, "scenario": "version_mismatch", "passed": asyncio.run(mismatch())})
    write_json(target / "results.json", {"recovery": rows, "rejections": rejections})
    summary = {"run_id": run_id, "status": "complete", "model_calls": 0,
               "recovery_trials": len(rows), "recovery_passed": sum(row["passed"] for row in rows),
               "rejection_trials": len(rejections), "rejections_passed": sum(row["passed"] for row in rejections),
               "prepare_calls": {mode: sum(row["phase_calls"].get("prepare", 0) for row in rows if row["mode"] == mode)
                                 for mode in ("durable", "stateless")}}
    write_json(target / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
