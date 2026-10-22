"""Summarize matched quantization outputs without changing predictions or labels."""

import json
from pathlib import Path
import statistics
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.planner_benchmark import load_bundle, paired_report, summarize_predictions, validate_prediction_rows
from finevolab.runtime import write_json


def main():
    bundle = ROOT / "data/training/planner_formal/frozen_v1"
    cases, contract = load_bundle(bundle, "test")
    records, configurations, groups = {}, {}, {}
    for mode in ("none", "nf4"):
        directory = ROOT / f"project_notes/experiments/planner-quant-{mode}-v1"
        rows = [json.loads(line) for line in (directory / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
        validate_prediction_rows(rows, cases, 1, max_repairs=0)
        config = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["config"]
        records[mode], configurations[mode] = rows, config
        groups[mode] = summarize_predictions(cases, rows, contract["aliases"])
        durations = [r["attempts"][0]["elapsed_seconds"] for r in rows]
        groups[mode]["latency"] = {"mean": statistics.mean(durations), "median": statistics.median(durations),
                                   "p95": float(np.quantile(durations, 0.95))}
        groups[mode]["peak_allocated_gib"] = max(r["attempts"][0]["peak_allocated_bytes"] for r in rows) / 2**30
        groups[mode]["predictions_sha256"] = sha256(directory / "predictions.jsonl")
    if {k:v for k,v in configurations["none"].items() if k != "mode"} != {k:v for k,v in configurations["nf4"].items() if k != "mode"}:
        raise ValueError("Quantization arms differ in more than precision")
    formal_path = ROOT / "project_notes/experiments/planner-formal-lora17-test/predictions.jsonl"
    formal = {r["case_id"]: r for r in (json.loads(line) for line in formal_path.read_text(encoding="utf-8").splitlines())}
    agreement = sum(row["attempts"][0]["raw"] == formal[row["case_id"]]["attempts"][0]["raw"] for row in records["none"])
    result = {"status": "completed", "scope": "post_training_quantization_regression", "cases": len(cases),
        "bundle_sha256": sha256(bundle / "manifest.json"), "groups": groups,
        "strict_pairing": paired_report(cases, records["none"], records["nf4"], contract["aliases"], 1),
        "semantic_pairing": paired_report(cases, records["none"], records["nf4"], contract["aliases"], 1,
                                           metric="semantic_score"),
        "warmups_excluded": 3, "total_model_generations": 486, "execution_order": ["none", "nf4"],
        "bf16_formal_raw_agreement": {"equal": agreement, "cases": len(cases)},
        "memory_reduction": 1 - groups["nf4"]["peak_allocated_gib"] / groups["none"]["peak_allocated_gib"],
        "mean_latency_ratio": groups["nf4"]["latency"]["mean"] / groups["none"]["latency"]["mean"]}
    write_json(ROOT / "project_notes/evaluations/planner_quantization.json", result)
    print(json.dumps({"status": "completed", "groups": {mode: {"strict": value["first"]["successes"],
        "mean_seconds": value["latency"]["mean"], "peak_gib": value["peak_allocated_gib"]} for mode,value in groups.items()}}))


if __name__ == "__main__":
    main()
