"""Execute frozen source-scoped tasks and paired release comparisons."""

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.config import ModelConfig
from finevolab.experiments import manifest, sha256
from finevolab.grounded import analyze_grounded
from finevolab.retrieval import load_index
from finevolab.runtime import RunBudget, write_json
from finevolab.scope_evaluation import assess_scope, task_store
from run_followup_uploads import UncachedExecutor


def append(path, row):
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def summarize(rows):
    result = {}
    for arm in sorted({r["arm"] for r in rows}):
        subset = [r for r in rows if r["arm"] == arm]
        categories = {}
        for category in sorted({r["category"] for r in subset}):
            values = [r for r in subset if r["category"] == category]
            categories[category] = {"attempts": len(values), "successes": sum(r["score"] for r in values)}
        result[arm] = {"attempts": len(subset), "successes": sum(r["score"] for r in subset),
            "success_rate": statistics.mean(r["score"] for r in subset), "categories": categories,
            "mean_seconds": statistics.mean(r["elapsed_seconds"] for r in subset),
            "requests": sum(r.get("usage", {}).get("requests", 0) for r in subset),
            "tokens": sum(r.get("usage", {}).get("total_tokens", 0) for r in subset),
            "unknown_usage_tasks": sum(not r.get("usage_complete", False) for r in subset),
            "repairs": sum(r.get("output", {}).get("repair", {}).get("count", 0) for r in subset),
            "failed_checks": dict(Counter(key for r in subset for key, value in r.get("checks", {}).items() if not value))}
    return result


def legacy_engine():
    source = subprocess.check_output(["git", "show", "d2fcca26:finevolab/grounded.py"], cwd=ROOT).decode("utf-8")
    module = types.ModuleType("finevolab.archived_grounded")
    module.__file__ = str(ROOT / "finevolab/grounded.py")
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module.analyze_grounded


async def run(args):
    dataset = ROOT / "data/evaluation/scope_v3" / f"{args.split}.json"
    frozen = json.loads((dataset.parent / "manifest.json").read_text(encoding="utf-8"))
    if sha256(dataset) != frozen["split_sha256"][args.split]:
        raise ValueError("The frozen task split changed")
    for name, digest in frozen["files"].items():
        if sha256(dataset.parent / name) != digest:
            raise ValueError("A frozen evaluation reference changed")
    dataset_document = json.loads(dataset.read_text(encoding="utf-8"))
    cases = dataset_document["tasks"]
    if args.limit:
        cases = cases[:args.limit]
    documents = json.loads((dataset.parent / "documents.json").read_text(encoding="utf-8"))
    settings = ModelConfig.load()
    candidate = json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8"))
    directory = ROOT / "project_notes/experiments" / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    config = {"split": args.split, "repeats": args.repeats, "paired": args.paired, "limit": args.limit,
        "model": settings.model, "base_url": settings.base_url, "thinking": settings.extra_body,
        "dataset": sha256(dataset), "temperature": 0, "max_tokens": 1600, "context_chars": 60000,
        "data_version": dataset_document["data_version"],
        "source_files": {name: sha256(ROOT / name) for name in (
            "data/processed/reports.json", "data/sources.json", "data/sec_sources.json", "data/filing_sources.json",
            "data/acceptance_sources.json", "data/indexes/structured.json", "data/indexes/structured.metadata.json",
            "data/indexes/contextual/structured.json", "data/indexes/contextual/structured.metadata.json", "configs/retrieval.json")},
        "budget": {"model": 6, "tool": 10, "tokens": 20000, "seconds": 180},
        "legacy_release": "d2fcca26", "legacy_adapter": "archived planner/generator with compatible current source models and scorer",
        "source_hashes": {p.relative_to(ROOT).as_posix(): sha256(p) for p in
            list((ROOT / "finevolab").glob("*.py")) + [Path(__file__), ROOT / "configs/company_aliases.json", ROOT / "prompts/benchmark.json"]},
        "comparison": "common-supported tasks only; new capabilities reported separately",
        "order": "alternate release order by task index plus repeat; no model-output cache"}
    config["deterministic_cache"] = "disabled to isolate evaluation executions"
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding="utf-8"))["config"] != config:
            raise ValueError("The experiment implementation or configuration changed; use a new run ID")
    else:
        write_json(manifest_path, manifest(ROOT, args.run_id, config))
    path = directory / "tasks.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    done = {(r["case_id"], r["repeat"], r["arm"]) for r in rows}
    stores = {}
    for case in cases:
        scope = tuple(case["upload_ids"])
        if scope not in stores:
            stores[scope] = task_store(ROOT, case, documents)
    for profile in (["legacy", "contextual"] if args.paired else ["contextual"]):
        load_index(ROOT, mode="hybrid", profile=profile).search("annual revenue", company="MSFT", year=2024, mode="hybrid")
    old = legacy_engine() if args.paired else None
    for repeat in range(args.repeats):
        for index, case in enumerate(cases):
            arms = ["current", "legacy"] if args.paired and case["common_support"] else ["current"]
            if (index + repeat) % 2:
                arms.reverse()
            for arm in arms:
                key = (case["id"], repeat, arm)
                if key in done:
                    continue
                telemetry, events = {}, []
                row = {"case_id": case["id"], "repeat": repeat, "arm": arm, "category": case["category"],
                    "common_support": case["common_support"], "fact_clusters": case["fact_clusters"]}
                started = time.monotonic()
                store = stores[tuple(case["upload_ids"])]
                try:
                    options = {"events": events, "budget": RunBudget(max_context_chars=60000), "telemetry": telemetry}
                    with patch("finevolab.grounded.ToolExecutor", UncachedExecutor), patch.dict(
                            old.__globals__ if old else {}, {"ToolExecutor": UncachedExecutor}):
                        output = await (analyze_grounded if arm == "current" else old)(settings, store, case["question"], candidate, **options)
                    row["output"] = output
                    row.update(output=output, execution_status="completed", usage=output["usage"],
                        usage_complete=output.get("usage_complete", True), **assess_scope(ROOT, store, case, output))
                except Exception as error:
                    row.update(score=0, checks={}, execution_status="failed", error_type=type(error).__name__,
                        error=str(error)[:1500], partial_telemetry=telemetry, events=events,
                        usage=telemetry.get("usage", {}), usage_complete=(
                            isinstance(error, (ValueError, KeyError, TypeError))
                            and bool(telemetry.get("usage", {}).get("requests"))
                            and not telemetry.get("unknown_usage", False)))
                row["elapsed_seconds"] = round(time.monotonic() - started, 4)
                append(path, row)
                rows.append(row)
                done.add(key)
                write_json(directory / "summary.json", summarize(rows))
                print(json.dumps({"completed": len(rows), "repeat": repeat, "arm": arm,
                    "category": case["category"], "score": row["score"], "error": row.get("error_type")}), flush=True)
    write_json(directory / "summary.json", {"status": "completed", "groups": summarize(rows),
        "planned": args.repeats * sum(2 if args.paired and c["common_support"] else 1 for c in cases),
        "mechanical_only_until_blind_review": True})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["development", "validation", "test"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--paired", action="store_true")
    parser.add_argument("--limit", type=int)
    asyncio.run(run(parser.parse_args()))
