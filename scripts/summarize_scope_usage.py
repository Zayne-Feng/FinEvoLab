"""Count recorded provider responses without duplicating checkpoints or reviews."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.runtime import write_json


def main():
    groups, files = {}, {}
    def add(name, usages, unknown=0):
        groups[name] = {"recorded_responses": sum(u.get("requests", 1) for u in usages if u),
            "known_tokens": sum(u.get("total_tokens", 0) for u in usages if u),
            "unknown_usage_records": unknown + sum(u is None for u in usages)}
    for path in sorted((ROOT / "project_notes/experiments").glob("scope-*/tasks.jsonl")):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        incomplete, recovered = 0, 0
        for row in rows:
            if row.get("usage_complete", False) or not row.get("usage"):
                continue
            telemetry = row.get("partial_telemetry", {})
            if (telemetry.get("model_attempts") == row["usage"].get("requests", 0)
                    and row["usage"].get("requests", 0) > 0 and not telemetry.get("unknown_usage", False)):
                recovered += 1
            else:
                incomplete += 1
        add(path.parent.name, [r.get("usage") for r in rows], incomplete)
        groups[path.parent.name]["usage_metadata_recovered"] = recovered
        files[path.relative_to(ROOT).as_posix()] = sha256(path)
    for path in sorted((ROOT / "project_notes/evaluations").glob("scope-*/requests.jsonl")):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        add(path.parent.name, [r.get("usage") for r in rows])
        files[path.relative_to(ROOT).as_posix()] = sha256(path)
    for path in sorted((ROOT / "project_notes/experiments").glob("scope-*/repair.jsonl")):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        add(path.parent.name, [request.get("usage") for row in rows for request in row["actual_requests"]])
        groups[path.parent.name]["injected_responses_excluded"] = sum(row["injected_response_count"] for row in rows)
        files[path.relative_to(ROOT).as_posix()] = sha256(path)
    path = ROOT / "project_notes/run_logs/scope_sdk_smoke.json"
    smoke = json.loads(path.read_text(encoding="utf-8"))
    add("scope_sdk_smoke", smoke["minimal_usage"] + [smoke["output"]["usage"]])
    files[path.relative_to(ROOT).as_posix()] = sha256(path)
    path = ROOT / "project_notes/run_logs/scope_delivery_v1.json"
    delivery = json.loads(path.read_text(encoding="utf-8"))
    add("scope_delivery", [delivery["web"]["usage"]] + [usage for row in delivery["recovery"] for usage in row["usage"]])
    files[path.relative_to(ROOT).as_posix()] = sha256(path)
    error_path = ROOT / "project_notes/run_logs/scope_annotation_initial_error.json"
    if error_path.exists():
        add("annotation_initial_error", [None])
        files[error_path.relative_to(ROOT).as_posix()] = sha256(error_path)
    result = {"status": "completed", "groups": groups,
        "recorded_responses": sum(g["recorded_responses"] for g in groups.values()),
        "known_tokens": sum(g["known_tokens"] for g in groups.values()),
        "unknown_usage_records": sum(g["unknown_usage_records"] for g in groups.values()),
        "input_sha256": files, "currency_cost": None,
        "accounting": "successful provider returns with observed usage; failed or retried HTTP attempts may have unknown usage; not a provider invoice",
        "reused_review_inputs": "counted once in request ledgers", "resumed_completed_model_phases": "counted once"}
    write_json(ROOT / "project_notes/run_logs/scope_usage.json", result)
    print(json.dumps({k: result[k] for k in ("status", "recorded_responses", "known_tokens", "unknown_usage_records")}))


if __name__ == "__main__":
    main()
