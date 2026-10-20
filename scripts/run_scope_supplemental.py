"""Evaluate frozen natural-language questions against a selected held-out filing."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.config import ModelConfig
from finevolab.experiments import manifest, sha256
from finevolab.grounded import analyze_grounded
from finevolab.runtime import RunBudget, write_json
from finevolab.scope_evaluation import assess_scope, task_store
from run_scope_experiments import append, summarize


async def run(args):
    dataset = ROOT / "data/evaluation/scope_supplemental/test.json"
    frozen = json.loads((dataset.parent / "manifest.json").read_text(encoding="utf-8"))
    if sha256(dataset) != frozen["dataset_sha256"]:
        raise ValueError("The supplemental task set changed")
    cases = json.loads(dataset.read_text(encoding="utf-8"))["tasks"]
    documents = json.loads((ROOT / "data/evaluation/scope_v3/documents.json").read_text(encoding="utf-8"))
    settings = ModelConfig.load()
    candidate = json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8"))
    directory = ROOT / "project_notes/experiments" / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    config = {"split": "test", "repeats": args.repeats, "paired": False, "limit": None,
        "dataset": sha256(dataset), "dataset_path": str(dataset.relative_to(ROOT)),
        "model": settings.model, "base_url": settings.base_url, "thinking": settings.extra_body,
        "temperature": 0, "max_tokens": 1600, "budget": {"model": 6, "tool": 10, "tokens": 20000, "seconds": 180, "context_chars": 60000},
        "source_hashes": {p.relative_to(ROOT).as_posix(): sha256(p) for p in list((ROOT / "finevolab").glob("*.py")) +
            [Path(__file__), ROOT / "scripts/run_scope_experiments.py", ROOT / "configs/company_aliases.json", ROOT / "prompts/benchmark.json"]},
        "source_files": {name: sha256(ROOT / name) for name in ("data/processed/reports.json", "data/acceptance_sources.json",
            "data/evaluation/scope_v3/documents.json", "data/evaluation/scope_v3/financial_reference_audit.json")},
        "scope": "selected NVDA FY2023 filing; natural English and Chinese inputs; English narratives explicitly requested"}
    path = directory / "manifest.json"
    if path.exists() and json.loads(path.read_text(encoding="utf-8"))["config"] != config:
        raise ValueError("Supplemental execution configuration changed")
    if not path.exists():
        write_json(path, manifest(ROOT, args.run_id, config))
    path = directory / "tasks.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    done = {(r["case_id"], r["repeat"]) for r in rows}
    store = task_store(ROOT, cases[0], documents)
    for repeat in range(args.repeats):
        for case in cases:
            if (case["id"], repeat) in done:
                continue
            telemetry, events = {}, []
            row = {"case_id": case["id"], "repeat": repeat, "arm": "current", "category": case["category"],
                "common_support": False, "fact_clusters": case["fact_clusters"], "language": case["language"]}
            started = time.monotonic()
            try:
                output = await analyze_grounded(settings, store, case["question"], candidate, events=events,
                    budget=RunBudget(max_context_chars=60000), telemetry=telemetry)
                row.update(output=output, execution_status="completed", usage=output["usage"],
                    usage_complete=output["usage_complete"], **assess_scope(ROOT, store, case, output))
            except Exception as error:
                row.update(score=0, checks={}, execution_status="failed", error_type=type(error).__name__,
                    error=str(error)[:1500], events=events, partial_telemetry=telemetry, usage=telemetry.get("usage", {}),
                    usage_complete=isinstance(error, (ValueError, KeyError, TypeError)) and
                        bool(telemetry.get("usage", {}).get("requests")) and not telemetry.get("unknown_usage", False))
            row["elapsed_seconds"] = time.monotonic() - started
            append(path, row)
            rows.append(row)
            done.add((case["id"], repeat))
            write_json(directory / "summary.json", summarize(rows))
            print(json.dumps({"completed": len(rows), "planned": len(cases) * args.repeats,
                "repeat": repeat, "language": case["language"], "score": row["score"]}), flush=True)
    write_json(directory / "summary.json", {"status": "completed", "groups": summarize(rows),
        "planned": len(cases) * args.repeats, "mechanical_only_until_blind_review": True})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    asyncio.run(run(parser.parse_args()))
