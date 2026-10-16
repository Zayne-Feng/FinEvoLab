"""Optimize prompt components on a development and validation pilot."""

from uuid import uuid4
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gepa
from finevolab.optimization import FinancialGEPAAdapter, expand, flatten, select_validation_candidate
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json
from finevolab.config import ModelConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=int, default=12)
    parser.add_argument("--scope", choices=("revenue_growth_pilot", "structured_benchmark"), default="revenue_growth_pilot")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "data/evaluation")
    parser.add_argument("--config", type=Path, default=ROOT / "finrobot_equity/core/config/config.ini")
    args = parser.parse_args()
    if not 6 <= args.budget <= 200:
        raise ValueError("The task evaluation budget must be between 6 and 200")
    structured = args.scope == "structured_benchmark"
    prefix = "" if structured else "pilot_"
    development = json.loads((args.dataset_dir / f"{prefix}development.json").read_text())
    validation = json.loads((args.dataset_dir / f"{prefix}validation.json").read_text())
    if structured:
        frozen = json.loads((args.dataset_dir / "manifest.json").read_text())
        if not frozen.get("status", "").startswith("frozen_structured_"):
            raise ValueError("A frozen structured benchmark is required")
        for name in ("development", "validation"):
            if sha256(args.dataset_dir / f"{name}.json") != frozen["split_sha256"][name]:
                raise ValueError("A frozen split has changed")
        for payload in (development, validation):
            cases = payload["tasks"]
            if args.pilot:
                cases = [next(case for case in cases if case["category"] == category)
                         for category in ("metric_query", "annual_growth", "company_comparison", "integrated_analysis", "boundary")]
            payload["cases"] = cases
    run_id = uuid4().hex[:12] + "-gepa-" + ("pilot" if args.pilot or not structured else "benchmark")
    directory = ROOT / "project_notes/optimization" / run_id
    adapter = FinancialGEPAAdapter(ROOT, directory, settings=ModelConfig.load(args.config), scope=args.scope, task_budget=args.budget)
    if development["data_version"] != adapter.store.version or validation["data_version"] != adapter.store.version:
        raise ValueError("Evaluation data version differs from the current snapshot")
    prompt_path = ROOT / ("prompts/benchmark.json" if structured else "prompts/default.json")
    seed = flatten(json.loads(prompt_path.read_text()))
    write_json(directory / "manifest.json", manifest(ROOT, run_id, {
        "scope": args.scope, "pilot": args.pilot, "task_budget": args.budget,
        "model": adapter.settings.model, "base_url": adapter.settings.base_url,
        "data_version": adapter.store.version, "seed_prompt_sha256": sha256(prompt_path),
        "dataset_manifest_sha256": sha256(args.dataset_dir / "manifest.json") if structured else None,
        "development_ids": [case["id"] for case in development["cases"]],
        "validation_ids": [case["id"] for case in validation["cases"]],
        "test_feedback_used": False, "answer_cache": False, "seed": 17}))
    try:
        result = gepa.optimize(seed_candidate=seed, trainset=development["cases"], valset=validation["cases"],
                               adapter=adapter, max_metric_calls=args.budget, reflection_minibatch_size=1,
                               skip_perfect_score=False, acceptance_criterion="improvement_or_equal",
                               cache_evaluation=False, run_dir=str(ROOT / "output/optimization" / run_id),
                               stop_callbacks=lambda state: adapter.calls + len(validation["cases"]) + 2 > args.budget,
                               display_progress_bar=False, use_cloudpickle=False, seed=17)
    except (Exception, KeyboardInterrupt) as error:
        summary = {"run_id": run_id, "scope": args.scope,
                   "status": "stopped_after_provider_failures" if adapter.infrastructure_failures >= 3 else "failed",
                   "error_type": type(error).__name__, "task_evaluations": adapter.calls,
                   "reflection_calls": adapter.reflection_calls, "default_promoted": False}
        (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary))
        raise SystemExit(1) from None
    records = [json.loads(line) for line in (directory / "evaluations.jsonl").read_text().splitlines()]
    try:
        selected = select_validation_candidate(records, {case["id"] for case in validation["cases"]})
    except ValueError:
        write_json(directory / "summary.json", {"run_id": run_id, "status": "no_valid_validation_selection",
            "task_evaluations": adapter.calls, "reflection_calls": adapter.reflection_calls, "default_promoted": False})
        raise SystemExit(1) from None
    selected_prompt = json.loads((ROOT / "prompts/candidates" / (selected["candidate"] + ".json")).read_text())
    summary = {"run_id": run_id, "scope": args.scope, "status": "complete", "task_evaluations": adapter.calls,
               "reflection_calls": adapter.reflection_calls, "result": result.to_dict(),
               "data_version": adapter.store.version, "test_feedback_used": False, "cost": None,
               "selection": selected, "default_promoted": False}
    (directory / "summary.json").write_text(json.dumps(summary, default=str, indent=2) + "\n", encoding="utf-8")
    (directory / "selected_candidate.json").write_text(json.dumps(selected_prompt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": run_id, "task_evaluations": adapter.calls, "reflection_calls": adapter.reflection_calls}, indent=2))


if __name__ == "__main__":
    main()
