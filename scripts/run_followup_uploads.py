"""Evaluate source-isolated uploads against archived and current runtimes."""

import argparse
import asyncio
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.config import ModelConfig
from finevolab.experiments import manifest, sha256
from finevolab.grounded import analyze_grounded
from finevolab.runtime import RunBudget, ToolExecutor, write_json
from finevolab.scope_evaluation import assess_scope, task_store
from finevolab.upstream_baseline import archived_module

REFERENCE = "944a59ff"


class UncachedExecutor(ToolExecutor):
    def __init__(self, budget, events, version, cache_root=None):
        super().__init__(budget, events, version, cache_root=None)


def selected_reference(case):
    scoped = deepcopy(case)
    reference = scoped.get("reference_answer")
    if reference is not None and scoped.get("upload_ids"):
        for metric in reference["metrics"] + [m for calc in reference["calculations"] for m in calc["inputs"]]:
            metric["report_year"] = scoped["report_year"]
    return scoped


async def run(args):
    base = ROOT / args.dataset
    frozen = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in frozen["files"].items():
        if sha256(base / name) != digest:
            raise ValueError("The frozen dataset changed")
    for name, digest in frozen.get("sources", {}).items():
        if sha256(ROOT / name) != digest:
            raise ValueError("A frozen source changed")
    dataset = base / f"{args.split}.json"
    expected_split = frozen.get("split_sha256", {}).get(args.split)
    if expected_split and sha256(dataset) != expected_split:
        raise ValueError("The frozen task split changed")
    cases = json.loads(dataset.read_text(encoding="utf-8"))["tasks"]
    if args.case_id:
        cases = [case for case in cases if case["id"] in args.case_id]
        if {case["id"] for case in cases} != set(args.case_id):
            raise ValueError("An unknown case identifier was selected")
    documents = json.loads((base / "documents.json").read_text(encoding="utf-8"))
    if args.limit:
        cases = cases[:args.limit]
    old, old_hash = archived_module(ROOT, "_followup_grounded", "finevolab/grounded.py", args.reference_commit)
    retrieval_commit = args.retrieval_commit or args.reference_commit
    retrieval, retrieval_hash = archived_module(ROOT, "_followup_retrieval", "finevolab/retrieval.py", retrieval_commit)
    settings = ModelConfig.load()
    candidate = json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8"))
    optimized = json.loads((ROOT / args.candidate).read_text(encoding="utf-8")) if args.candidate else candidate
    arms = args.arms or (["before", "after", "after_old_retrieval"] if args.paired else ["after"])
    directory = ROOT / "project_notes/experiments" / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    configuration = {"split": args.split, "dataset_sha256": sha256(dataset), "repeats": args.repeats,
        "limit": args.limit, "case_ids": args.case_id, "arms": arms, "reference_commit": args.reference_commit,
        "retrieval_reference_commit": retrieval_commit,
        "archived_hashes": {"grounded": old_hash, "retrieval": retrieval_hash},
        "model": settings.model, "base_url": settings.base_url, "thinking": settings.extra_body,
        "temperature": 0, "max_tokens": 1600, "budget": {"model": 6, "tool": 10, "tokens": 20000,
        "seconds": 180, "context_chars": 60000}, "candidate_sha256": sha256(ROOT / "prompts/benchmark.json"),
        "source_hashes": {p.relative_to(ROOT).as_posix(): sha256(p) for p in
            list((ROOT / "finevolab").glob("*.py")) + [Path(__file__)]},
        "scorer": "archived narrative validator for every arm; shared financial scorer; blind semantic review pending",
        "order": "rotate arms by task and repeat; no model response cache", "fmp_requests": 0}
    configuration.update(dataset_path=str(dataset.relative_to(ROOT)),
        financial_sources={name: sha256(ROOT / name) for name in (
            "data/processed/reports.json", "data/sources.json", "data/sec_sources.json", "data/filing_sources.json",
            "data/acceptance_sources.json", "data/indexes/contextual/structured.json",
            "data/indexes/contextual/structured.metadata.json", "configs/retrieval.json")},
        optimized_candidate_sha256=sha256(ROOT / args.candidate) if args.candidate else None,
        deterministic_cache="disabled in all arms to isolate archived implementations",
        reference_adapter="selected report_year only; monetary values and fiscal dates unchanged")
    path = directory / "manifest.json"
    if path.exists() and json.loads(path.read_text(encoding="utf-8"))["config"] != configuration:
        raise ValueError("The experiment changed; use a new run ID")
    if not path.exists():
        write_json(path, manifest(ROOT, args.run_id, configuration))
    path = directory / "tasks.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    done = {(r["case_id"], r["repeat"], r["arm"]) for r in rows}
    stores = {}
    for case in cases:
        key = tuple(case["upload_ids"])
        if key not in stores:
            stores[key] = task_store(ROOT, case, documents)
    if any(not case["upload_ids"] for case in cases):
        from finevolab.retrieval import load_index
        for loader in (load_index, retrieval.load_index):
            loader(ROOT, mode="hybrid", profile="contextual").search(
                "annual revenue", company="MSFT", year=2024, mode="hybrid")
    for repeat in range(args.repeats):
        for index, case in enumerate(cases):
            offset = (index + repeat) % len(arms)
            for arm in arms[offset:] + arms[:offset]:
                key = (case["id"], repeat, arm)
                if key in done:
                    continue
                store = stores[tuple(case["upload_ids"])]
                telemetry, events = {}, []
                row = {"case_id": case["id"], "repeat": repeat, "arm": arm, "category": case["category"],
                    "fact_clusters": case["fact_clusters"], "common_support": case.get("common_support", True)}
                started = time.monotonic()
                try:
                    with ExitStack() as stack:
                        stack.enter_context(patch("finevolab.grounded.ToolExecutor", UncachedExecutor))
                        stack.enter_context(patch.object(old, "ToolExecutor", UncachedExecutor))
                        if arm in {"before", "after_old_retrieval"}:
                            stack.enter_context(patch("finevolab.retrieval.explanation_passages", retrieval.explanation_passages))
                        output = await (old.analyze_grounded if arm == "before" else analyze_grounded)(
                            settings, store, case["question"], optimized if arm == "optimized" else candidate, events=events, telemetry=telemetry,
                            budget=RunBudget(max_context_chars=60000))
                    with patch("finevolab.grounded.validate_explanation", old.validate_explanation):
                        row["output"] = output
                        assessment = assess_scope(ROOT, store, selected_reference(case), output)
                    row.update(output=output, usage=output["usage"], usage_complete=output.get("usage_complete", True),
                        execution_status="completed", **assessment)
                except Exception as error:
                    row.update(score=0, checks={}, execution_status="failed", error_type=type(error).__name__,
                        error=str(error)[:1500], partial_telemetry=telemetry, events=events,
                        usage=telemetry.get("usage", {}), usage_complete=bool(telemetry.get("usage", {}).get("requests"))
                        and not telemetry.get("unknown_usage", False))
                row["elapsed_seconds"] = round(time.monotonic() - started, 4)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows.append(row)
                done.add(key)
                groups = {name: {"attempted": sum(r["arm"] == name for r in rows),
                    "successes": sum(r["score"] for r in rows if r["arm"] == name)} for name in arms}
                write_json(directory / "summary.json", {"groups": groups, "planned": len(cases) * args.repeats * len(arms),
                    "completed": len(rows), "mechanical_only_until_blind_review": True})
                print(json.dumps({"completed": len(rows), "case": case["id"], "arm": arm, "score": row["score"],
                    "error": row.get("error_type")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["development", "validation", "acceptance", "test"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--paired", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dataset", default="data/evaluation/followup_v1")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--reference-commit", default=REFERENCE)
    parser.add_argument("--arms", nargs="+", choices=["before", "after", "after_old_retrieval", "optimized"])
    parser.add_argument("--candidate")
    parser.add_argument("--retrieval-commit")
    asyncio.run(run(parser.parse_args()))
