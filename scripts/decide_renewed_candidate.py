"""Apply fixed quality and token gates to a frozen prompt candidate."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def gate(run_id, strict_pairs=False):
    directory = ROOT / "project_notes/experiments" / run_id
    result = read(directory / "assessment.json")
    if result["status"] != "completed" or result["tasks_sha256"] != sha256(directory / "tasks.jsonl"):
        raise ValueError("The assessed experiment changed")
    rows = read(directory / "assessed_tasks.json")
    paired = defaultdict(dict)
    for row in rows:
        if row["arm"] in {"after", "optimized"}:
            paired[(row["case_id"], row["repeat"])][row["arm"]] = row["strict_score"]
    if not paired or any(set(pair) != {"after", "optimized"} for pair in paired.values()):
        raise ValueError("The prompt comparison is incomplete")
    cases = defaultdict(list)
    for (case_id, _), pair in paired.items():
        cases[case_id].append(pair["optimized"] - pair["after"])
    baseline, candidate = (result["groups"][arm] for arm in ("after", "optimized"))
    raw = [json.loads(line) for line in (directory / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    checks = {"total_quality": candidate["strict_successes"] >= baseline["strict_successes"],
        "case_mean_quality": all(sum(values) >= 0 for values in cases.values()),
        "category_quality": all(candidate["categories"][key]["strict_successes"] >= value["strict_successes"]
                                for key, value in baseline["categories"].items()),
        "known_usage": all(row.get("usage_complete", False) for row in raw if row["arm"] in {"after", "optimized"}),
        "fewer_tokens": candidate["tokens"] < baseline["tokens"]}
    if strict_pairs:
        checks["no_new_failure"] = all(pair["optimized"] >= pair["after"] for pair in paired.values())
    return {"passed": all(checks.values()), "checks": checks, "pairs": len(paired),
        "tasks_sha256": result["tasks_sha256"], "assessment_sha256": sha256(directory / "assessment.json"),
        "baseline": baseline, "candidate": candidate}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    directory = ROOT / "project_notes/optimization/renewed-gepa-v1"
    acceptance = gate("renewed-acceptance-v1")
    regression = gate("renewed-regression-v1", strict_pairs=True) if args.apply else None
    promote = bool(args.apply and acceptance["passed"] and regression["passed"])
    candidate_path = directory / "selected_candidate.json"
    candidate = read(candidate_path)
    if candidate != read(ROOT / "prompts/candidates/c4d319c7d66d7155ea18.json"):
        raise ValueError("The frozen candidate changed")
    if promote:
        (ROOT / "prompts/benchmark.json").write_bytes((json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    result = {**manifest(ROOT, "renewed-promotion-decision", {"candidate_sha256": sha256(candidate_path)}),
        "status": "completed" if args.apply else "acceptance_gate_completed", "acceptance": acceptance,
        "regression": regression, "default_promoted": promote, "candidate_id": "c4d319c7d66d7155ea18",
        "default_sha256": sha256(ROOT / "prompts/benchmark.json"), "fmp_requests": 0}
    write_json(directory / ("promotion_decision.json" if args.apply else "acceptance_gate.json"), result)
    print(json.dumps({"acceptance_passed": acceptance["passed"], "regression_passed": regression["passed"] if regression else None,
                      "default_promoted": promote}))


if __name__ == "__main__":
    main()
