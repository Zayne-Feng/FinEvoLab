"""Compare validation, bounded repair, and checkpointing on paired controlled faults."""

import argparse
import asyncio
from collections import Counter
import copy
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.closure_evaluation import assess
from finevolab.config import ModelConfig
from finevolab.experiments import manifest, sha256
from finevolab.grounded import RequestPlan, analyze_grounded, assemble_numeric
from finevolab.provider import async_client
from finevolab.runtime import PhaseRunner, write_json
from finevolab.tools import FinancialStore
from finevolab.validation import validate_answer


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def append(path, row):
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def validation_faults(store, output):
    answer = output["answer"]
    yield "none", copy.deepcopy(output), False
    observations = answer["metrics"] or (answer["calculations"][0]["inputs"] if answer["calculations"] else [])
    for kind in ("amount", "unit", "date", "citation") if observations else ():
        wrong = copy.deepcopy(output)
        metric = (wrong["answer"]["metrics"] or wrong["answer"]["calculations"][0]["inputs"])[0]
        if kind == "amount":
            metric["value"] = str(int(metric["value"]) + 1)
        elif kind == "unit":
            metric["unit"] = "USD_millions"
        elif kind == "date":
            metric["period_start"] = "2000-01-01"
        else:
            wrong["answer"]["evidence_ids"] = ["unknown-evidence"]
        yield kind, wrong, True
    if answer["calculations"]:
        wrong = copy.deepcopy(output)
        wrong["answer"]["calculations"][0]["formula"] = "current / previous"
        yield "formula", wrong, True
    plan = RequestPlan.model_validate(output["request"])
    if plan.intent in {"comparison", "alignment"} and answer["periods_aligned"] is False:
        wrong = copy.deepcopy(output)
        wrong["answer"]["status"] = "complete" if answer["status"] == "periods_not_aligned" else "periods_not_aligned"
        yield "request_status", wrong, True
    if observations and plan.intent in {"metric", "growth", "comparison", "alignment"}:
        other = "net_income" if plan.metrics == ["revenue"] else "revenue"
        replacement = assemble_numeric(plan.model_copy(update={"metrics": [other]}), store, [])
        if replacement["status"] == answer["status"] and replacement != answer:
            wrong = copy.deepcopy(output)
            wrong["answer"] = replacement
            yield "request_scope", wrong, True


def validation_suite(directory, store, source):
    rows = []
    for record in read_rows(source):
        if record["arm"] != "program_assembly":
            continue
        for kind, output, invalid in validation_faults(store, record["output"]):
            for arm in ("none", "source_only", "request_contract"):
                try:
                    if arm != "none":
                        value = {key: item for key, item in output.items() if arm != "source_only" or key != "request"}
                        validate_answer(store, value)
                    accepted, error = True, None
                except ValueError as exception:
                    accepted, error = False, str(exception)[:300]
                rows.append({"case_id": record["case_id"], "repeat": record["repeat"], "fault": kind,
                    "arm": arm, "invalid": invalid, "accepted": accepted, "error": error,
                    "output": output})
    write_json(directory / "validation.json", rows)
    groups = {}
    for arm in ("none", "source_only", "request_contract"):
        subset = [row for row in rows if row["arm"] == arm]
        bad = [row for row in subset if row["invalid"]]
        good = [row for row in subset if not row["invalid"]]
        groups[arm] = {"invalid": len(bad), "false_accepts": sum(row["accepted"] for row in bad),
            "valid": len(good), "false_rejects": sum(not row["accepted"] for row in good),
            "false_accept_types": dict(Counter(row["fault"] for row in bad if row["accepted"]))}
    write_json(directory / "validation_summary.json", groups)


async def repair_suite(directory, store, settings, cases, plans):
    path = directory / "repair.jsonl"
    rows = read_rows(path)
    done = {(row["case_id"], row["fault"], row["enabled"]) for row in rows}
    seeds = [row for row in plans if row["repeat"] == 0][:3]
    for seed in seeds:
        case = cases[seed["case_id"]]
        for fault in ("malformed_json", "extra_field", "invalid_enum"):
            value = copy.deepcopy(seed["plan"])
            if fault == "malformed_json":
                raw = "{"
            elif fault == "extra_field":
                raw = json.dumps({**value, "unexpected": True})
            else:
                raw = json.dumps({**value, "intent": "unknown_intent"})
            for enabled in (False, True):
                if (case["id"], fault, enabled) in done:
                    continue
                telemetry, real = {}, []
                async with async_client(settings) as client:
                    class InjectedClient:
                        def __init__(self):
                            self.chat = SimpleNamespace(completions=self)
                            self.first = True
                        async def __aenter__(self):
                            return self
                        async def __aexit__(self, *args):
                            pass
                        async def create(self, **kwargs):
                            if self.first:
                                self.first = False
                                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw))],
                                                       usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))
                            response = await client.chat.completions.create(**kwargs)
                            real.append({"content": response.choices[0].message.content,
                                         "usage": response.usage.model_dump() if response.usage else None})
                            return response
                    row = {"case_id": case["id"], "fault": fault, "enabled": enabled,
                           "injected_response": raw, "injected_response_is_replay": True}
                    try:
                        with patch("finevolab.grounded.async_client", return_value=InjectedClient()):
                            output = await analyze_grounded(settings, store, case["question"], {"system": ""},
                                allow_repair=enabled, telemetry=telemetry)
                        row.update(output=output, **assess(ROOT, case, output), recovered=True)
                    except Exception as error:
                        row.update(recovered=False, score=0, error_type=type(error).__name__)
                    row.update(real_requests=real, telemetry=telemetry)
                    append(path, row)
                    rows.append(row)
                    print(json.dumps({"suite": "repair", "completed": len(rows), "enabled": enabled,
                                      "recovered": row["recovered"]}), flush=True)
    write_json(directory / "repair_summary.json", {str(enabled): {
        "attempts": sum(row["enabled"] == enabled for row in rows),
        "successes": sum(row["score"] for row in rows if row["enabled"] == enabled),
        "real_api_requests": sum(len(row["real_requests"]) for row in rows if row["enabled"] == enabled),
        "real_tokens": sum((request["usage"] or {}).get("total_tokens", 0) for row in rows
                           if row["enabled"] == enabled for request in row["real_requests"])} for enabled in (False, True)})


async def checkpoint_suite(directory, store, settings, cases):
    path = directory / "checkpoint.jsonl"
    rows = read_rows(path)
    done = {(row["case_id"], row["fault"], row["checkpoint"]) for row in rows}
    candidate = json.loads((ROOT / "prompts/benchmark.json").read_text())
    for index, case in enumerate(list(cases.values())[:3]):
        for fault in ("none", "report_connection"):
            arms = [False, True] if index % 2 == 0 else [True, False]
            for enabled in arms:
                if (case["id"], fault, enabled) in done:
                    continue
                requests, analysis_count, report_count = [], 0, 0
                task_id = uuid4().hex
                async def analyze(state):
                    nonlocal analysis_count
                    analysis_count += 1
                    output = await analyze_grounded(settings, store, case["question"], candidate)
                    requests.append(output["usage"])
                    append(directory / "checkpoint_calls.jsonl", {"case_id": case["id"], "fault": fault,
                        "checkpoint": enabled, "analysis_execution": analysis_count,
                        "usage": output["usage"], "output": output})
                    return output
                async def validate(state):
                    return validate_answer(store, state["phases"]["analyze"]["result"])
                async def report(state):
                    nonlocal report_count
                    report_count += 1
                    if fault != "none" and report_count == 1:
                        raise ConnectionError("Injected report connection failure")
                    return {"status": "complete"}
                phases = {"analyze": analyze, "validate": validate, "report": report}
                versions = {"case_id": case["id"], "fault": fault, "data": store.version,
                            "experiment": directory.name}
                runner = PhaseRunner(ROOT / "output/tasks", task_id, versions)
                started = time.monotonic()
                try:
                    state = await runner.run(phases)
                except ConnectionError:
                    if not enabled:
                        runner = PhaseRunner(ROOT / "output/tasks", uuid4().hex, versions)
                    state = await runner.run(phases)
                result = state["phases"]["analyze"]["result"]
                row = {"case_id": case["id"], "fault": fault, "checkpoint": enabled,
                    "analysis_executions": analysis_count, "report_executions": report_count,
                    "usage": {key: sum(item[key] for item in requests) for key in requests[0]},
                    "elapsed_seconds": time.monotonic() - started, "state_path": str(runner.path.relative_to(ROOT)),
                    "initial_state_path": str((ROOT / "output/tasks" / task_id / "state.json").relative_to(ROOT)),
                    "output": result, **assess(ROOT, case, result)}
                append(path, row)
                rows.append(row)
                print(json.dumps({"suite": "checkpoint", "completed": len(rows), "checkpoint": enabled,
                                  "analyses": analysis_count}), flush=True)
    write_json(directory / "checkpoint_summary.json", {f"{fault}:{enabled}": {
        "attempts": sum(row["fault"] == fault and row["checkpoint"] == enabled for row in rows),
        "successes": sum(row["score"] for row in rows if row["fault"] == fault and row["checkpoint"] == enabled),
        "analysis_executions": sum(row["analysis_executions"] for row in rows if row["fault"] == fault and row["checkpoint"] == enabled),
        "real_requests": sum(row["usage"]["requests"] for row in rows if row["fault"] == fault and row["checkpoint"] == enabled),
        "real_tokens": sum(row["usage"]["total_tokens"] for row in rows if row["fault"] == fault and row["checkpoint"] == enabled)}
        for fault in ("none", "report_connection") for enabled in (False, True)})


async def run(args):
    directory = ROOT / "project_notes/experiments" / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    source = ROOT / "project_notes/experiments" / args.numeric_run
    settings = ModelConfig.load()
    store = FinancialStore(ROOT / "data/processed/reports.json")
    cases = {case["id"]: case for case in json.loads((ROOT / "data/evaluation/closure/acceptance.json").read_text())["tasks"]
             if case["category"] != "explanation"}
    config = {"numeric_run": args.numeric_run, "numeric_rows_sha256": sha256(source / "tasks.jsonl"),
        "model": settings.model, "thinking": settings.extra_body, "data_version": store.version,
        "faults": "fixed_paired_output_mutations_and_report_connection_and_three_schema_faults",
        "real_requests_separate_from_replayed_fault_response": True,
        "source_hashes": {path.relative_to(ROOT).as_posix(): sha256(path) for path in (ROOT / "finevolab").glob("*.py")}}
    target = directory / "manifest.json"
    if target.exists() and json.loads(target.read_text())["config"] != config:
        raise ValueError("Harness experiment inputs changed")
    if not target.exists():
        write_json(target, manifest(ROOT, args.run_id, config))
    validation_suite(directory, store, source / "tasks.jsonl")
    await repair_suite(directory, store, settings, cases, read_rows(source / "plans.jsonl"))
    await checkpoint_suite(directory, store, settings, cases)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--numeric-run", required=True)
    parser.add_argument("--run-id", required=True)
    asyncio.run(run(parser.parse_args()))
