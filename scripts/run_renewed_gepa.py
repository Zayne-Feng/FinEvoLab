"""Search request guidance and confirm candidates with repeated paired planning."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gepa
from gepa.core.adapter import EvaluationBatch
from finevolab.config import ModelConfig
from finevolab.experiments import manifest, sha256
from finevolab.grounded import RequestPlan, request_instruction
from finevolab.optimization import expand, flatten, select_validation_candidate
from finevolab.provider import async_client
from finevolab.runtime import write_json
from finevolab.sdk_runtime import generate_json, json_messages
from run_closure_gepa import CompactGEPAAdapter


class PlannerAdapter(CompactGEPAAdapter):
    phase = "search"

    async def plan(self, case, candidate):
        usage = {key: 0 for key in ("requests", "input_tokens", "output_tokens", "total_tokens")}
        def record(response):
            usage["requests"] += 1
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                usage[key] += getattr(response.usage, key)
        try:
            async with async_client(self.settings) as client:
                raw = await asyncio.wait_for(generate_json(client, self.settings,
                    json_messages(RequestPlan, request_instruction(candidate),
                                  {"question": case["question"], "selected_documents": []}), record), timeout=180)
            parsed = RequestPlan.model_validate_json(raw)
            parsed = parsed.model_copy(update={"companies": [self.store.company_id(c) for c in parsed.companies]})
            resolved = parsed
            def checks(plan):
                values = plan.model_dump()
                values["report_year"] = values["report_year"] or values["fiscal_year"]
                return {key: sorted(values[key]) == sorted(value) if isinstance(value, list) else values[key] == value
                        for key, value in case["expected_plan"].items()}
            return {"raw": parsed.model_dump(), "resolved": resolved.model_dump(), "usage": usage,
                    "raw_checks": checks(parsed), "checks": checks(resolved), "usage_complete": True}
        except Exception as error:
            return {"error_type": type(error).__name__, "usage": usage, "usage_complete": False,
                    "checks": {"valid_plan": False}}

    def evaluate(self, batch, candidate, capture_traces=False):
        nested = expand(candidate)
        identifier = hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest()[:20]
        write_json(ROOT / "prompts/candidates" / f"{identifier}.json", nested)
        outputs, scores, traces = [], [], []
        for case in batch:
            if case["split"] not in {"development", "validation"} or self.calls >= self.task_budget:
                raise ValueError("The planning split or budget is invalid")
            started = time.monotonic()
            output = asyncio.run(self.plan(case, nested))
            score = int(all(output["checks"].values()))
            self.calls += 1
            row = {"evaluation": self.calls, "candidate": identifier, "case": case, "phase": self.phase,
                "score": score, "output": output, "feedback": json.dumps(output["checks"]),
                "elapsed_seconds": round(time.monotonic() - started, 4),
                "execution_status": "execution_failure" if "error_type" in output else "completed"}
            self.append("evaluations.jsonl", row)
            outputs.append(output)
            scores.append(score)
            traces.append(row)
            print(json.dumps({"phase": self.phase, "evaluation": self.calls, "case": case["id"], "score": score}), flush=True)
        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=traces if capture_traces else None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--budget", type=int, default=48)
    args = parser.parse_args()
    data = ROOT / "data/evaluation/renewed_v1"
    frozen = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in frozen["files"].items():
        if sha256(data / name) != digest:
            raise ValueError("The frozen dataset changed")
    train, validation = [json.loads((data / f"{split}.json").read_text(encoding="utf-8"))["tasks"]
                         for split in ("development", "validation")]
    directory = ROOT / "project_notes/optimization" / args.run_id
    if directory.exists():
        raise ValueError("The optimization evidence already exists")
    settings = ModelConfig.load()
    adapter = PlannerAdapter(ROOT, directory, settings, task_budget=args.budget)
    seed = flatten(json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8")))
    seed_id = hashlib.sha256(json.dumps(seed, sort_keys=True).encode()).hexdigest()[:20]
    write_json(directory / "manifest.json", manifest(ROOT, args.run_id, {
        "task_budget": args.budget, "confirmation_trials": len(validation) * 6,
        "dataset_manifest_sha256": sha256(data / "manifest.json"), "model": settings.model,
        "thinking": settings.extra_body, "temperature": 0, "max_tokens": 1600,
        "metric": "first-pass normalized RequestPlan fields, not end-to-end success",
        "optimized_components": ["system"], "fixed_contract": "PLAN_PROMPT/schema/tools",
        "acceptance_feedback_used": False, "fmp_requests": 0,
        "code_hashes": {p.relative_to(ROOT).as_posix(): sha256(p) for p in list((ROOT / "finevolab").glob("*.py")) + [Path(__file__)]}}))
    result = gepa.optimize(seed_candidate=seed, trainset=train, valset=validation, adapter=adapter,
        max_metric_calls=args.budget, reflection_minibatch_size=2, skip_perfect_score=False,
        module_selector=lambda state, trajectories, scores, index, candidate: ["system"],
        acceptance_criterion="improvement_or_equal", cache_evaluation=False,
        run_dir=str(ROOT / "output/optimization" / args.run_id), display_progress_bar=False,
        use_cloudpickle=False, seed=17,
        stop_callbacks=lambda state: adapter.calls + len(validation) + 3 > args.budget)
    rows = [json.loads(line) for line in (directory / "evaluations.jsonl").read_text(encoding="utf-8").splitlines()]
    proposals = [row for row in rows if row["candidate"] != seed_id]
    selected = select_validation_candidate(proposals or rows, {case["id"] for case in validation})
    candidate_id = selected["candidate"]
    candidate = flatten(json.loads((ROOT / "prompts/candidates" / f"{candidate_id}.json").read_text(encoding="utf-8")))
    search_calls = adapter.calls
    adapter.phase, adapter.task_budget = "confirmation", adapter.calls + len(validation) * 6
    for repeat in range(3):
        for index, case in enumerate(validation):
            pair = [("seed", seed), ("candidate", candidate)]
            for _, prompt in pair[(index + repeat) % 2:] + pair[:(index + repeat) % 2]:
                adapter.evaluate([case], prompt)
    all_rows = [json.loads(line) for line in (directory / "evaluations.jsonl").read_text(encoding="utf-8").splitlines()]
    confirmation = [row for row in all_rows if row["phase"] == "confirmation"]
    groups = {}
    for identifier in (seed_id, candidate_id):
        values = [row for row in confirmation if row["candidate"] == identifier]
        groups[identifier] = {"attempts": len(values), "successes": sum(r["score"] for r in values),
            "tokens": sum(r["output"]["usage"]["total_tokens"] for r in values),
            "categories": {category: sum(r["score"] for r in values if r["case"]["category"] == category)
                           for category in {r["case"]["category"] for r in values}},
            "unknown_usage": sum(not r["output"]["usage_complete"] for r in values)}
    baseline, challenger = groups[seed_id], groups[candidate_id]
    eligible = (candidate_id != seed_id and not challenger["unknown_usage"]
        and challenger["successes"] >= baseline["successes"]
        and all(challenger["categories"][key] >= value for key, value in baseline["categories"].items())
        and challenger["tokens"] < baseline["tokens"])
    write_json(directory / "selected_candidate.json", expand(candidate if eligible else seed))
    write_json(directory / "challenger.json", expand(candidate))
    write_json(directory / "summary.json", {"status": "completed", "search_evaluations": search_calls,
        "reflection_calls": adapter.reflection_calls, "confirmation": groups,
        "seed_id": seed_id, "candidate_id": candidate_id, "confirmation_passed": eligible,
        "selected": candidate_id if eligible else seed_id, "default_promoted": False,
        "result": result.to_dict(), "acceptance_feedback_used": False})
    print(json.dumps({"search_evaluations": search_calls, "confirmation": groups, "passed": eligible}))


if __name__ == "__main__":
    main()
