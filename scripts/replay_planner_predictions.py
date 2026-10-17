"""Replay paired planner outputs through fixed source-bound financial execution."""

import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from filelock import FileLock
from finevolab.config import ModelConfig
from finevolab.evaluation import score_task
from finevolab.experiments import sha256
from finevolab.grounded import analyze_grounded, assemble_numeric, request_instruction
from finevolab.planner_benchmark import ARMS, experiment_manifest, load_bundle, parsed_plan, score_prediction
from finevolab.runtime import RunBudget, ToolExecutor, write_json
from finevolab.scope_evaluation import task_store
from finevolab.validation import validate_answer
from scripts.planner_benchmark import checked_run, implementation_hashes, read_rows, validate_arm_contracts


class UncachedExecutor(ToolExecutor):
    def __init__(self, budget, events, version, cache_root=None):
        super().__init__(budget, events, version, cache_root=None)


def replay_score(case, raw, output, store, aliases):
    checks = {"request": bool(score_prediction(case, raw, aliases)["score"]), "source_validation": False}
    validate_answer(store, output)
    checks["source_validation"] = True
    expected = case.expected
    expected_type = "hybrid" if expected.intent == "hybrid" else "explanation" if expected.intent == "explanation" else "numeric"
    checks["task_type"] = output["answer_type"] == expected_type
    if expected_type != "explanation":
        reference = assemble_numeric(expected, store, [])
        numerical = score_task({"category": "boundary", "reference_answer": reference}, output)
        checks.update({"financial_" + key: value for key, value in numerical["checks"].items()})
    if expected_type in {"hybrid", "explanation"}:
        narrative = output["narrative"] if expected_type == "hybrid" else output["answer"]
        checks["narrative_completion"] = narrative["status"] == "complete" and bool(narrative["claims"])
    return {"score": int(all(checks.values())), "checks": checks,
            "semantic_review": "pending" if expected_type != "numeric" else "not_applicable"}


async def run(args):
    cases, contract = load_bundle(args.bundle, args.split)
    selected = cases if args.with_narrative else [case for case in cases if case.expected.intent not in {"explanation", "hybrid"}]
    selection_path = getattr(args, "case_ids", None)
    if selection_path is not None:
        identifiers = json.loads(selection_path.read_text(encoding="utf-8"))
        if not isinstance(identifiers, list) or not identifiers or len(set(identifiers)) != len(identifiers) or not set(identifiers) <= {c.id for c in selected}:
            raise ValueError("Replay case identities must be a distinct eligible subset")
        selected = [case for case in selected if case.id in identifiers]
    runs = {}
    for directory in args.runs:
        config, rows = checked_run(directory, args.bundle, args.split)
        if config["arm"] in runs or config["implementation"] != implementation_hashes():
            raise ValueError("A duplicated arm or changed planner implementation cannot be replayed")
        runs[config["arm"]] = (config, {(r["case_id"], r["repeat"]): r for r in rows})
    validate_arm_contracts({arm: value[0] for arm, value in runs.items()}, args.split)
    documents = json.loads((args.bundle / "documents.json").read_text(encoding="utf-8"))
    for document in documents:
        path = (ROOT / document["path"]).resolve()
        if not path.is_relative_to(ROOT) or sha256(path) != document["sha256"]:
            raise ValueError("The selected source document changed")
    settings = ModelConfig.load(args.config) if args.with_narrative else ModelConfig("offline", "https://offline.invalid/", "unused")
    candidate = json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8"))
    expected_instruction = request_instruction(candidate)
    if expected_instruction != contract["instruction"]:
        raise ValueError("The fixed financial guidance changed")
    config = {"bundle_sha256": sha256(args.bundle / "manifest.json"), "split": args.split,
        "case_selection_sha256": sha256(selection_path) if selection_path else None,
        "replay_code_sha256": sha256(Path(__file__)),
        "phase": args.phase, "with_narrative": args.with_narrative, "excluded_cases": len(cases) - len(selected),
        "runs": {str(p): sha256(p / "predictions.jsonl") for p in args.runs},
        "narrative_model": {"model": settings.model, "endpoint": settings.base_url},
        "reference": "deterministic_execution_of_audited_request_labels", "semantic_review": "separate",
        "source_files": {str(p.relative_to(ROOT)): sha256(p) for p in [
            ROOT / name for name in ("data/processed/reports.json", "configs/retrieval.json", "configs/company_aliases.json",
                "data/filing_sources.json", "data/sources.json", "data/sec_sources.json", "data/acceptance_sources.json")]
            + list((ROOT / "data/indexes/contextual").glob("*")) if p.is_file()},
        "code": {str(p.relative_to(ROOT)): sha256(p) for p in (ROOT / "finevolab").glob("*.py")},
        "budget": {"model": 6, "tool": 10, "tokens": 20000, "seconds": 180, "context_chars": 60000}}
    args.output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(args.output / "execution.lock"), timeout=0):
        path = args.output / "manifest.json"
        if path.exists() and json.loads(path.read_text(encoding="utf-8"))["config"] != config:
            raise ValueError("The replay configuration changed")
        if not path.exists():
            write_json(path, experiment_manifest(ROOT, args.output.name, config))
        rows = read_rows(args.output / "tasks.jsonl")
        done = {(r["case_id"], r["repeat"], r["arm"]) for r in rows}
        stores = {}
        repeats = next(iter(runs.values()))[0]["repeats"]
        expected = {(case.id, repeat, arm) for case in selected for repeat in range(repeats) for arm in ARMS}
        if len(done) != len(rows) or not done <= expected:
            raise ValueError("Duplicate or unexpected replay outcomes")
        inflight = args.output / "inflight.json"
        if inflight.exists():
            interrupted = json.loads(inflight.read_text(encoding="utf-8"))
            if (interrupted["case_id"], interrupted["repeat"], interrupted["arm"]) not in done:
                raise ValueError("An interrupted replay has unknown usage; preserve it and use a new run ID")
            inflight.unlink()
        for repeat in range(repeats):
            for index, case in enumerate(selected):
                ids = tuple(d.id for d in case.input.selected_documents)
                if ids not in stores:
                    stores[ids] = task_store(ROOT, {"upload_ids": list(ids)}, documents)
                store = stores[ids]
                arms = list(ARMS)
                shift = (repeat + index) % len(arms)
                for arm in arms[shift:] + arms[:shift]:
                    if (case.id, repeat, arm) in done:
                        continue
                    prediction = runs[arm][1][(case.id, repeat)]
                    raw = prediction["attempts"][0 if args.phase == "first" else -1]["raw"]
                    started = time.monotonic()
                    row = {"case_id": case.id, "repeat": repeat, "arm": arm, "category": case.category}
                    telemetry = {}
                    write_json(inflight, {"case_id": case.id, "repeat": repeat, "arm": arm})
                    try:
                        plan = parsed_plan(raw)
                        if not args.with_narrative and plan.intent in {"explanation", "hybrid"}:
                            raise ValueError("A numeric-only replay cannot execute an unrequested narrative branch")
                        with patch("finevolab.grounded.ToolExecutor", UncachedExecutor):
                            output = await analyze_grounded(settings, store, case.input.question, candidate,
                                request_plan=plan, budget=RunBudget(max_context_chars=60000), allow_repair=True, telemetry=telemetry)
                        row.update(output=output, **replay_score(case, raw, output, store, contract["aliases"]))
                    except Exception as error:
                        row.update(score=0, checks={}, error_type=type(error).__name__)
                    row["elapsed_seconds"] = time.monotonic() - started
                    row["usage"] = row.get("output", {}).get("usage", telemetry.get("usage", {}))
                    row["usage_complete"] = row.get("output", {}).get("usage_complete", not telemetry.get("unknown_usage", False))
                    with (args.output / "tasks.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    rows.append(row)
                    inflight.unlink()
        groups = {arm: {"attempts": sum(r["arm"] == arm for r in rows),
            "mechanical_successes": sum(r["score"] for r in rows if r["arm"] == arm),
            "downstream_requests": sum(r.get("usage", {}).get("requests", 0) for r in rows if r["arm"] == arm),
            "known_downstream_tokens": sum(r.get("usage", {}).get("total_tokens", 0) for r in rows if r["arm"] == arm),
            "unknown_usage_tasks": sum(not r.get("usage_complete", False) for r in rows if r["arm"] == arm),
            "failures": dict(Counter(r.get("error_type", "failed_checks") for r in rows if r["arm"] == arm and not r["score"]))}
            for arm in ARMS}
        write_json(args.output / "summary.json", {"status": "completed", "groups": groups,
            "excluded_cases": len(cases) - len(selected), "semantic_review": "pending" if args.with_narrative else "not_applicable"})
        print(json.dumps({"status": "completed", "groups": groups}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--split", choices=["development", "validation", "test"], required=True)
    parser.add_argument("--runs", type=Path, nargs=3, required=True)
    parser.add_argument("--phase", choices=["first", "final"], default="first")
    parser.add_argument("--with-narrative", action="store_true")
    parser.add_argument("--case-ids", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
