"""Wait for verified exports and render measured planner comparisons and numeric replays."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from filelock import FileLock
from finevolab.experiments import sha256
from finevolab.runtime import write_json


def table(headers, rows):
    return "\n".join(["| " + " | ".join(map(str, headers)) + " |", "| " + " | ".join("---" for _ in headers) + " |",
                      *["| " + " | ".join(map(str, row)) + " |" for row in rows]])


def run(*arguments):
    subprocess.run([sys.executable, *map(str, arguments)], check=True, cwd=ROOT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--report-template", type=Path)
    args = parser.parse_args()
    template_path = args.report_template or ROOT / "project_notes/experiments/planner_formal_report_template.md"
    if args.report_template is not None and not template_path.is_file():
        raise ValueError("The report template does not exist")
    evaluations = ROOT / "project_notes/evaluations"
    completion_path = evaluations / "planner_formal_completion.json"
    while not completion_path.exists():
        if not args.wait:
            raise ValueError("The experiment has not completed")
        print(json.dumps({"status": "waiting_for_verified_exports"}), flush=True)
        time.sleep(45)
    run(ROOT / "scripts/analyze_planner_formal.py")
    replay_summaries = {}
    replay_rows = {}
    for seed in (17, 29):
        output = ROOT / f"project_notes/experiments/planner-formal-replay{seed}"
        run(ROOT / "scripts/replay_planner_predictions.py", "--bundle", ROOT / "data/training/planner_formal/frozen_v1",
            "--split", "test", "--runs", ROOT / "project_notes/experiments/planner-formal-deepseek-test",
            ROOT / "project_notes/experiments/planner-formal-base-test",
            ROOT / f"project_notes/experiments/planner-formal-lora{seed}-test", "--case-ids",
            ROOT / "data/training/planner_formal/replay_case_ids.json", "--output", output)
        replay_summaries[str(seed)] = json.loads((output / "summary.json").read_text())
        replay_rows[str(seed)] = [json.loads(line) for line in (output / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
        identifiers = json.loads((ROOT / "data/training/planner_formal/replay_case_ids.json").read_text())
        expected = {(identifier, arm, 0) for identifier in identifiers for arm in ("deepseek", "qwen_base", "qwen_lora")}
        actual = {(row["case_id"], row["arm"], row["repeat"]) for row in replay_rows[str(seed)]}
        if actual != expected or len(actual) != len(replay_rows[str(seed)]) or any(
            row.get("usage", {}).get("requests", 0) != 0 or not row.get("usage_complete") for row in replay_rows[str(seed)]):
            raise ValueError("The numeric replay grid or zero-call contract differs")
    shared = [{(row["case_id"], row["arm"]): (row["score"], row["checks"]) for row in replay_rows[seed]
               if row["arm"] != "qwen_lora"} for seed in ("17", "29")]
    if shared[0] != shared[1]:
        raise ValueError("Shared numeric controls changed between seed replays")
    result = json.loads((evaluations / "planner_formal_results.json").read_text())
    completion = json.loads(completion_path.read_text())
    rows = []
    for name, seed, arm in (("DeepSeek", "17", "deepseek"), ("Qwen base", "17", "qwen_base"),
                             ("LoRA seed17", "17", "qwen_lora"), ("LoRA seed29", "29", "qwen_lora")):
        value = result["seeds"][seed]["arms"][arm]
        rows.append([name, f"{value['strict_successes']}/240", f"{value['strict_successes']/2.4:.2f}%",
                     f"{value['semantic_successes']}/240", f"{value['schema_valid']}/240"])
    effects = []
    mechanisms = []
    for seed in ("17", "29"):
        for metric, value in result["seeds"][seed]["paired_effects"].items():
            interval = value["interval"]
            effects.append([seed, metric, f"{value['mean_difference']*100:+.2f} pp",
                            f"[{interval[0]*100:.2f}, {interval[1]*100:.2f}] pp",
                            f"{value['wins']}/{value['losses']}/{value['ties']}"])
        value = result["seeds"][seed]["strict_gain_mechanisms"]
        mechanisms.append(f"- Seed {seed}: schema-only {value.get('schema_recovery_only',0)}; "
            f"joint {value.get('schema_and_semantic_recovery',0)}; semantic {value.get('semantic_field_recovery',0)}.")
    replays = []
    for seed in ("17", "29"):
        for arm, value in replay_summaries[seed]["groups"].items():
            selected = [row for row in replay_rows[seed] if row["arm"] == arm]
            strata = {}
            for name, boundary in (("numeric", False), ("boundary", True)):
                subset = [row for row in selected if (row["category"] == "boundary") == boundary]
                strata[name] = {"cases": len(subset), "successes": sum(row["score"] for row in subset)}
            value["strata"] = strata
            replays.append([seed, arm, f"{value['mechanical_successes']}/{value['attempts']}",
                f"{strata['numeric']['successes']}/{strata['numeric']['cases']}",
                f"{strata['boundary']['successes']}/{strata['boundary']['cases']}", value["downstream_requests"]])
    usage = (f"Inference requests: {result['total_inference_requests']}; known tokens: {result['known_inference_tokens']}; "
        f"unknown-usage attempts: {result['unknown_usage_attempts']}. "
        f"Measured training time across two seeds: {result['training_wall_seconds']/60:.2f} min. "
        "GPU wall time also includes preflight, validation, test and export. Invoice charges are not inferred from training time alone.")
    result_path = evaluations / "planner_formal_results.json"
    target = result_path
    report_sha256 = None
    template_sha256 = None
    if template_path.is_file():
        template = template_path.read_text(encoding="utf-8")
        report = template.format(completed_at=completion.get("completed_at", ""), epoch=result["selection"]["selected_epoch"],
            test_table=table(["Model", "Strict", "Strict rate", "Semantic", "Schema"], rows),
            effect_table=table(["Seed", "Metric", "Difference", "95% CI", "Wins/Losses/Ties"], effects),
            mechanisms="\n".join(mechanisms), replay_table=table(["Seed", "Arm", "Overall", "Numeric", "Boundary", "Model calls"], replays), usage=usage)
        target = evaluations / "planner_formal_results.md"
        target.write_text(report, encoding="utf-8")
        report_sha256 = sha256(target)
        template_sha256 = sha256(template_path)
    write_json(evaluations / "planner_formal_finalization.json", {"status": "completed",
        "report_sha256": report_sha256, "analysis_sha256": sha256(ROOT / "scripts/analyze_planner_formal.py"),
        "template_sha256": template_sha256, "results_sha256": sha256(result_path),
        "numeric_replays": replay_summaries})
    print(json.dumps({"status": "completed", "report": str(target)}), flush=True)


if __name__ == "__main__":
    with FileLock(str(ROOT / "output/cloud/planner_formal_finalizer.lock"), timeout=0):
        main()
