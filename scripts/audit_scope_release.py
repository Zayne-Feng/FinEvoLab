"""Audit frozen inputs, complete pairing, source validation, and archived bytes."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json
from finevolab.scope_evaluation import assess_scope, task_store
from summarize_scope_results import approved


def git_digest(commit, name):
    return hashlib.sha256(subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)).hexdigest()


def verify_inputs(record, directory):
    inputs = {**record["config"]["source_hashes"], **record["config"]["source_files"]}
    path = directory / "input_archive.json"
    archive = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    variants = {v["path"]: v for v in archive["historical_newline_variants"]} if archive else {}
    if archive and (archive["executed_commit"] != record["commit"] or archive["input_sha256"] != inputs):
        raise ValueError("The exact input archive describes a different execution")
    for name, digest in inputs.items():
        runtime = (ROOT / name).read_bytes()
        if hashlib.sha256(runtime).hexdigest() != digest:
            raise ValueError(f"A frozen runtime input differs: {name}")
        historical_digest = git_digest(record["commit"], name)
        if historical_digest != digest:
            variant = variants.get(name)
            historical = subprocess.check_output(["git", "show", f"{record['commit']}:{name}"], cwd=ROOT)
            if (not variant or not name.startswith("data/") or not name.endswith(".json")
                    or variant["runtime_sha256"] != digest or variant["executed_git_sha256"] != historical_digest
                    or historical.replace(b"\r\n", b"\n") != runtime.replace(b"\r\n", b"\n")
                    or json.loads(historical) != json.loads(runtime)):
                raise ValueError(f"A historical input differs beyond declared JSON newlines: {name}")
        if archive and git_digest(archive["archive_commit"], name) != digest:
            raise ValueError(f"The exact input archive differs: {name}")
    return len(variants)


def verify_review(directory, review, rows):
    summary = json.loads((review / "summary.json").read_text(encoding="utf-8"))
    if summary["status"] != "completed" or any(summary["control_errors"].values()):
        raise ValueError("Blind-review support controls failed")
    assessment = json.loads((directory / "assessment.json").read_text(encoding="utf-8"))
    if assessment["tasks_sha256"] != sha256(directory / "tasks.jsonl") or assessment["review_summary_sha256"] != sha256(review / "summary.json"):
        raise ValueError("An assessed input changed")
    samples = json.loads((review / "samples.json").read_text(encoding="utf-8"))
    if any(set(sample) - {"question", "claims", "sources"} for sample in samples.values()):
        raise ValueError("Blind samples expose execution metadata")
    a = json.loads((review / "reviewer_A.json").read_text(encoding="utf-8"))
    b = json.loads((review / "reviewer_B.json").read_text(encoding="utf-8"))
    mappings = {(m["case_id"], m["repeat"], m["arm"]): m["sample_id"] for m in
                json.loads((review / "mappings.json").read_text(encoding="utf-8"))}
    assessed = {(r["case_id"], r["repeat"], r["arm"]): r for r in
                json.loads((directory / "assessed_tasks.json").read_text(encoding="utf-8"))}
    totals = {}
    for row in rows:
        key = (row["case_id"], row["repeat"], row["arm"])
        identifier = mappings.get(key)
        primary = bool(row["score"]) and (not identifier or approved(a[identifier]))
        strict = int(primary and (not identifier or identifier not in b or approved(b[identifier])))
        if assessed[key]["strict_score"] != strict:
            raise ValueError("An assessed strict score differs from its source reviews")
        totals[row["arm"]] = totals.get(row["arm"], 0) + strict
    for arm, total in totals.items():
        if assessment["groups"][arm]["all"]["strict_successes"] != total:
            raise ValueError("An aggregate strict score differs from its task scores")
    return summary


def main(args):
    directory = ROOT / "project_notes/experiments" / args.experiment
    review = ROOT / "project_notes/evaluations" / args.review
    record = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    config = record["config"]
    newline_variants = verify_inputs(record, directory)
    bank = ROOT / "data/evaluation/scope_v3"
    frozen = json.loads((bank / "manifest.json").read_text(encoding="utf-8"))
    for split, digest in frozen["split_sha256"].items():
        if sha256(bank / f"{split}.json") != digest:
            raise ValueError("A frozen task split changed")
    cases = json.loads((bank / f"{config['split']}.json").read_text(encoding="utf-8"))["tasks"]
    if config.get("limit"):
        cases = cases[:config["limit"]]
    cases = {case["id"]: case for case in cases}
    rows = [json.loads(line) for line in (directory / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = {(case["id"], repeat, arm) for case in cases.values() for repeat in range(config["repeats"])
                for arm in (["current", "legacy"] if config["paired"] and case["common_support"] else ["current"])}
    observed = {(row["case_id"], row["repeat"], row["arm"]) for row in rows}
    if observed != expected or len(rows) != len(expected):
        raise ValueError("Task execution is incomplete or duplicated")
    documents = json.loads((bank / "documents.json").read_text(encoding="utf-8"))
    stores, rescored, failed = {}, 0, 0
    for row in rows:
        if "output" not in row:
            if row["score"]:
                raise ValueError("A failed execution has a positive score")
            failed += 1
            continue
        case = cases[row["case_id"]]
        key = tuple(case["upload_ids"])
        if key not in stores:
            stores[key] = task_store(ROOT, case, documents)
        score = assess_scope(ROOT, stores[key], case, row["output"])
        if score["score"] != row["score"]:
            raise ValueError("A task score changed on independent recomputation")
        for step in row["output"].get("messages", []):
            if step.get("schema") == "RequestPlan":
                payload = json.loads(step["input"][1]["content"])
                if set(payload) - {"question", "selected_documents"}:
                    raise ValueError("Unexpected planner input fields")
        rescored += 1
    review_summary = verify_review(directory, review, rows)
    supplemental_stats = None
    if args.supplemental_experiment:
        supplemental_dir = ROOT / "project_notes/experiments" / args.supplemental_experiment
        supplemental_review = ROOT / "project_notes/evaluations" / args.supplemental_review
        supplemental_record = json.loads((supplemental_dir / "manifest.json").read_text(encoding="utf-8"))
        supplemental_config = supplemental_record["config"]
        supplemental_newline_variants = verify_inputs(supplemental_record, supplemental_dir)
        path = ROOT / supplemental_config["dataset_path"]
        if sha256(path) != supplemental_config["dataset"]:
            raise ValueError("The supplemental task set changed")
        supplemental_cases = {c["id"]: c for c in json.loads(path.read_text(encoding="utf-8"))["tasks"]}
        supplemental_rows = [json.loads(line) for line in (supplemental_dir / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
        expected_supplemental = {(key, rep, "current") for key in supplemental_cases for rep in range(supplemental_config["repeats"])}
        if {(r["case_id"], r["repeat"], r["arm"]) for r in supplemental_rows} != expected_supplemental or len(supplemental_rows) != len(expected_supplemental):
            raise ValueError("Supplemental execution is incomplete or duplicated")
        checked = 0
        for row in supplemental_rows:
            if "output" not in row:
                if row["score"]:
                    raise ValueError("A failed supplemental execution has a positive score")
                continue
            case = supplemental_cases[row["case_id"]]
            key = tuple(case["upload_ids"])
            if key not in stores:
                stores[key] = task_store(ROOT, case, documents)
            if assess_scope(ROOT, stores[key], case, row["output"])["score"] != row["score"]:
                raise ValueError("A supplemental financial score changed")
            checked += 1
        verify_review(supplemental_dir, supplemental_review, supplemental_rows)
        supplemental_stats = {"planned": len(expected_supplemental), "rescored_outputs": checked,
            "failed_executions_retained": len(supplemental_rows) - checked,
            "historical_json_newline_variants": supplemental_newline_variants}
    delivery_path = ROOT / "project_notes/run_logs/scope_delivery_v1.json"
    delivery = json.loads(delivery_path.read_text(encoding="utf-8"))
    if delivery["status"] != "passed" or len(delivery["recovery"]) != 9 or not all(r["recovered"] for r in delivery["recovery"]):
        raise ValueError("Delivery or recovery acceptance is incomplete")
    for report in delivery["web"]["reports"].values():
        if sha256(ROOT / report["path"]) != report["sha256"]:
            raise ValueError("A delivered report changed")
    visual = json.loads((ROOT / "project_notes/run_logs/scope_pdf_visual_review.json").read_text(encoding="utf-8"))
    if visual["status"] != "passed":
        raise ValueError("PDF visual review is incomplete")
    controls = json.loads((ROOT / "project_notes/experiments/scope-validation-controls/validation_summary.json").read_text(encoding="utf-8"))
    strict_controls = controls["request_contract"]
    if strict_controls["invalid"] != 150 or strict_controls["valid"] != 36 or strict_controls["false_accepts"] or strict_controls["false_rejects"]:
        raise ValueError("The fixed validation controls failed")
    repair = json.loads((ROOT / "project_notes/experiments/scope-sdk-repair/summary.json").read_text(encoding="utf-8"))
    if repair["groups"]["False"]["successes"] != 0 or repair["groups"]["True"]["successes"] != 9:
        raise ValueError("The paired SDK repair controls failed")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    paths = subprocess.check_output(["git", "ls-files", "project_notes/experiments/scope-*",
        "project_notes/evaluations/scope-*", "project_notes/data/scope_*.json",
        "data/evaluation/scope_v3", "data/evaluation/scope_supplemental", "data/acceptance_sources.json",
        "data/raw/acceptance_*", "data/indexes/contextual"], cwd=ROOT, text=True).splitlines()
    if not paths:
        raise ValueError("No archived experiment artifacts found")
    for name in paths:
        if sha256(ROOT / name) != git_digest(head, name):
            raise ValueError(f"Archived bytes differ from Git: {name}")
    result = {**manifest(ROOT, "scope-release-audit", {"experiment": args.experiment, "review": args.review}),
        "status": "passed", "planned_executions": len(expected), "rescored_outputs": rescored,
        "failed_executions_retained": failed, "frozen_release_inputs": len(config["source_hashes"]) + len(config["source_files"]),
        "archived_files_verified": len(paths), "blind_identity_hidden": True,
        "historical_json_newline_variants": newline_variants,
        "support_controls": review_summary["control_errors"], "pdf_visual_review": True,
        "validation_controls": strict_controls, "sdk_repair_controls": repair["groups"],
        "supplemental": supplemental_stats,
        "delivery_sha256": sha256(delivery_path), "assessment_sha256": sha256(directory / "assessment.json")}
    write_json(ROOT / "project_notes/run_logs/scope_release_audit.json", result)
    print(json.dumps({k: result[k] for k in ("status", "planned_executions", "rescored_outputs", "failed_executions_retained", "archived_files_verified")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--review", required=True)
    parser.add_argument("--supplemental-experiment")
    parser.add_argument("--supplemental-review")
    arguments = parser.parse_args()
    if bool(arguments.supplemental_experiment) != bool(arguments.supplemental_review):
        parser.error("Both supplemental experiment and review arguments are required together")
    main(arguments)
