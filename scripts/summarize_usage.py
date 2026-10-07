"""Aggregate recorded model usage and bound costs when cache usage is unknown."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def recorded_usage(row, provenance):
    usage = row.get("output", {}).get("usage")
    identifier = row.get("attempt_id", "")
    if usage or provenance["config"].get("variant") != "harness":
        return usage, None
    if len(identifier) != 32 or any(character not in "0123456789abcdef" for character in identifier):
        return None, None
    path = ROOT / "output/tasks" / identifier / "state.json"
    if not path.exists():
        return None, None
    state = json.loads(path.read_text(encoding="utf-8"))
    if state["task_id"] != identifier or any(state["versions"].get(key) != provenance["config"].get(key)
                                            for key in ("model", "base_url", "data_version")):
        raise ValueError("The usage checkpoint differs from its experiment")
    for name, digest in state["versions"]["sources"].items():
        if provenance["source_hashes"].get("finevolab/" + name) != digest:
            raise ValueError("The usage checkpoint has different source code")
    analysis = state["phases"].get("analyze", {})
    if analysis.get("status") != "complete":
        return None, None
    usage = analysis["result"].get("usage")
    return usage, {"attempt_id": identifier, "state_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                   "total_tokens": usage.get("total_tokens")} if usage else None


def summarize(directory, args):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest["config"]["model"] != args.model:
        raise ValueError("Run model differs from the pricing model")
    tasks = read_rows(directory / "tasks.jsonl") or read_rows(directory / "evaluations.jsonl")
    reflections = [row for row in read_rows(directory / "reflections.jsonl") if row["status"] != "started"]
    recovered = [recorded_usage(row, manifest) for row in tasks]
    task_usages = [usage for usage, recovery in recovered]
    usages = task_usages + [row.get("usage") for row in reflections]
    review_attempts = 0
    if manifest["config"].get("method") == "same_model_isolated_blind_contexts":
        for name in ("reviewer_A", "reviewer_B", "adjudications"):
            path = directory / (name + ".json")
            if path.exists():
                rows = json.loads(path.read_text(encoding="utf-8"))["ratings"]
                usages.extend(row.get("usage") for row in rows)
                review_attempts += len(rows)
        active_path = directory / "active_review.json"
        if active_path.exists() and json.loads(active_path.read_text())["status"] == "started":
            review_attempts += 1
    summary_path = directory / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    reflection_attempts = summary.get("reflection_calls", len(reflections))
    known_input = known_output = known_requests = 0
    for usage in usages:
        if not usage:
            continue
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
        if input_tokens is None or output_tokens is None or min(input_tokens, output_tokens) < 0:
            raise ValueError("Recorded token counts are incomplete or invalid")
        if usage.get("total_tokens") != input_tokens + output_tokens:
            raise ValueError("Recorded token counts do not sum to the total")
        known_input += input_tokens
        known_output += output_tokens
        known_requests += usage.get("requests", 1)
    known_usages = sum(bool(value) for value in usages)
    partial_events = [event for row, usage in zip(tasks, task_usages) if not usage
                      for event in row.get("partial_telemetry", {}).get("lifecycle_events", [])
                      if event.get("event") == "model_end"]
    divisor = Decimal(1_000_000)
    lower = (known_input * args.cached_input + known_output * args.output_rate) / divisor
    upper = (known_input * args.uncached_input + known_output * args.output_rate) / divisor
    return {"run_id": manifest["run_id"], "commit": manifest["commit"],
            "run_status": summary.get("status", "unavailable"), "planned_tasks": summary.get("planned"),
            "task_evaluations": len(tasks), "reflection_attempts": reflection_attempts,
            "review_attempts": review_attempts,
            "usage_recovered_from_checkpoints": [recovery for usage, recovery in recovered if recovery],
            "known_input_tokens": known_input, "known_output_tokens": known_output,
            "known_requests": known_requests,
            "partial_observed_tokens": sum(event["tokens"] for event in partial_events),
            "partial_observed_calls": len(partial_events),
            "unknown_usage_evaluations": len(tasks) + reflection_attempts + review_attempts - known_usages,
            "known_usage_cost_lower": str(lower), "known_usage_cost_upper": str(upper),
            "cost_method": "complete_usage_cache_bounds_with_partial_observations_separate", "invoice_cost": None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directories", type=Path, nargs="+")
    parser.add_argument("--model", required=True)
    parser.add_argument("--cached-input", type=Decimal, required=True)
    parser.add_argument("--uncached-input", type=Decimal, required=True)
    parser.add_argument("--output-rate", type=Decimal, required=True)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--pricing-source", required=True)
    parser.add_argument("--pricing-period", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 <= args.cached_input <= args.uncached_input or args.output_rate < 0:
        parser.error("Token rates must be nonnegative and ordered")
    rows = [summarize(directory, args) for directory in args.directories]
    if len({row["run_id"] for row in rows}) != len(rows):
        parser.error("Duplicate run IDs are not allowed")
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "model": args.model,
              "currency": args.currency, "pricing_source": args.pricing_source,
              "pricing_period": args.pricing_period,
              "rates_per_million": {"cached_input": str(args.cached_input),
                  "uncached_input": str(args.uncached_input), "output": str(args.output_rate)},
              "runs": rows,
              "known_usage_cost_lower": str(sum(Decimal(row["known_usage_cost_lower"]) for row in rows)),
              "known_usage_cost_upper": str(sum(Decimal(row["known_usage_cost_upper"]) for row in rows)),
              "unknown_usage_evaluations": sum(row["unknown_usage_evaluations"] for row in rows),
              "partial_observed_tokens": sum(row["partial_observed_tokens"] for row in rows),
              "invoice_cost": None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "runs"}))


if __name__ == "__main__":
    main()
