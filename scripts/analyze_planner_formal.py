"""Aggregate paired planner effects, schema recovery, cohorts, and measured usage."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.grounded import RequestPlan
from finevolab.planner_benchmark import json_object, load_bundle, score_prediction
from finevolab.runtime import write_json
from scripts.estimate_paired_uncertainty import estimate
from scripts.planner_benchmark import checked_run, validate_arm_contracts

EXPERIMENTS = ROOT / "project_notes/experiments"
EVALUATIONS = ROOT / "project_notes/evaluations"
BUNDLE = ROOT / "data/training/planner_formal/frozen_v1"


def prediction_runs(directory):
    names = [f"planner-formal-{arm}-{split}" for arm in ("base", "deepseek") for split in ("validation", "test")]
    names += [f"planner-formal-lora{seed}-epoch{epoch}-validation" for seed in (17, 29) for epoch in (1, 2)]
    names += [f"planner-formal-lora{seed}-test" for seed in (17, 29)]
    paths = [directory / name for name in names]
    if any(not (path / filename).is_file() for path in paths for filename in ("manifest.json", "summary.json", "predictions.jsonl")):
        raise ValueError("A registered inference run is incomplete")
    return paths


def semantic_score(case, raw, aliases):
    try:
        value = json_object(raw)
    except ValueError:
        return 0
    defaults = {name: field.default for name, field in RequestPlan.model_fields.items() if not field.is_required()}
    return score_prediction(case, json.dumps({**defaults, **value}), aliases)["semantic_score"]


def effect(cases, left, right):
    groups = defaultdict(list)
    for case in cases:
        groups[case.group_id].append(right[case.id] - left[case.id])
    pairs = [{"case_id": name, "difference": statistics.mean(values)} for name, values in groups.items()]
    result = estimate({"complete_pairing": True, "observed_pairs": len(pairs), "expected_pairs": len(pairs),
                       "left_run": "qwen_base", "right_run": "qwen_lora", "pairs": pairs})
    result.update(wins=sum(right[c.id] > left[c.id] for c in cases),
                  losses=sum(right[c.id] < left[c.id] for c in cases),
                  ties=sum(right[c.id] == left[c.id] for c in cases))
    return result


def main():
    completion = json.loads((EVALUATIONS / "planner_formal_completion.json").read_text())
    if completion["status"] != "completed" or not completion["test_exports_verified"]:
        raise ValueError("Verified complete test exports are required")
    cases, contract = load_bundle(BUNDLE, "test")
    contract_aliases = contract["aliases"]
    result = {"status": "completed", "created_at": datetime.now(timezone.utc).isoformat(),
        "bundle_sha256": sha256(BUNDLE / "manifest.json"), "code_sha256": sha256(Path(__file__)),
        "selection": json.loads((EVALUATIONS / "planner_formal_selection_details.json").read_text()),
        "test_cases": len(cases), "template_clusters": len({c.group_id for c in cases}),
        "semantic_rule": "explicit_fields_with_only_declared_schema_defaults",
        "seeds": {}, "usage": {}, "training": {}}
    for seed in (17, 29):
        paths = {"deepseek": EXPERIMENTS / "planner-formal-deepseek-test",
                 "qwen_base": EXPERIMENTS / "planner-formal-base-test",
                 "qwen_lora": EXPERIMENTS / f"planner-formal-lora{seed}-test"}
        configurations, scores, raw_scores, rows = {}, {}, {}, {}
        for arm, path in paths.items():
            config, records = checked_run(path, BUNDLE, "test")
            configurations[arm] = config
            indexed = {row["case_id"]: row for row in records}
            rows[arm] = indexed
            raw_scores[arm] = {c.id: score_prediction(c, indexed[c.id]["attempts"][0]["raw"], contract_aliases)
                               for c in cases}
            scores[arm] = {"strict": {c.id: raw_scores[arm][c.id]["score"] for c in cases},
                "semantic": {c.id: semantic_score(c, indexed[c.id]["attempts"][0]["raw"], contract_aliases) for c in cases}}
        validate_arm_contracts(configurations, "test")
        entry = {"arms": {}, "paired_effects": {}}
        for arm in paths:
            cohort = {}
            for label, predicate in (
                ("english", lambda c: c.language == "en"), ("chinese", lambda c: c.language == "zh"),
                ("held_out_entity", lambda c: bool(set(c.expected.companies) & {"NVDA", "TSLA", "ADBE"})),
                ("training_entities", lambda c: bool(c.expected.companies) and not bool(set(c.expected.companies) & {"NVDA", "TSLA", "ADBE"})),
                ("unresolved_entity", lambda c: not c.expected.companies)):
                selected = [c for c in cases if predicate(c)]
                cohort[label] = {"cases": len(selected), **{metric: sum(scores[arm][metric][c.id] for c in selected)
                    for metric in ("strict", "semantic")}}
            categories = {category: {"cases": sum(c.category == category for c in cases),
                **{metric: sum(scores[arm][metric][c.id] for c in cases if c.category == category)
                   for metric in ("strict", "semantic")}} for category in sorted({c.category for c in cases})}
            entry["arms"][arm] = {"cases": len(cases), "strict_successes": sum(scores[arm]["strict"].values()),
                "semantic_successes": sum(scores[arm]["semantic"].values()),
                "raw_explicit_semantic_successes": sum(v["semantic_score"] for v in raw_scores[arm].values()),
                "schema_valid": sum(v["schema_valid"] for v in raw_scores[arm].values()),
                "cohorts": cohort, "categories": categories,
                "macro_category_strict_rate": statistics.mean(v["strict"] / v["cases"] for v in categories.values())}
        for metric in ("strict", "semantic"):
            entry["paired_effects"][metric] = effect(cases, scores["qwen_base"][metric], scores["qwen_lora"][metric])
        improvements = Counter()
        errors = []
        for case in cases:
            baseline, tuned = raw_scores["qwen_base"][case.id], raw_scores["qwen_lora"][case.id]
            if tuned["score"] and not baseline["score"]:
                mechanism = "semantic_field_recovery" if baseline["schema_valid"] else (
                    "schema_recovery_only" if scores["qwen_base"]["semantic"][case.id] else "schema_and_semantic_recovery")
                improvements[mechanism] += 1
            if not tuned["score"]:
                errors.append({"case_id": case.id, "group_id": case.group_id, "question": case.input.question,
                    "expected": case.expected.model_dump(), "required_fields": case.semantic_fields,
                    "raw": rows["qwen_lora"][case.id]["attempts"][0]["raw"], "checks": tuned})
        entry.update(strict_gain_mechanisms=dict(improvements), failures=errors)
        result["seeds"][str(seed)] = entry
        training = ROOT / f"output/training/formal-seed{seed}"
        result["training"][str(seed)] = json.loads((training / "training_result.json").read_text())
    for directory in prediction_runs(EXPERIMENTS):
        path = directory / "summary.json"
        summary = json.loads(path.read_text())
        manifest = json.loads((path.parent / "manifest.json").read_text())
        result["usage"][path.parent.name] = {key: summary.get(key) for key in (
            "attempted", "model_attempts", "known_tokens", "unknown_usage_attempts", "mean_total_seconds", "peak_allocated_bytes")}
        result["usage"][path.parent.name].update(model=manifest["config"]["model"]["model"],
            arm=manifest["config"]["arm"], split=manifest["config"]["split"],
            predictions_sha256=sha256(path.parent / "predictions.jsonl"))
    result["total_inference_requests"] = sum(r["model_attempts"] for r in result["usage"].values())
    result["known_inference_tokens"] = sum(r["known_tokens"] for r in result["usage"].values())
    result["unknown_usage_attempts"] = sum(r["unknown_usage_attempts"] for r in result["usage"].values())
    result["training_wall_seconds"] = sum(r["metrics"]["train_runtime"] for r in result["training"].values())
    verified_adapters = {}
    epoch = result["selection"]["selected_epoch"]
    for seed in (17, 29):
        config = json.loads((EXPERIMENTS / f"planner-formal-lora{seed}-test/manifest.json").read_text())["config"]
        directory = ROOT / f"output/training/formal-seed{seed}/checkpoint-{epoch*75}"
        digests = {name: sha256(directory / name) for name in config["model"]["adapter"]["files"]}
        if digests != config["model"]["adapter"]["files"]:
            raise ValueError("A downloaded adapter differs from the evaluated checkpoint")
        verified_adapters[str(seed)] = digests
    result["verified_adapter_files"] = verified_adapters
    write_json(EVALUATIONS / "planner_formal_results.json", result)
    print(json.dumps({"status": "completed", "output": "project_notes/evaluations/planner_formal_results.json",
                      "inference_requests": result["total_inference_requests"]}))


if __name__ == "__main__":
    main()
