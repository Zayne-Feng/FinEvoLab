"""Join fixed-plan acceptance, isolated reviews, and paired usage records."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.runtime import fingerprint, read_json, write_json


def summarize(folder: Path) -> dict:
    manifest = read_json(folder / "manifest.json")
    inputs = read_json(ROOT / "data/evaluation/analysis_extensions/cases.json")
    records = read_json(folder / "model_records.json")
    reviews = read_json(folder / "blind_reviews.json")
    samples = read_json(folder / "blind_samples.json")
    from scripts.review_analysis_extensions import samples_for
    generated, identities_map, controls_list = samples_for(records, inputs)
    if samples != {"samples": generated, "identities": identities_map, "controls": controls_list}:
        raise ValueError("Review samples differ from the recorded answers and frozen evidence")
    if hashlib.sha256((ROOT / "data/evaluation/analysis_extensions/cases.json").read_bytes()).hexdigest() != manifest["input_sha256"]:
        raise ValueError("The frozen input identity changed")
    arms = ("baseline", "encoding", "methods", "combined")
    expected = {(case["id"], repeat, arm) for case in inputs["model_cases"]
                for repeat in range(inputs["repeats"]) for arm in arms}
    identities = [(r["case_id"], r["repeat"], r["arm"]) for r in records]
    if set(identities) != expected or len(identities) != len(expected):
        raise ValueError("Analysis records must cover the complete paired schedule exactly once")
    if any(r["model"] != manifest["model"] for r in records):
        raise ValueError("Analysis model identities differ")
    eligible = {s["id"] for s in samples["samples"]}
    controls = {c["id"]: c["expected"] for c in samples["controls"]}
    judgments = {}
    calibrated = True
    batch_keys = []
    for batch in reviews:
        batch_keys.append((batch["reviewer"], batch["batch"]))
        if batch["model"] != manifest["model"] or batch["reviewer"] not in {"A", "B"}:
            raise ValueError("Review model or context identity differs")
        decisions = {d["id"]: d for d in batch["decisions"]}
        if len(decisions) != len(batch["decisions"]):
            raise ValueError("Duplicate review decision")
        control_pass = all(identifier in decisions and
            decisions[identifier]["supported"] == value and decisions[identifier]["complete"] == value
            for identifier, value in controls.items())
        calibrated = calibrated and control_pass and batch["controls_passed"]
        for identifier, decision in decisions.items():
            if identifier in controls:
                continue
            if identifier not in eligible:
                raise ValueError("An unexpected answer was reviewed")
            target = judgments.setdefault(identifier, {})
            if batch["reviewer"] in target:
                raise ValueError("An answer was reviewed twice in the same context")
            target[batch["reviewer"]] = decision
    if len(set(batch_keys)) != len(batch_keys):
        raise ValueError("Duplicate review batch")
    if set(judgments) != eligible or any(set(value) != {"A", "B"} for value in judgments.values()):
        raise ValueError("Every eligible answer requires both review contexts")
    rows, disagreements = [], 0
    for record in records:
        identity = {key: record[key] for key in ("case_id", "repeat", "arm")}
        identifier = fingerprint(identity)[:20]
        decisions = judgments.get(identifier, {})
        supported = len(decisions) == 2 and all(d["supported"] for d in decisions.values())
        complete = len(decisions) == 2 and all(d["complete"] for d in decisions.values())
        disagreement = len(decisions) == 2 and len({(d["supported"], d["complete"]) for d in decisions.values()}) > 1
        disagreements += int(disagreement)
        rows.append({**identity, "mechanical_passed": record["mechanical_passed"],
            "both_supported": supported, "both_complete": complete,
            "strict_passed": calibrated and record["mechanical_passed"] and supported and complete,
            "review_disagreement": disagreement})
    grouped = {}
    for arm in arms:
        selected = [r for r in rows if r["arm"] == arm]
        baseline = {(r["case_id"], r["repeat"]): r for r in rows if r["arm"] == "baseline"}
        grouped[arm] = {"tasks": len(selected), "mechanical_passed": sum(r["mechanical_passed"] for r in selected),
            "both_supported": sum(r["both_supported"] for r in selected),
            "strict_passed": sum(r["strict_passed"] for r in selected),
            "strict_pair_wins": sum(r["strict_passed"] and not baseline[r["case_id"], r["repeat"]]["strict_passed"] for r in selected),
            "strict_pair_losses": sum(not r["strict_passed"] and baseline[r["case_id"], r["repeat"]]["strict_passed"] for r in selected),
            "cases": {case["id"]: {"tasks": inputs["repeats"],
                "strict_passed": sum(r["strict_passed"] for r in selected if r["case_id"] == case["id"])} for case in inputs["model_cases"]}}
    model_usage = {key: sum(r["usage"].get(key, 0) for r in records)
                   for key in ("requests", "input_tokens", "output_tokens")}
    review_usage = {"requests": len(reviews),
        "input_tokens": sum((r["usage"] or {}).get("prompt_tokens", 0) for r in reviews),
        "output_tokens": sum((r["usage"] or {}).get("completion_tokens", 0) for r in reviews)}
    return {"run_id": manifest["run_id"], "model": manifest["model"],
        "scope": "Four fixed questions with two repeats; descriptive engineering ablation",
        "strict_definition": "Mechanical acceptance and support/completeness agreement in both calibrated review contexts",
        "review_contexts": "Two isolated requests to the same model family",
        "controls_passed": calibrated, "review_disagreements": disagreements, "reviewed_answers": len(eligible),
        "arms": grouped, "rows": rows, "analysis_usage": model_usage, "review_usage": review_usage,
        "failures": dict(Counter(r.get("error", "incomplete_status") for r in records if not r["mechanical_passed"])),
        "input_sha256": manifest["input_sha256"], "source_patch_sha256": manifest["source_patch_sha256"],
        "artifact_sha256": {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in
            ("manifest.json", "model_records.json", "blind_samples.json", "blind_reviews.json")},
        "summarizer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.run)
    write_json(args.run / "assessment.json", result)
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
