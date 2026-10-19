"""Run repeated financial task evaluations with durable per-task records."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.config import ModelConfig
from finevolab.evaluation import analyze_task, score_task
from finevolab.experiments import aggregate, manifest, sha256
from finevolab.runtime import write_json
from finevolab.tools import FinancialStore
from finevolab.workflow import run_question


async def run(args):
    settings = ModelConfig.load(args.config)
    store = FinancialStore(ROOT / "data/processed/reports.json")
    frozen = json.loads((args.dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    path = args.dataset_dir / (args.split + ".json")
    if not frozen.get("status", "").startswith("frozen_structured_") or frozen["split_sha256"][args.split] != sha256(path):
        raise ValueError("A frozen unmodified evaluation split is required")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["data_version"] != store.version:
        raise ValueError("Task data version differs from the source snapshot")
    cases = payload["tasks"][:args.limit] if args.limit else payload["tasks"]
    candidate = json.loads(args.prompt.read_text(encoding="utf-8"))
    run_id = args.run_id or uuid4().hex[:12] + "-" + args.variant + "-" + args.split
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", run_id):
        raise ValueError("Invalid experiment run ID")
    directory = ROOT / "project_notes/experiments" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    config = {"split": args.split, "repeats": args.repeats, "variant": args.variant,
              "task_ids": [case["id"] for case in cases], "prompt_sha256": sha256(args.prompt),
              "model": settings.model, "base_url": settings.base_url, "data_version": store.version,
              "dataset_manifest_sha256": sha256(args.dataset_dir / "manifest.json"),
              "timeout_seconds": 180, "infrastructure_failure_threshold": 3,
              "answer_cache": False, "prose_quality_scored": False}
    provenance = manifest(ROOT, run_id, config)
    record_path = directory / "tasks.jsonl"
    records = [json.loads(line) for line in record_path.read_text(encoding="utf-8").splitlines()] if record_path.exists() else []
    if (directory / "manifest.json").exists():
        saved = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if saved["config"] != config or saved["source_hashes"] != provenance["source_hashes"]:
            raise ValueError("Resume configuration or source versions differ")
    else:
        write_json(directory / "manifest.json", provenance)
    complete = {(record["repeat"], record["case_id"]) for record in records}
    infrastructure_failures = 0
    planned = args.repeats * len(cases)
    try:
        for repeat in range(1, args.repeats + 1):
            for case in cases:
                if (repeat, case["id"]) in complete:
                    continue
                started, events, telemetry = time.monotonic(), [], {}
                record = {"attempt_id": uuid4().hex, "repeat": repeat, "case_id": case["id"], "category": case["category"], "score": 0.0}
                write_json(directory / "active_task.json", {**record, "status": "started",
                           "started_at": datetime.now(timezone.utc).isoformat(), "usage": None})
                try:
                    if args.variant == "harness":
                        state = await run_question(record["attempt_id"], case["question"], settings, candidate,
                                                   events=events, telemetry=telemetry, render=False, timeout_seconds=180)
                        output = state["phases"]["analyze"]["result"]
                        record["workflow_task_id"] = state["task_id"]
                    else:
                        output = await asyncio.wait_for(analyze_task(settings, store, case["question"], candidate, events,
                                                                     variant=args.variant, telemetry=telemetry), timeout=180)
                    record.update(output=output, execution_status="completed", **score_task(case, output))
                    infrastructure_failures = 0
                except Exception as error:
                    error_type = type(error).__name__
                    status = getattr(error, "status_code", None)
                    infrastructure = status in {429, 500, 502, 503, 504} or error_type in {
                        "APITimeoutError", "APIConnectionError", "TimeoutError"}
                    record.update(execution_status="infrastructure_failure" if infrastructure else "execution_failure",
                                  error_type=error_type, status_code=status, events=events,
                                  partial_telemetry=telemetry)
                    provider_code = str(getattr(error, "code", ""))
                    if provider_code.isdecimal():
                        record["provider_error_code"] = provider_code
                    infrastructure_failures = infrastructure_failures + 1 if infrastructure else 0
                record["elapsed_seconds"] = round(time.monotonic() - started, 3)
                with record_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                records.append(record)
                write_json(directory / "active_task.json", {"attempt_id": record["attempt_id"], "status": "recorded"})
                summary = aggregate(records, planned)
                summary.update(run_id=run_id, status="running", variant=args.variant, split=args.split)
                write_json(directory / "summary.json", summary)
                print(json.dumps({"run_id": run_id, "repeat": repeat, "case": case["id"],
                                  "score": record["score"], "status": record["execution_status"]}), flush=True)
                if infrastructure_failures >= 3:
                    raise ConnectionError("Consecutive provider failures reached the experiment threshold")
    except (Exception, KeyboardInterrupt) as error:
        summary = aggregate(records, planned)
        summary.update(run_id=run_id, status="interrupted", stop_reason=type(error).__name__)
        write_json(directory / "summary.json", summary)
        return False
    summary = aggregate(records, planned)
    summary.update(run_id=run_id, status="complete", variant=args.variant, split=args.split)
    summary["repeats"] = {str(index): aggregate([r for r in records if r["repeat"] == index], len(cases))
                          for index in range(1, args.repeats + 1)}
    write_json(directory / "summary.json", summary)
    print(json.dumps(summary), flush=True)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("development", "validation", "test"), default="development")
    parser.add_argument("--variant", choices=("tools", "context", "rag", "unprotected", "harness"), default="tools")
    parser.add_argument("--repeats", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--prompt", type=Path, default=ROOT / "prompts/benchmark.json")
    parser.add_argument("--run-id")
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "data/evaluation")
    parser.add_argument("--config", type=Path, default=ROOT / "finrobot_equity/core/config/config.ini")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("The task limit must be positive")
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == "__main__":
    main()
