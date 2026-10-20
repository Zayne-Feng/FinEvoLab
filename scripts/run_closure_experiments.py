"""Run resumable paired financial and passage evaluations with fixed provenance."""

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.closure_evaluation import assess
from finevolab.config import ModelConfig
from finevolab.evaluation import analyze_baseline_task
from finevolab.experiments import manifest, sha256
from finevolab.grounded import analyze_grounded
from finevolab.runtime import write_json
from finevolab.tools import FinancialStore


def summarize(records):
    groups = {}
    for engine in sorted({row["engine"] for row in records}):
        subset = [row for row in records if row["engine"] == engine]
        groups[engine] = {}
        for group in ("all", "numeric", "explanation"):
            rows = [row for row in subset if group == "all" or
                    (row["category"] == "explanation") == (group == "explanation")]
            if not rows:
                continue
            times = sorted(row["elapsed_seconds"] for row in rows)
            known = [row["output"]["usage"] for row in rows if row.get("output", {}).get("usage")
                     and row["output"].get("usage_complete", True)]
            groups[engine][group] = {
                "attempts": len(rows), "successes": sum(row["score"] for row in rows),
                "success_rate": statistics.mean(row["score"] for row in rows),
                "mean_seconds": statistics.mean(times), "p50_seconds": statistics.median(times),
                "p95_seconds": times[min(len(times) - 1, int(len(times) * .95))],
                "known_usage": {key: sum(row.get(key, 0) for row in known) for key in
                                ("requests", "input_tokens", "output_tokens", "total_tokens")},
                "unknown_usage_tasks": len(rows) - len(known),
                "repairs": sum(row.get("output", {}).get("repair", {}).get("count", 0) for row in rows),
                "first_pass_successes": sum(row["score"] for row in rows
                    if not row.get("output", {}).get("repair", {}).get("attempted")),
                "error_types": dict(Counter(row.get("error_type") for row in rows if row.get("error_type"))),
            }
    return groups


async def run(args):
    data = ROOT / "data/evaluation/closure"
    frozen = json.loads((data / "manifest.json").read_text())
    if sha256(data / f"{args.split}.json") != frozen["split_sha256"][args.split]:
        raise ValueError("Frozen task bytes changed")
    tasks = json.loads((data / f"{args.split}.json").read_text())["tasks"]
    if args.limit:
        tasks = tasks[:args.limit]
    directory = ROOT / "project_notes/experiments" / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    settings = ModelConfig.load()
    candidate_path = args.candidate or ROOT / "prompts/benchmark.json"
    candidate = json.loads(candidate_path.read_text())
    core = {path.relative_to(ROOT).as_posix(): sha256(path) for path in (ROOT / "finevolab").rglob("*.py")}
    config = {"split": args.split, "repeats": args.repeats, "engines": args.engines,
              "task_ids": [case["id"] for case in tasks], "dataset_sha256": sha256(data / "manifest.json"),
              "candidate_sha256": sha256(candidate_path), "model": settings.model, "base_url": settings.base_url,
              "thinking": settings.extra_body, "core": core, "answer_cache": False,
              "text_metric": "mechanical_source_scope_separate_blind_semantics"}
    identity = directory / "manifest.json"
    if identity.exists():
        if json.loads(identity.read_text())["config"] != config:
            raise ValueError("The run configuration or implementation changed")
    else:
        write_json(identity, manifest(ROOT, args.run_id, config))
    record_path = directory / "tasks.jsonl"
    rows = [json.loads(line) for line in record_path.read_text().splitlines()] if record_path.exists() else []
    done = {(row["case_id"], row["repeat"], row["engine"]) for row in rows}
    store = FinancialStore(ROOT / "data/processed/reports.json")
    failures = 0
    for repeat in range(args.repeats):
        for index, case in enumerate(tasks):
            engines = args.engines if (repeat + index) % 2 == 0 else list(reversed(args.engines))
            for engine in engines:
                if (case["id"], repeat, engine) in done:
                    continue
                started, telemetry, events = time.monotonic(), {}, []
                write_json(directory / "active.json", {"case_id": case["id"], "repeat": repeat,
                           "engine": engine, "started_at": datetime.now(timezone.utc).isoformat()})
                row = {"case_id": case["id"], "repeat": repeat, "engine": engine, "category": case["category"]}
                try:
                    if engine == "baseline" and case["category"] != "explanation":
                        output = await asyncio.wait_for(analyze_baseline_task(settings, store, case["question"],
                            candidate, events, telemetry=telemetry), timeout=180)
                    else:
                        output = await asyncio.wait_for(analyze_grounded(settings, store, case["question"], candidate,
                            events, telemetry=telemetry, retrieval_variant="baseline" if engine == "baseline" else "improved"), timeout=180)
                    row.update(output=output, **assess(ROOT, case, output), execution_status="completed")
                    failures = 0
                except Exception as error:
                    status = getattr(error, "status_code", None)
                    infrastructure = status in {429, 500, 502, 503, 504} or type(error).__name__ in {
                        "APITimeoutError", "APIConnectionError", "TimeoutError"}
                    row.update(score=0.0, error_type=type(error).__name__, status_code=status,
                               execution_status="infrastructure_failure" if infrastructure else "execution_failure",
                               output={"events": events, "partial_telemetry": telemetry})
                    failures = failures + 1 if infrastructure else 0
                row["elapsed_seconds"] = round(time.monotonic() - started, 4)
                with record_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows.append(row)
                write_json(directory / "summary.json", {"status": "running", "groups": summarize(rows)})
                print(json.dumps({key: row[key] for key in ("case_id", "repeat", "engine", "score", "elapsed_seconds")}), flush=True)
                if failures >= 3:
                    raise ConnectionError("Provider failure threshold reached")
    changed = [name for name, digest in core.items() if sha256(ROOT / name) != digest]
    summary = {"run_id": args.run_id, "status": "complete" if not changed else "invalid_source_changed",
               "planned": len(tasks) * args.repeats * len(args.engines), "attempted": len(rows),
               "groups": summarize(rows), "changed_source_files": changed,
               "text_baseline": "same_explanation_adapter_old_retrieval_not_legacy_native_capability"}
    write_json(directory / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("development", "acceptance", "gepa_acceptance"), default="development")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--engines", nargs="+", choices=("baseline", "improved"), default=["baseline", "improved"])
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--limit", type=int)
    asyncio.run(run(parser.parse_args()))
