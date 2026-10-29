"""Aggregate paired retrieval and review experiments without new model calls."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service_experiment_utils import digest, write


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def summarize(retrieval_dir, roles_dir, controls_dir):
    infrastructure = load(retrieval_dir + "/summary.json")
    roles = load(roles_dir + "/summary.json")
    controls = load(controls_dir + "/summary.json")
    rows = load(roles_dir + "/records.json")
    if len(rows) != 72 or len({(r["case_id"], r["arm"]) for r in rows}) != 72:
        raise ValueError("The three-arm business comparison is incomplete")
    if roles["review"]["control_errors"] != [0, 0]:
        raise ValueError("Blind-review calibration failed")
    by_case = defaultdict(dict)
    by_source = defaultdict(list)
    for row in rows:
        by_case[row["case_id"]][row["arm"]] = row
        by_source[row["source_case_id"]].append(row)
    pairs = {}
    for baseline, candidate in (("S", "R"), ("S", "M"), ("R", "M")):
        pairs[candidate + "_vs_" + baseline] = {
            "wins": sum(c[candidate]["strict_score"] > c[baseline]["strict_score"] for c in by_case.values()),
            "losses": sum(c[candidate]["strict_score"] < c[baseline]["strict_score"] for c in by_case.values()),
            "ties": sum(c[candidate]["strict_score"] == c[baseline]["strict_score"] for c in by_case.values())}
    clusters = {}
    for arm in ("S", "R", "M"):
        clusters[arm] = sum(all(r["strict_score"] for r in cluster if r["arm"] == arm)
                            for cluster in by_source.values())
    language = {arm: {lang: sum(r["strict_score"] for r in rows if r["arm"] == arm and r["language"] == lang)
                      for lang in sorted({r["language"] for r in rows})} for arm in ("S", "R", "M")}
    review_usage = []
    for reviewer in ("A", "B"):
        review_usage.extend(load(roles_dir + "/review_usage_" + reviewer + ".json"))
    control_rows = load(controls_dir + "/records.json")
    support = {}
    for arm in ("self_check", "reviewer"):
        subset = [r for r in control_rows if r["arm"] == arm]
        support[arm] = {
            "false_accepts": sum(all(c["verdict"] == "supported" for c in r["review"]["claims"])
                                 and not r["expected_supported"] for r in subset),
            "false_rejects": sum(any(c["verdict"] != "supported" for c in r["review"]["claims"])
                                 and r["expected_supported"] for r in subset),
            "coverage_only_rejections": sum(not r["accepted"] and r["expected_supported"]
                and all(c["verdict"] == "supported" for c in r["review"]["claims"]) for r in subset)}
    sources = {}
    for directory in (retrieval_dir, roles_dir, controls_dir):
        for name in ("manifest.json", "summary.json", "records.json", "source.patch"):
            relative = directory + "/" + name
            sources[relative] = digest(ROOT / relative)
    return {"status": "completed", "evidence_sha256": sources,
        "infrastructure": infrastructure, "business": roles, "business_paired": pairs,
        "business_source_task_clusters": len(by_source), "clusters_with_both_languages_strictly_passed": clusters,
        "strict_successes_by_language": language,
        "online_review_corrections": {arm: [r["case_id"] for r in rows if r["arm"] == arm
            and r.get("output", {}).get("review", {}).get("repaired", False)] for arm in ("S", "R", "M")},
        "offline_business_review": {"requests": len(review_usage),
            "total_tokens": sum(r["total_tokens"] for r in review_usage if r)},
        "control_acceptance": controls, "control_support_only": support,
        "control_prediction_pairs": sum(r["expected_supported"] for r in control_rows if r["arm"] == "source_validation"),
        "control_source_request_ids": len({r["source_case_id"] for r in control_rows}),
        "decisions": {"default_vector_backend": "numpy", "redis": "optional shared public-corpus cache",
            "milvus": "optional Milvus Lite gRPC backend; no measured local latency improvement",
            "default_review_mode": "off", "review_mode_selection": "self_check preferred for opt-in review in this validation run",
            "reviewer": "optional isolated same-model role; no measured advantage over self_check"},
        "scope": "Validation comparison: 24 bilingual requests from 12 source tasks, one run per arm; not an independent test or a cross-model review"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "project_notes/evaluations/retrieval_service_comparison.json")
    parser.add_argument("--retrieval-run", default="project_notes/experiments/retrieval-services-v2")
    parser.add_argument("--roles-run", default="project_notes/experiments/agent-roles-v1")
    parser.add_argument("--controls-run", default="project_notes/experiments/claim-review-v3")
    args = parser.parse_args()
    result = summarize(args.retrieval_run, args.roles_run, args.controls_run)
    write(args.output, result)
    print(json.dumps({"status": result["status"], "business_paired": result["business_paired"]}))
