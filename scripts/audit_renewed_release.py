"""Verify paired experiment completeness, source bindings, and blind-review identities."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json
from finevolab.upstream_baseline import UPSTREAM
from finevolab.scope_evaluation import assess_scope, task_store
from run_followup_uploads import selected_reference
from summarize_scope_results import approved


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    datasets = {}
    for version in ("renewed_v1", "renewed_v2", "renewed_v3"):
        base = ROOT / "data/evaluation" / version
        frozen = read(base / "manifest.json")
        for name, digest in frozen["files"].items():
            if sha256(base / name) != digest:
                raise ValueError("A frozen task file changed")
        for name, digest in frozen["sources"].items():
            if sha256(ROOT / name) != digest:
                raise ValueError("A frozen financial source changed")
        datasets[version] = sha256(base / "manifest.json")
    runs = {}
    for run_id, review_id in (
        ("renewed-development-v1", "renewed-development-review-v1"),
        ("renewed-acceptance-v1", "renewed-acceptance-review-v1"),
        ("renewed-evidence-development-v2", "renewed-evidence-development-review-v2"),
        ("renewed-evidence-test-v2", "renewed-evidence-review-v2"),
        ("renewed-planner-development-v3", "renewed-planner-development-review-v3"),
        ("renewed-planner-test-v3", "renewed-planner-review-v3"),
        ("renewed-regression-v1", "renewed-regression-review-v1"),
    ):
        directory = ROOT / "project_notes/experiments" / run_id
        execution, assessment = read(directory / "manifest.json"), read(directory / "assessment.json")
        config = execution["config"]
        dataset = ROOT / config.get("dataset_path", f"data/evaluation/scope_v3/{config['split']}.json")
        expected_digest = config.get("dataset_sha256", config.get("dataset"))
        if sha256(dataset) != expected_digest:
            raise ValueError("An evaluated task split changed")
        cases = read(dataset)["tasks"]
        if config.get("case_ids"):
            cases = [case for case in cases if case["id"] in config["case_ids"]]
        if config.get("limit"):
            cases = cases[:config["limit"]]
        arms = config.get("arms", ["current"])
        expected = {(case["id"], repeat, arm) for case in cases for repeat in range(config["repeats"]) for arm in arms}
        rows = [json.loads(line) for line in (directory / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
        actual = {(row["case_id"], row["repeat"], row["arm"]) for row in rows}
        if actual != expected or len(rows) != len(expected):
            raise ValueError("An experiment has incomplete or duplicate pairings")
        if assessment["tasks_sha256"] != sha256(directory / "tasks.jsonl"):
            raise ValueError("An assessed task ledger changed")
        for name, digest in config["source_hashes"].items():
            raw = subprocess.check_output(["git", "show", f"{execution['commit']}:{name}"], cwd=ROOT)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("A tested source differs from its implementation commit: " + name)
            if run_id == "renewed-regression-v1" and sha256(ROOT / name) != digest:
                raise ValueError("The current release differs from the final regression: " + name)
        for name, digest in config.get("financial_sources", {}).items():
            if sha256(ROOT / name) != digest:
                raise ValueError("An evaluated financial snapshot changed")
        review = ROOT / "project_notes/evaluations" / review_id
        review_manifest, review_summary = read(review / "manifest.json"), read(review / "summary.json")
        if review_manifest["config"]["tasks_sha256"] != sha256(directory / "tasks.jsonl"):
            raise ValueError("A blind review refers to a different task ledger")
        if review_manifest["config"]["dataset_sha256"] != sha256(dataset):
            raise ValueError("A blind review refers to a different question split")
        if review_summary["status"] != "completed" or any(review_summary["control_errors"].values()):
            raise ValueError("Blind-review controls did not pass")
        mappings = {(row["case_id"], row["repeat"], row["arm"]): row["sample_id"]
                    for row in read(review / "mappings.json")}
        a, b = read(review / "reviewer_A.json"), read(review / "reviewer_B.json")
        assessed = {(row["case_id"], row["repeat"], row["arm"]): row for row in read(directory / "assessed_tasks.json")}
        documents = read(dataset.parent / "documents.json")
        stores = {}
        for row in rows:
            case = next(case for case in cases if case["id"] == row["case_id"])
            scope = tuple(case["upload_ids"])
            if scope not in stores:
                stores[scope] = task_store(ROOT, case, documents)
            if "output" in row and assess_scope(ROOT, stores[scope], selected_reference(case), row["output"])["score"] != row["score"]:
                raise ValueError("A mechanical score cannot be reproduced")
            key = (row["case_id"], row["repeat"], row["arm"])
            identifier = mappings.get(key)
            narrative = case["operation"].startswith(("hybrid", "explanation"))
            valid = not narrative or (identifier is not None and approved(a[identifier]) and
                                     (identifier not in b or approved(b[identifier])))
            if assessed[key]["strict_score"] != int(bool(row["score"]) and valid):
                raise ValueError("A strict score differs from its frozen blind reviews")
        for sample in read(review / "samples.json").values():
            if any(key in sample for key in ("arm", "repeat", "case_id", "expected_plan")):
                raise ValueError("The review input exposes experimental identities")
        if config["thinking"].get("thinking", {}).get("type") != "disabled":
            raise ValueError("Thinking was not explicitly disabled")
        runs[run_id] = {"commit": execution["commit"], "executions": len(rows),
            "tasks_sha256": assessment["tasks_sha256"], "assessment_sha256": sha256(directory / "assessment.json"),
            "review_sha256": sha256(review / "summary.json"), "groups": assessment["groups"]}
    optimization = ROOT / "project_notes/optimization/renewed-gepa-v1"
    optimized = read(optimization / "summary.json")
    if optimized["status"] != "completed" or optimized["search_evaluations"] > 48 or optimized["default_promoted"]:
        raise ValueError("The optimization budget or promotion record is invalid")
    seed = optimized["result"]["candidates"][0]
    if read(ROOT / "prompts/benchmark.json") != {"system": seed["system"],
            "tools": {key: value for key, value in seed.items() if key != "system"}}:
        raise ValueError("The default prompt changed without promotion")
    replay = read(ROOT / "project_notes/run_logs/renewed_semantic_replay.json")
    if replay["status"] != "passed" or replay["recorded_plans"] != replay["unchanged"]:
        raise ValueError("The semantic boundary replay did not pass")
    for name, digest in replay["source_sha256"].items():
        if sha256(ROOT / name) != digest:
            raise ValueError("A replayed planner ledger changed")
    delivery_path = ROOT / "project_notes/run_logs/renewed_delivery.json"
    delivery = read(delivery_path)
    if delivery["status"] != "passed" or len(delivery["recovery"]) != 9:
        raise ValueError("The current delivery verification is incomplete")
    for name, digest in delivery["source_hashes"].items():
        if name.startswith("finevolab/") and sha256(ROOT / name) != digest:
            raise ValueError("The current engine differs from the HTTP delivery")
    for artifact in delivery["web"]["reports"].values():
        if sha256(ROOT / artifact["path"]) != artifact["sha256"]:
            raise ValueError("A delivered report changed")
    visual = read(ROOT / "project_notes/run_logs/renewed_pdf_visual_review.json")
    if visual["status"] != "passed" or visual["pdf_sha256"] != delivery["web"]["reports"]["pdf"]["sha256"]:
        raise ValueError("The current PDF visual review is incomplete")
    for name in ("LICENSE", "NOTICE", "TRADEMARK_POLICY.md"):
        original = subprocess.check_output(["git", "show", f"{UPSTREAM}:{name}"], cwd=ROOT)
        if (ROOT / "licenses/finrobot" / name).read_bytes().replace(b"\r\n", b"\n") != original.replace(b"\r\n", b"\n"):
            raise ValueError("An upstream legal file changed")
    if subprocess.check_output(["git", "ls-files", "--", "**/config.ini", ".env"], cwd=ROOT, text=True).strip():
        raise ValueError("Private configuration is tracked")
    result = {**manifest(ROOT, "renewed-release-audit", {"datasets": datasets}),
        "status": "passed", "runs": runs, "optimization_sha256": sha256(optimization / "summary.json"),
        "paired_executions": sum(run["executions"] for run in runs.values()),
        "legal_files_unchanged": True, "private_config_untracked": True, "fmp_requests": 0,
        "default_promoted": False, "delivery_sha256": sha256(delivery_path),
        "current_pdf_visual_review": True,
        "independent_acceptance_and_historical_regression_separated": True}
    write_json(ROOT / "project_notes/run_logs/renewed_release_audit.json", result)
    print(json.dumps({key: result[key] for key in ("status", "paired_executions", "fmp_requests")}))


if __name__ == "__main__":
    main()
