"""Optimize request guidance using development-only GEPA task feedback."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gepa
from finevolab.config import ModelConfig
from finevolab.experiments import manifest, sha256
from finevolab.optimization import FinancialGEPAAdapter, flatten, select_validation_candidate
from finevolab.runtime import write_json


class CompactGEPAAdapter(FinancialGEPAAdapter):
    def propose_new_texts(self, candidate, reflective_dataset, components_to_update):
        from finevolab.grounded import PLAN_PROMPT
        enriched = {name: list(rows) + [{"Inputs": {"fixed_request_contract": PLAN_PROMPT},
            "Generated Outputs": {}, "Feedback":
                "Minimize redundant guidance while preserving every successful numerical and status check. "
                "The immutable request contract and JSON schema are already supplied separately. The application "
                "executes deterministic tools and assembles final answers. Rewrite the requested system guidance "
                "in at most 60 English words. Remove final answer schema and repeated calculation instructions. "
                "Do not change scope or state semantics. Equal accuracy is selected by lower measured Tokens."}]
                    for name, rows in reflective_dataset.items()}
        return super().propose_new_texts(candidate, enriched, components_to_update)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--budget", type=int, default=48)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    data = ROOT / "data/evaluation/closure"
    frozen = json.loads((data / "manifest.json").read_text())
    if sha256(data / "development.json") != frozen["split_sha256"]["development"]:
        raise ValueError("Frozen development bytes changed")
    cases = [case for case in json.loads((data / "development.json").read_text())["tasks"]
             if case["category"] != "explanation"]
    train = [{**case, "split": "development"} for case in cases[::2]]
    selection = [{**case, "split": "validation"} for case in cases[1::2]]
    directory = ROOT / "project_notes/optimization" / args.run_id
    if (directory / "manifest.json").exists():
        raise ValueError("An optimization run cannot overwrite its evidence")
    settings = ModelConfig.load()
    adapter_class = CompactGEPAAdapter if args.compact else FinancialGEPAAdapter
    adapter = adapter_class(ROOT, directory, settings, scope="structured_benchmark", task_budget=args.budget)
    seed = flatten(json.loads((ROOT / "prompts/benchmark.json").read_text()))
    write_json(directory / "manifest.json", manifest(ROOT, args.run_id, {
        "scope": "grounded_request_guidance", "task_budget": args.budget,
        "compact_guidance": args.compact, "accuracy_scorer_changed": False,
        "dataset_sha256": sha256(data / "manifest.json"), "model": settings.model,
        "thinking": settings.extra_body, "reflection_ids": [case["id"] for case in train],
        "selection_ids": [case["id"] for case in selection], "selection_source": "development_only",
        "acceptance_feedback_used": False, "fixed_contract": "finevolab.grounded.PLAN_PROMPT_and_assembly",
        "answer_cache": False}))
    result = gepa.optimize(seed_candidate=seed, trainset=train, valset=selection, adapter=adapter,
        max_metric_calls=args.budget, reflection_minibatch_size=2, skip_perfect_score=False,
        module_selector=lambda state, trajectories, scores, index, candidate: ["system"],
        acceptance_criterion="improvement_or_equal", cache_evaluation=False,
        run_dir=str(ROOT / "output/optimization" / args.run_id), display_progress_bar=False,
        use_cloudpickle=False, seed=17,
        stop_callbacks=lambda state: adapter.calls + len(selection) + 3 > args.budget)
    rows = [json.loads(line) for line in (directory / "evaluations.jsonl").read_text().splitlines()]
    selected = select_validation_candidate(rows, {case["id"] for case in selection})
    prompt = json.loads((ROOT / "prompts/candidates" / (selected["candidate"] + ".json")).read_text())
    write_json(directory / "selected_candidate.json", prompt)
    write_json(directory / "summary.json", {"run_id": args.run_id, "status": "complete",
        "completed_at": datetime.now(timezone.utc).isoformat(), "task_evaluations": adapter.calls,
        "reflection_calls": adapter.reflection_calls, "selection": selected,
        "result": result.to_dict(), "acceptance_feedback_used": False, "default_promoted": False})
    print(json.dumps({"status": "complete", "task_evaluations": adapter.calls,
                      "reflection_calls": adapter.reflection_calls, "selection": selected}))


if __name__ == "__main__":
    main()
