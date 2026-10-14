"""Prepare development-only prose anchors and independent rating material."""

from uuid import uuid4
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json


def main():
    path = ROOT / "data/evaluation/v2/development.json"
    tasks = json.loads(path.read_text())["tasks"]
    rubric_path = ROOT / "configs/open_text_rubric.json"
    rubric = json.loads(rubric_path.read_text())
    samples = []
    for case in tasks:
        if case["category"] != "integrated_analysis":
            continue
        values = case["expected"]
        metrics = [values[name] for name in ("revenue", "operating_income", "net_income")]
        numbers = "; ".join(f"{item['name']} = {item['value']} {item['unit']} [{item['evidence_id']}]" for item in metrics)
        period = f"{metrics[0]['period_start']} to {metrics[0]['period_end']}"
        operating = "below" if int(values["operating_income"]["value"]) < int(values["revenue"]["value"]) else "above"
        net = "below" if int(values["net_income"]["value"]) < int(values["operating_income"]["value"]) else "above"
        variants = {
            "supported": (f"{case['company']} FY{case['fiscal_year']} covers {period}. {numbers}. "
                          f"Operating income is {operating} revenue, while net income is {net} operating income. "
                          "Revenue measures the top line; operating income measures the operating result, and net income the bottom line."),
            "listing": f"{case['company']} FY{case['fiscal_year']}: {numbers}.",
            "unsupported": "Every measure doubled because artificial-intelligence demand surged. The stock will rise next year."}
        for name, answer in variants.items():
            samples.append({"id": case["id"] + "-" + name, "case_id": case["id"], "split": "development",
                            "question": case["question"], "observations": metrics, "answer": answer,
                            "origin": "synthetic_control_not_model_output"})
    payload = {"rubric_version": rubric["version"], "rubric_sha256": sha256(rubric_path),
               "development_sha256": sha256(path), "samples": samples, "human_calibration": "pending"}
    target = ROOT / "data/evaluation/calibration/controls.json"
    write_json(target, payload)
    lines = ["# Open-text review packet", "", "Use the development-only synthetic controls below to calibrate independent ratings.", "",
             "Two distinct raters assign 0, 1 or 2 to each dimension in configs/open_text_rubric.json.",
             "Compare dimension-level disagreements after independent scoring and record adjudication.", "",
             "Model-generated answers require a separate sampled review before automatic judge scores are used.", "",
             "Rating file format:", "", "```json", '{"rater":"reviewer_name","rubric_version":"open_text_v1","ratings":[',
             '  {"sample_id":"sample_id","scores":{"factual_support":2,"period_and_units":2,"interpretation":2,"citation_support":2}}',
             "]}", "```", ""]
    for sample in samples:
        lines.extend(["## " + sample["id"], "", sample["question"], "", sample["answer"], "",
                      "Source observations:", "", "```json", json.dumps(sample["observations"], indent=2), "```", ""])
    (ROOT / "project_notes/evaluations/open_text_review.md").write_text("\n".join(lines), encoding="utf-8")
    run_id = uuid4().hex[:12] + "-open-text-packet"
    write_json(ROOT / "project_notes/evaluations/open_text_calibration.json", manifest(ROOT, run_id, {
        "status": "human_ratings_pending", "samples": len(samples), "controls_sha256": sha256(target),
        "rubric_sha256": sha256(rubric_path), "test_data_used": False, "model_outputs_reviewed": 0}))
    print(json.dumps({"samples": len(samples), "status": "human_ratings_pending"}))


if __name__ == "__main__":
    main()
