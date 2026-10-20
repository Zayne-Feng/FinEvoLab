"""Aggregate paired closure outcomes and retain task-level uncertainty."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.runtime import write_json
from scripts.estimate_paired_uncertainty import estimate


def load_rows(directory):
    return [json.loads(line) for line in (directory / "tasks.jsonl").read_text().splitlines()]


def compare(left, right, left_name, right_name):
    left_map = {(row["case_id"], row["repeat"]): row for row in left if row["category"] != "explanation"}
    right_map = {(row["case_id"], row["repeat"]): row for row in right if row["category"] != "explanation"}
    keys = sorted(left_map.keys() & right_map.keys())
    pairs = [{"case_id": key[0], "repeat": key[1], "left": left_map[key]["score"],
              "right": right_map[key]["score"], "difference": right_map[key]["score"] - left_map[key]["score"]}
             for key in keys]
    report = {"left_run": left_name, "right_run": right_name,
              "complete_pairing": left_map.keys() == right_map.keys(),
              "observed_pairs": len(keys), "expected_pairs": len(left_map), "pairs": pairs}
    report["uncertainty"] = estimate(report)
    report["wins_losses_ties"] = {"wins": sum(pair["difference"] > 0 for pair in pairs),
                                  "losses": sum(pair["difference"] < 0 for pair in pairs),
                                  "ties": sum(pair["difference"] == 0 for pair in pairs)}
    return report


def usage_ledger():
    entries = []
    def add(scope, usage, partial=False):
        if not usage:
            return
        entries.append({"scope": scope, "partial": partial,
            "requests": usage.get("requests", 1),
            "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
            "output_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0)),
            "total_tokens": usage["total_tokens"]})
    incomplete = []
    for directory in sorted((ROOT / "project_notes/experiments").glob("closure-*")):
        if not (directory / "tasks.jsonl").exists():
            continue
        for row in load_rows(directory):
            output = row["output"]
            if output.get("usage") and output.get("usage_complete", True):
                add(directory.name, output["usage"])
            else:
                add(directory.name, output.get("partial_telemetry", {}).get("usage"), partial=True)
                incomplete.append({"run_id": directory.name, "case_id": row["case_id"],
                                   "error_type": row.get("error_type")})
    for directory in sorted((ROOT / "project_notes/optimization").glob("closure-gepa*")):
        for line in (directory / "evaluations.jsonl").read_text().splitlines():
            add(directory.name, json.loads(line)["output"].get("usage"))
        for line in (directory / "reflections.jsonl").read_text().splitlines():
            add(directory.name + ":reflection", json.loads(line).get("usage"))
    review = ROOT / "project_notes/evaluations/closure-blind-review/requests.jsonl"
    for line in review.read_text().splitlines():
        add("claim_blind_review", json.loads(line).get("usage"))
    delivery = json.loads((ROOT / "project_notes/run_logs/closure_delivery.json").read_text())
    for result in delivery["scenarios"].values():
        add("live_delivery", result["usage"])
    add("provider_error_probe", {"requests": 1, "input_tokens": 820, "output_tokens": 39, "total_tokens": 859}, True)
    incomplete.append({"run_id": "provider_error_probe", "error_type": "BadRequestError"})
    return {"scope": "closure_development_acceptance_two_gepa_runs_holdout_review_and_delivery",
            "known_usage": {key: sum(row[key] for row in entries) for key in
                            ("requests", "input_tokens", "output_tokens", "total_tokens")},
            "partial_observed_tokens": sum(row["total_tokens"] for row in entries if row["partial"]),
            "incomplete_tasks": incomplete, "invoice_cost": None,
            "pricing_applied": False, "entries": entries}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--gepa-initial", type=Path)
    parser.add_argument("--gepa-selected", type=Path)
    args = parser.parse_args()
    rows = load_rows(args.acceptance)
    output = {"acceptance_manifest_sha256": sha256(args.acceptance / "manifest.json"),
              "engineering": compare([row for row in rows if row["engine"] == "baseline"],
                                     [row for row in rows if row["engine"] == "improved"], "baseline", "improved")}
    if args.gepa_initial and args.gepa_selected:
        output["gepa"] = compare(load_rows(args.gepa_initial), load_rows(args.gepa_selected), "gepa_initial", "gepa_selected")
    historical = ROOT / "project_notes/experiments/deepseek-v2-tools-validation/tasks.jsonl"
    if historical.exists():
        prior = [json.loads(line) for line in historical.read_text().splitlines()]
        output["historical_failure_checks"] = dict(Counter(name for row in prior if not row.get("score")
            for name, passed in row.get("checks", {}).items() if not passed))
    write_json(ROOT / "project_notes/evaluations/closure_comparisons.json", output)
    write_json(ROOT / "project_notes/evaluations/closure_usage.json", usage_ledger())
    print(json.dumps({key: value["uncertainty"] for key, value in output.items() if isinstance(value, dict) and "uncertainty" in value}))


if __name__ == "__main__":
    main()
