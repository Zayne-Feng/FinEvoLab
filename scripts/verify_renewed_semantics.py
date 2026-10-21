"""Replay recorded planner inputs against two normalization implementations."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.grounded import RequestPlan
from finevolab.runtime import write_json
from finevolab.upstream_baseline import archived_module


def main():
    old, digest = archived_module(ROOT, "_renewed_normalizer", "finevolab/grounded.py", "5eb2a196")
    records, sources = [], {}
    for run, split in (("development", "development"), ("acceptance", "acceptance")):
        tasks = ROOT / f"data/evaluation/renewed_v1/{split}.json"
        cases = {row["id"]: row for row in json.loads(tasks.read_text(encoding="utf-8"))["tasks"]}
        path = ROOT / f"project_notes/experiments/renewed{run}-v1/tasks.jsonl"
        sources[path.relative_to(ROOT).as_posix()] = sha256(path)
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["arm"] == "before":
                continue
            output = row["output"]
            original = next((event["original"] for event in output.get("events", [])
                             if event.get("event") == "request_intent_resolved"), output["request"])
            question = cases[row["case_id"]]["question"]
            previous = old.resolve_request_intent(question, old.RequestPlan.model_validate(original)).model_dump()
            current = RequestPlan.model_validate(original).model_dump()
            records.append({"case_id": row["case_id"], "repeat": row["repeat"], "arm": row["arm"],
                            "equal": previous == current})
    if not all(row["equal"] for row in records):
        raise ValueError("The boundary repair changes a recorded experimental plan")
    write_json(ROOT / "project_notes/run_logs/renewed_planner_preservation_replay.json", {
        **manifest(ROOT, "renewed-planner-preservation-replay", {"reference_commit": "5eb2a196", "reference_sha256": digest}),
        "status": "passed", "recorded_plans": len(records), "unchanged": len(records),
        "source_sha256": sources, "records": records, "model_requests": 0,
        "evaluation_stage": "recorded_request_plan_replay"})
    print(json.dumps({"status": "passed", "recorded_plans": len(records)}))


if __name__ == "__main__":
    main()
