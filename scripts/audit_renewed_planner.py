"""Audit raw planning contracts and observed semantic overrides without model calls."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json


def main():
    plans_path = ROOT / "project_notes/optimization/renewed-gepa-v1/evaluations.jsonl"
    plans = [json.loads(line) for line in plans_path.read_text(encoding="utf-8").splitlines()]
    overrides_path = ROOT / "project_notes/experiments/renewed-evidence-test-v2/tasks.jsonl"
    rows = [json.loads(line) for line in overrides_path.read_text(encoding="utf-8").splitlines()]
    dataset_path = ROOT / "data/evaluation/renewed_v2/test.json"
    cases = {row["id"]: row for row in json.loads(dataset_path.read_text(encoding="utf-8"))["tasks"]}
    affected, raw_passes, final_passes = [], 0, 0
    for row in rows:
        case = cases[row["case_id"]]
        expected = "hybrid" if case["operation"].startswith("hybrid") else "explanation"
        def correct(plan):
            return plan["intent"] == expected and (expected != "hybrid" or plan["numeric_intent"] == case["operation"].split("_", 1)[1])
        original = next((event["original"] for event in row.get("output", {}).get("events", [])
                         if event.get("event") == "request_intent_resolved"), row["output"]["request"])
        raw_passes += correct(original)
        final_passes += correct(row["output"]["request"])
        for event in row.get("output", {}).get("events", []):
            if event.get("event") == "request_intent_resolved" and event["original"] != event["resolved"]:
                affected.append({"case_id": row["case_id"], "repeat": row["repeat"], "arm": row["arm"],
                    "original": event["original"], "overridden": event["resolved"], "mechanical_score": row["score"],
                    "raw_intent_correct": correct(event["original"]), "overridden_intent_correct": correct(event["resolved"])})
    write_json(ROOT / "project_notes/run_logs/renewed_planner_audit.json", {
        **manifest(ROOT, "renewed-planner-audit", {"plans_sha256": sha256(plans_path), "tasks_sha256": sha256(overrides_path)}),
        "raw_plans": len(plans), "raw_contract_passes": sum(all(row["output"]["raw_checks"].values()) for row in plans),
        "v2_executions": len(rows), "v2_raw_intent_passes": raw_passes, "v2_overridden_intent_passes": final_passes,
        "beneficial_overrides": sum(not row["raw_intent_correct"] and row["overridden_intent_correct"] for row in affected),
        "damaging_overrides": sum(row["raw_intent_correct"] and not row["overridden_intent_correct"] for row in affected),
        "overridden_executions": len(affected), "affected": affected, "model_requests": 0,
        "runtime_configuration": {"semantic_override_enabled": False, "company_alias_normalization_enabled": True}})
    print(json.dumps({"raw_plans": len(plans), "raw_contract_passes": sum(all(row["output"]["raw_checks"].values()) for row in plans),
                      "overridden_executions": len(affected)}))


if __name__ == "__main__":
    main()
