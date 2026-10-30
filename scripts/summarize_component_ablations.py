"""Audit paired component runs and explain observed changes from saved outputs."""

import argparse
import ast
from collections import Counter
from decimal import Decimal
import json
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.runtime import write_json
from finevolab.grounded import validate_explanation
from scripts.estimate_paired_uncertainty import estimate


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def compare(records, left, right, field="score"):
    lookup = {(row["case_id"], row["repeat"], row.get("arm")): row for row in records}
    keys = sorted({(row["case_id"], row["repeat"]) for row in records})
    pairs = [{"case_id": case, "repeat": repeat,
        "difference": lookup[case, repeat, right][field] - lookup[case, repeat, left][field],
        "left": lookup[case, repeat, left][field], "right": lookup[case, repeat, right][field]}
        for case, repeat in keys]
    report = {"complete_pairing": True, "observed_pairs": len(pairs), "expected_pairs": len(pairs),
              "pairs": pairs, "left_run": left, "right_run": right}
    return {**report, "uncertainty": estimate(report),
        "wins": sum(pair["difference"] > 0 for pair in pairs),
        "losses": sum(pair["difference"] < 0 for pair in pairs),
        "ties": sum(pair["difference"] == 0 for pair in pairs)}


def audit_run(directory, expected):
    identity = json.loads((directory / "manifest.json").read_text())
    records = rows(directory / "tasks.jsonl")
    keys = [(row["case_id"], row["repeat"], row["arm"]) for row in records]
    config = identity["config"]
    assert len(records) == expected and len(set(keys)) == expected
    assert len(config["case_ids"]) * config["repeats"] * len(config["arms"]) == expected
    assert set(keys) == {(case, repeat, arm) for case in config["case_ids"]
                         for repeat in range(config["repeats"]) for arm in config["arms"]}
    current_differences = []
    for path, value in config["source_hashes"].items():
        if sha256(ROOT / path) != value:
            original = subprocess.check_output(["git", "show", f"{identity['commit']}:{path}"], cwd=ROOT)
            assert hashlib.sha256(original).hexdigest() == value, path
            current_differences.append(path)
    for path in ("prompts/benchmark.json", "configs/retrieval.json", "configs/company_aliases.json"):
        assert sha256(ROOT / path) == identity["source_hashes"][path], path
    dataset = ROOT / f"data/evaluation/closure/{config['split']}.json"
    assert sha256(dataset) == config["dataset_sha256"]
    plans = rows(directory / "plans.jsonl")
    lookup = {(row["case_id"], row["repeat"]): row["plan"] for row in plans}
    assert len(lookup) == len(config["case_ids"]) * config["repeats"]
    assert all(row["plan"] == lookup[row["case_id"], row["repeat"]] for row in records)
    return records, {"run_id": directory.name, "commit": identity["commit"], "rows": len(records),
        "complete_pairing": True, "shared_plans": len(plans), "recorded_code_hashes_verified": True,
        "current_code_differences": current_differences,
        "dataset_hash_matches": True, "unknown_plan_usage": sum(row.get("usage") is None for row in plans),
        "plan_requests": sum((row.get("usage") or {}).get("requests", 0) for row in plans),
        "plan_tokens": sum((row.get("usage") or {}).get("total_tokens", 0) for row in plans),
        "arm_requests": sum((row.get("usage") or {}).get("requests", 0) for row in records),
        "arm_tokens": sum((row.get("usage") or {}).get("total_tokens", 0) for row in records),
        "execution_errors": dict(Counter(row["error_type"] for row in records if row.get("error_type")))}


def run(args):
    experiments = ROOT / "project_notes/experiments"
    numeric, numeric_audit = audit_run(experiments / args.numeric, 108)
    rag, rag_audit = audit_run(experiments / args.rag, 120)
    corpus = json.loads((experiments / args.rag / "corpus_identity.json").read_text(encoding="utf-8-sig"))
    assert corpus["verified_sources"] == 22
    for path, value in corpus["source_hashes"].items():
        assert sha256(ROOT / path) == value, path
    verified_sources = 0
    for name in ("filing_sources.json", "sources.json", "sec_sources.json"):
        for source in json.loads((ROOT / "data" / name).read_text(encoding="utf-8")):
            assert sha256(ROOT / source["path"]) == source["sha256"], source["path"]
            verified_sources += 1
    assert verified_sources == 22
    rag_audit["pinned_corpus_hashes_match"] = True
    rag_audit["verified_sources"] = corpus["verified_sources"]
    historical = subprocess.check_output(["git", "show", f"{numeric_audit['commit']}:finevolab/grounded.py"], cwd=ROOT).decode()
    current = (ROOT / "finevolab/grounded.py").read_text(encoding="utf-8")
    def numeric_definitions(text):
        return [ast.dump(node, include_attributes=False) for node in ast.parse(text).body
                if getattr(node, "name", None) in {"RequestPlan", "assemble_numeric"} or
                isinstance(node, ast.Assign) and any(isinstance(item, ast.Name) and item.id == "PLAN_PROMPT" for item in node.targets)]
    assert numeric_definitions(historical) == numeric_definitions(current)
    numeric_audit["numeric_plan_schema_prompt_and_assembly_unchanged"] = True
    review = ROOT / "project_notes/evaluations" / args.review
    ratings = json.loads((review / "scores.json").read_text())
    assert len(ratings) == len(rag)
    results = {"numeric": {left: compare(numeric, left, "program_assembly") for left in
                            ("model_assembly", "model_calculation")},
               "rag_mechanical": {left: compare(rag, left, "improved") for left in
                            ("baseline", "no_expansion", "no_priority", "none")},
               "rag_grounded": {left: compare(ratings, left, "improved", "grounded_score") for left in
                            ("baseline", "no_expansion", "no_priority", "none")}}
    growth = [row for row in numeric if row["category"] == "annual_growth"]
    results["growth_calculator"] = compare(growth, "model_calculation", "model_assembly")
    failures = []
    for row in numeric:
        if row["score"]:
            continue
        item = {key: row[key] for key in ("case_id", "repeat", "arm", "checks")}
        calculations = row.get("output", {}).get("answer", {}).get("calculations", [])
        reference = next(value for value in numeric if value["case_id"] == row["case_id"]
                         and value["repeat"] == row["repeat"] and value["arm"] == "program_assembly")
        expected = reference["output"]["answer"]["calculations"]
        if calculations and expected:
            item.update(actual_growth=str(calculations[0]["value"]), expected_growth=str(expected[0]["value"]),
                        absolute_error_pp=str(abs(Decimal(str(calculations[0]["value"])) - Decimal(str(expected[0]["value"])))))
        failures.append(item)
    mechanism = []
    for row in rag:
        if row["arm"] != "improved" or not row["score"]:
            continue
        used = {item["id"] for item in row["output"]["evidence"]}
        counterparts = [value for value in rag if value["case_id"] == row["case_id"] and value["repeat"] == row["repeat"]]
        mechanism.append({"case_id": row["case_id"], "repeat": row["repeat"], "improved_used_passages": sorted(used),
            "counterparts": {value["arm"]: {"score": value["score"],
                "selected_passages": len(value.get("retrieved", [])),
                "table_passages": sum(item.get("kind") == "table" for item in value.get("retrieved", [])),
                "improved_evidence_present": len(used & {item["id"] for item in value.get("retrieved", [])}),
                "improved_evidence_total": len(used)} for value in counterparts}})
    output = ROOT / "project_notes/evaluations" / args.output
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "paired_comparisons.json", results)
    write_json(output / "numeric_failures.json", failures)
    write_json(output / "retrieval_mechanisms.json", mechanism)
    metrics = {}
    for suite, records, identity in (("numeric", numeric, numeric_audit), ("rag", rag, rag_audit)):
        metrics[suite] = {}
        for arm in sorted({row["arm"] for row in records}):
            subset = [row for row in records if row["arm"] == arm]
            repairs = [row.get("output", {}).get("repair", {}).get("count", 0) for row in subset]
            tokens = sum((row.get("usage") or {}).get("total_tokens", 0) for row in subset)
            metrics[suite][arm] = {"attempts": len(subset), "successes": sum(row["score"] for row in subset),
                "first_pass_successes": sum(row["score"] for row, repair in zip(subset, repairs) if not repair),
                "repairs": sum(repairs), "stage_tokens": tokens, "shared_plan_tokens": identity["plan_tokens"],
                "path_tokens_with_shared_plan": tokens + identity["plan_tokens"],
                "stage_requests": sum((row.get("usage") or {}).get("requests", 0) for row in subset),
                "mean_stage_seconds": sum(row["elapsed_seconds"] for row in subset) / len(subset),
                "unknown_usage": sum(row.get("usage") is None or not row.get("output", {}).get("usage_complete", True) for row in subset),
                "retrieved_characters": sum(item["chars"] for row in subset for item in row.get("retrieved", []))}
    write_json(output / "component_metrics.json", metrics)
    focus_directory = experiments / args.focus
    focus_rows = rows(focus_directory / "tasks.jsonl")
    focus_identity = json.loads((focus_directory / "manifest.json").read_text())
    assert len(focus_rows) == 120
    assert focus_identity["config"]["rows_sha256"] == sha256(experiments / args.rag / "tasks.jsonl")
    for path, value in focus_identity["config"]["core"].items():
        assert sha256(ROOT / path) == value, path
    original_lookup = {(row["case_id"], row["repeat"], row["arm"]): row for row in rag}
    assert len({(row["case_id"], row["repeat"], row["arm"]) for row in focus_rows}) == 120
    for row in focus_rows:
        original = original_lookup[row["case_id"], row["repeat"], row["arm"]]["output"]
        assert hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest() == row["original_output_sha256"]
        assert row["output"]["events"] == original["events"]
        assert row["output"]["request"] == original["request"]
        if not row["repaired"]:
            assert row["output"] == original
        else:
            assert row["output"]["repair"]["count"] == 1
        if row["score"]:
            validate_explanation(ROOT, row["output"])
    focus_review = ROOT / "project_notes/evaluations" / args.focus_review
    focus_scores = json.loads((focus_review / "scores.json").read_text())
    assert len(focus_scores) == 120
    focus_review_summary = json.loads((focus_review / "summary.json").read_text())
    assert all(value == 0 for value in focus_review_summary["control_errors"].values())
    focus_before = {(row["case_id"], row["repeat"], row["arm"]): row for row in ratings}
    focus_differences = {}
    for arm in sorted({row["arm"] for row in focus_scores}):
        paired = []
        for row in focus_scores:
            if row["arm"] != arm:
                continue
            before = focus_before[row["case_id"], row["repeat"], arm]
            paired.extend([{**row, "arm": "before", "score": before["grounded_score"]},
                           {**row, "arm": "after", "score": row["grounded_score"]}])
        focus_differences[arm] = compare(paired, "before", "after")
    write_json(output / "focus_comparisons.json", {"repair_vs_same_initial_answers": focus_differences,
        "retrieval_with_same_repair_policy": {arm: compare(focus_scores, arm, "improved", "grounded_score")
            for arm in ("baseline", "no_expansion", "no_priority", "none")}})
    focus_summary = json.loads((focus_directory / "summary.json").read_text())
    harness = experiments / args.harness
    harness_identity = json.loads((harness / "manifest.json").read_text())
    assert harness_identity["config"]["numeric_rows_sha256"] == sha256(experiments / args.numeric / "tasks.jsonl")
    for path, value in harness_identity["config"]["source_hashes"].items():
        if sha256(ROOT / path) != value:
            original = subprocess.check_output(["git", "show", f"{harness_identity['commit']}:{path}"], cwd=ROOT)
            assert hashlib.sha256(original).hexdigest() == value, path
    validation = json.loads((harness / "validation_summary.json").read_text())
    repair = json.loads((harness / "repair_summary.json").read_text())
    checkpoint = json.loads((harness / "checkpoint_summary.json").read_text())
    assert validation["request_contract"]["false_accepts"] == 0
    assert validation["request_contract"]["false_rejects"] == 0
    repair_rows = rows(harness / "repair.jsonl")
    checkpoint_rows = rows(harness / "checkpoint.jsonl")
    assert len(repair_rows) == 18 and len(checkpoint_rows) == 12
    assert len({(row["case_id"], row["fault"], row["enabled"]) for row in repair_rows}) == 18
    assert len({(row["case_id"], row["fault"], row["checkpoint"]) for row in checkpoint_rows}) == 12
    for row in repair_rows:
        other = next(item for item in repair_rows if item["case_id"] == row["case_id"] and item["fault"] == row["fault"] and item["enabled"] != row["enabled"])
        assert row["injected_response"] == other["injected_response"]
    review_summary = json.loads((review / "summary.json").read_text())
    assert all(value == 0 for value in review_summary["control_errors"].values())
    usage = {"formal_component_requests": numeric_audit["plan_requests"] + numeric_audit["arm_requests"]
             + rag_audit["plan_requests"] + rag_audit["arm_requests"],
        "formal_component_tokens": numeric_audit["plan_tokens"] + numeric_audit["arm_tokens"]
             + rag_audit["plan_tokens"] + rag_audit["arm_tokens"],
        "repair_real_requests": sum(len(row["real_requests"]) for row in repair_rows),
        "repair_real_tokens": sum((request["usage"] or {}).get("total_tokens", 0) for row in repair_rows for request in row["real_requests"]),
        "checkpoint_real_requests": sum(row["usage"]["requests"] for row in checkpoint_rows),
        "checkpoint_real_tokens": sum(row["usage"]["total_tokens"] for row in checkpoint_rows),
        "review_requests": review_summary["real_requests"], "review_tokens": review_summary["known_tokens"],
        "review_unknown_usage_requests": review_summary["unknown_usage_requests"],
        "focus_repair_requests": focus_summary["real_repair_requests"],
        "focus_repair_tokens": focus_summary["known_repair_tokens"],
        "focus_review_requests": focus_review_summary["real_requests"],
        "focus_review_tokens": focus_review_summary["known_tokens"],
        "invoice_cost": None, "synthetic_fault_responses_counted_as_real_requests": False}
    usage["formal_total_requests"] = sum(usage[key] for key in ("formal_component_requests", "repair_real_requests", "checkpoint_real_requests", "review_requests", "focus_repair_requests", "focus_review_requests"))
    usage["formal_total_tokens"] = sum(usage[key] for key in ("formal_component_tokens", "repair_real_tokens", "checkpoint_real_tokens", "review_tokens", "focus_repair_tokens", "focus_review_tokens"))
    development = {}
    for name in ("component-numeric-dev", "component-numeric-dev-v2", "component-rag-dev", "component-rag-dev-v2", "component-rag"):
        directory = experiments / name
        records, plans = rows(directory / "tasks.jsonl"), rows(directory / "plans.jsonl")
        development[name] = {"requests": sum((row.get("usage") or {}).get("requests", 0) for row in records + plans),
                             "tokens": sum((row.get("usage") or {}).get("total_tokens", 0) for row in records + plans)}
    usage["development_and_superseded_runs"] = development
    previous_focus = json.loads((experiments / "component-focus-repair/summary.json").read_text())
    previous_review = json.loads((ROOT / "project_notes/evaluations/component-focus-review/summary.json").read_text())
    development["superseded_focus_repair_and_review"] = {
        "requests": previous_focus["real_repair_requests"] + previous_review["real_requests"],
        "tokens": previous_focus["known_repair_tokens"] + previous_review["known_tokens"]}
    usage["all_total_requests"] = usage["formal_total_requests"] + sum(item["requests"] for item in development.values())
    usage["all_total_tokens"] = usage["formal_total_tokens"] + sum(item["tokens"] for item in development.values())
    write_json(output / "usage.json", usage)
    legal = {}
    for name in ("LICENSE", "NOTICE", "TRADEMARK_POLICY.md"):
        path = ROOT / "licenses/finrobot" / name
        if path.exists():
            original = subprocess.check_output(["git", "show", f"2717499b8e30f242640af08c4ad9afd1113c2d45:{name}"], cwd=ROOT)
            legal[name] = original == path.read_bytes()
    assert all(legal.values())
    ignored = subprocess.run(["git", "check-ignore", "finrobot_equity/core/config/config.ini"], cwd=ROOT, capture_output=True).returncode == 0
    assert ignored
    tests = json.loads((ROOT / "project_notes/run_logs/component_tests.json").read_text(encoding="utf-8-sig"))
    assert tests["status"] == "passed" and tests["tests"] == 72
    for path, value in {**tests["source_hashes"], **tests["test_hashes"]}.items():
        assert sha256(ROOT / path) == value, path
    write_json(output / "audit.json", {"status": "passed", "numeric": numeric_audit, "rag": rag_audit,
        "analysis_script_sha256": sha256(Path(__file__)),
        "analysis_base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "repair_pairs": len(repair_rows) // 2, "checkpoint_pairs": len(checkpoint_rows) // 2,
        "legal_unchanged": legal, "secret_config_ignored": ignored,
        "review_control_errors": review_summary["control_errors"], "tests": tests["tests"],
        "acceptance_used_for_parameter_selection": False,
        "acceptance_used_for_scale_defect_audit": True,
        "superseded_rag_run_retained": "component-rag"})
    write_json(output / "focus_audit.json", {"status": "passed", "commit": focus_identity["commit"],
        "unchanged_original_answers_and_passages": True, "rows": len(focus_rows),
        "maximum_one_repair_per_original_answer": True, "actual_repairs": focus_summary["real_repair_requests"],
        "repair_failures": focus_summary["failed_repairs"], "review_control_errors": focus_review_summary["control_errors"]})
    print(json.dumps({"status": "passed", "numeric_failures": len(failures), "usage": usage}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--numeric", default="component-numeric")
    parser.add_argument("--rag", default="component-rag-v2")
    parser.add_argument("--harness", default="component-harness")
    parser.add_argument("--review", default="component-blind-review")
    parser.add_argument("--output", default="component-analysis")
    parser.add_argument("--focus", default="component-focus-repair-v2")
    parser.add_argument("--focus-review", default="component-focus-review-v2")
    run(parser.parse_args())
