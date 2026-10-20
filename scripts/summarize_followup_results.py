"""Join blinded support reviews and estimate paired case-cluster differences."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.runtime import write_json
from summarize_scope_results import approved, group


def main(args):
    directory = ROOT / "project_notes/experiments" / args.experiment
    rows = [json.loads(line) for line in (directory / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    config = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["config"]
    split = config.get("split", "common")
    cases = json.loads((ROOT / config.get("dataset_path", f"data/evaluation/followup_v1/{split}.json")).read_text(encoding="utf-8"))["tasks"]
    if config.get("case_ids"):
        cases = [case for case in cases if case["id"] in config["case_ids"]]
    if config.get("limit"):
        cases = cases[:config["limit"]]
    arms = config.get("arms", ["upstream", "current"])
    expected = {(case["id"], repeat, arm) for case in cases for repeat in range(config["repeats"]) for arm in arms}
    actual = {(r["case_id"], r["repeat"], r["arm"]) for r in rows}
    if expected != actual or len(rows) != len(expected):
        raise ValueError("The planned pairing is incomplete or duplicated")
    mapping, a, b = {}, {}, {}
    review_hash = None
    if args.review:
        reviews = ROOT / "project_notes/evaluations" / args.review
        summary = json.loads((reviews / "summary.json").read_text(encoding="utf-8"))
        if summary["status"] != "completed" or any(summary["control_errors"].values()):
            raise ValueError("Blind review or its controls failed")
        mapping = {(m["case_id"], m["repeat"], m["arm"]): m["sample_id"] for m in
            json.loads((reviews / "mappings.json").read_text(encoding="utf-8"))}
        a = json.loads((reviews / "reviewer_A.json").read_text(encoding="utf-8"))
        b = json.loads((reviews / "reviewer_B.json").read_text(encoding="utf-8"))
        review_hash = sha256(reviews / "summary.json")
    elif split != "common":
        raise ValueError("Narrative experiments require a completed blind review")
    for row in rows:
        identifier = mapping.get((row["case_id"], row["repeat"], row["arm"]))
        case = next(case for case in cases if case["id"] == row["case_id"])
        needs_review = case.get("operation", "").startswith(("hybrid", "explanation"))
        valid = not needs_review or (identifier is not None and approved(a[identifier]))
        row["primary_score"] = int(bool(row["score"]) and valid)
        row["strict_score"] = int(row["primary_score"] and (identifier not in b or approved(b[identifier])))
        row["review_sample"] = identifier
    groups = {arm: group([r for r in rows if r["arm"] == arm]) for arm in arms}
    for arm in arms:
        subset = [row for row in rows if row["arm"] == arm]
        groups[arm]["latency_p50_p95"] = np.quantile([row["elapsed_seconds"] for row in subset], [.5, .95]).tolist()
        groups[arm]["tokens_per_strict_success"] = (groups[arm]["tokens"] / groups[arm]["strict_successes"]
            if groups[arm]["strict_successes"] else None)
        groups[arm]["categories"] = {category: {
            "attempts": sum(row["category"] == category for row in subset),
            "strict_successes": sum(row["strict_score"] for row in subset if row["category"] == category)}
            for category in sorted({row["category"] for row in subset})}
    comparisons = {}
    lookup = {(r["case_id"], r["repeat"], r["arm"]): r for r in rows}
    contrasts = [("current", "upstream")] if split == "common" else (
        [("after", arm) for arm in ("before", "after_old_retrieval") if arm in arms] if len(arms) > 1 else [])
    if "optimized" in arms:
        contrasts.append(("optimized", "after"))
    for left, right in contrasts:
        differences = defaultdict(list)
        paired = []
        for case in cases:
            for repeat in range(config["repeats"]):
                x, y = (lookup[(case["id"], repeat, arm)]["strict_score"] for arm in (left, right))
                differences[case["id"]].append(x - y)
                paired.append(x - y)
        values = np.array([statistics.mean(v) for v in differences.values()])
        generator = np.random.default_rng(71)
        bootstrap = values[generator.integers(0, len(values), size=(10000, len(values)))].mean(axis=1)
        comparisons[f"{left}_vs_{right}"] = {"pairs": len(paired), "unique_cases": len(values),
            "wins": sum(v > 0 for v in paired), "losses": sum(v < 0 for v in paired),
            "ties": sum(v == 0 for v in paired), "difference": float(values.mean()),
            "case_cluster_bootstrap_95": np.quantile(bootstrap, [.025, .975]).tolist(),
            "method": "paired resampling of questions, retaining all repeats; shared-source dependence remains"}
        comparisons[f"{left}_vs_{right}"]["token_difference"] = groups[left]["tokens"] - groups[right]["tokens"]
        comparisons[f"{left}_vs_{right}"]["token_relative_change"] = (groups[left]["tokens"] / groups[right]["tokens"] - 1
            if groups[right]["tokens"] else None)
    result = {"status": "completed", "groups": groups, "comparisons": comparisons,
        "tasks_sha256": sha256(directory / "tasks.jsonl"), "review": args.review,
        "review_summary_sha256": review_hash, "failures": [{key: row.get(key) for key in
            ("case_id", "repeat", "arm", "score", "strict_score", "error_type", "error", "review_sample")}
            for row in rows if not row["strict_score"]], "repeated_calls_are_not_independent_questions": True}
    write_json(directory / "assessment.json", result)
    write_json(directory / "assessed_tasks.json", [{key: row.get(key) for key in
        ("case_id", "repeat", "arm", "score", "strict_score", "primary_score", "review_sample")} for row in rows])
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--review")
    main(parser.parse_args())
