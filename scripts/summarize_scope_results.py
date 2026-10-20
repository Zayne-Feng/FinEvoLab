"""Aggregate repeated paired tasks with source-support reviews and uncertainty."""

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.runtime import write_json
from estimate_paired_uncertainty import estimate


def wilson(successes, size):
    if not size:
        return None
    z = 1.959963984540054
    p, d = successes / size, 1 + z * z / size
    center = (p + z * z / (2 * size)) / d
    width = z * math.sqrt(p * (1 - p) / size + z * z / (4 * size * size)) / d
    return [center - width, center + width]


def approved(rating):
    return (bool(rating["claims"]) and all(c["supported"] for c in rating["claims"])
            and rating["relevance"] == 2 and rating["interpretation"] == 2)


def group(rows):
    cases = defaultdict(list)
    for r in rows:
        cases[r["case_id"]].append(r["strict_score"])
    all_pass = sum(all(values) for values in cases.values())
    return {"attempts": len(rows), "mechanical_successes": sum(r["score"] for r in rows),
        "strict_successes": sum(r["strict_score"] for r in rows),
        "strict_rate": statistics.mean(r["strict_score"] for r in rows) if rows else None,
        "primary_only_successes": sum(r["primary_score"] for r in rows),
        "unique_questions": len(cases), "all_repeats_pass_questions": all_pass,
        "all_repeats_pass_wilson_95": wilson(all_pass, len(cases)),
        "requests": sum(r.get("usage", {}).get("requests", 0) for r in rows),
        "tokens": sum(r.get("usage", {}).get("total_tokens", 0) for r in rows),
        "unknown_usage_tasks": sum(not r.get("usage_complete", False) for r in rows),
        "mean_seconds": statistics.mean(r["elapsed_seconds"] for r in rows) if rows else None,
        "repairs": sum(r.get("output", {}).get("repair", {}).get("count",
            len(r.get("partial_telemetry", {}).get("repairs", []))) for r in rows)}


def main(args):
    directory = ROOT / "project_notes/experiments" / args.experiment
    review_dir = ROOT / "project_notes/evaluations" / args.review
    rows = [json.loads(line) for line in (directory / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    config = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["config"]
    dataset = ROOT / config.get("dataset_path", f"data/evaluation/scope_v3/{config['split']}.json")
    cases = json.loads(dataset.read_text(encoding="utf-8"))["tasks"]
    if config.get("limit"):
        cases = cases[:config["limit"]]
    expected = {(case["id"], repeat, arm) for case in cases for repeat in range(config["repeats"])
                for arm in (["current", "legacy"] if config["paired"] and case["common_support"] else ["current"])}
    observed = {(row["case_id"], row["repeat"], row["arm"]) for row in rows}
    if observed != expected or len(rows) != len(expected):
        raise ValueError("The planned task pairing is incomplete or duplicated")
    review_summary = json.loads((review_dir / "summary.json").read_text(encoding="utf-8"))
    if review_summary["status"] != "completed" or any(review_summary["control_errors"].values()):
        raise ValueError("Blind review or its support controls did not pass")
    a = json.loads((review_dir / "reviewer_A.json").read_text(encoding="utf-8"))
    b = json.loads((review_dir / "reviewer_B.json").read_text(encoding="utf-8"))
    mapping = {(m["case_id"], m["repeat"], m["arm"]): m["sample_id"]
               for m in json.loads((review_dir / "mappings.json").read_text(encoding="utf-8"))}
    for row in rows:
        key = (row["case_id"], row["repeat"], row["arm"])
        identifier = mapping.get(key)
        row["primary_score"] = int(bool(row["score"]) and (not identifier or approved(a[identifier])))
        row["strict_score"] = int(bool(row["primary_score"]) and (not identifier or identifier not in b or approved(b[identifier])))
        row["review_sample"] = identifier
    groups = {}
    for arm in sorted({r["arm"] for r in rows}):
        values = [r for r in rows if r["arm"] == arm]
        groups[arm] = {"all": group(values),
            "common": group([r for r in values if r["common_support"]]),
            "new_capabilities": group([r for r in values if not r["common_support"]]),
            "categories": {category: group([r for r in values if r["category"] == category])
                           for category in sorted({r["category"] for r in values})},
            "repeats": {str(rep): group([r for r in values if r["repeat"] == rep]) for rep in sorted({r["repeat"] for r in values})}}
    pairs, by_key = [], {(r["case_id"], r["repeat"], r["arm"]): r for r in rows}
    for row in rows:
        if row["arm"] != "current" or not row["common_support"]:
            continue
        other = by_key.get((row["case_id"], row["repeat"], "legacy"))
        if other:
            pairs.append({"case_id": row["case_id"], "repeat": row["repeat"],
                "current": row["strict_score"], "legacy": other["strict_score"],
                "difference": row["strict_score"] - other["strict_score"],
                "primary_fact_cluster": "boundary_contract" if row["category"] == "boundary" else row["fact_clusters"][0]})
    comparison = None
    if pairs:
        report = {"left_run": "current", "right_run": "legacy", "complete_pairing": True,
                  "observed_pairs": len(pairs), "expected_pairs": len(pairs), "pairs": pairs}
        uncertainty = estimate(report)
        clusters = defaultdict(list)
        for pair in pairs:
            clusters[pair["primary_fact_cluster"]].append(pair["difference"])
        generator = np.random.default_rng(71)
        keys = sorted(clusters)
        sampled = []
        for _ in range(10000):
            chosen = generator.choice(keys, size=len(keys), replace=True)
            values = [v for key in chosen for v in clusters[key]]
            sampled.append(statistics.mean(values))
        comparison = {"pairs": len(pairs), "question_clusters": uncertainty["case_clusters"],
            "wins": sum(p["difference"] > 0 for p in pairs), "losses": sum(p["difference"] < 0 for p in pairs),
            "ties": sum(p["difference"] == 0 for p in pairs), "task_bootstrap": uncertainty,
            "primary_fact_cluster_sensitivity": {"clusters": len(keys), "interval_95": np.quantile(sampled, [.025, .975]).tolist(),
                "method": "resample primary company-year clusters and one shared boundary-contract cluster; cross-company dependence not fully removed"}}
    failures = [{key: row.get(key) for key in ("case_id", "repeat", "arm", "category", "score", "strict_score", "checks", "error_type", "error", "review_sample")}
                for row in rows if not row["strict_score"]]
    write_json(directory / "assessed_tasks.json", [{key: row.get(key) for key in
        ("case_id", "repeat", "arm", "category", "score", "strict_score", "primary_score", "review_sample")} for row in rows])
    write_json(directory / "assessment.json", {"status": "completed", "groups": groups, "paired": comparison,
        "failures": failures, "tasks_sha256": sha256(directory / "tasks.jsonl"),
        "review_summary_sha256": sha256(review_dir / "summary.json"), "review_directory": args.review,
        "strict_rule": "mechanical checks and every claim supported and relevance=2 and interpretation=2; intersection where two reviewers available",
        "no_repeated_answer_independence_assumption": True})
    print(json.dumps({"groups": {a: v["all"] for a, v in groups.items()}, "paired": comparison}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--review", required=True)
    main(parser.parse_args())
