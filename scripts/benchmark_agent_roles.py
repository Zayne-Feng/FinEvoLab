"""Compare analyst, self-check, and isolated-review workflows under one budget."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.config import ModelConfig
from finevolab.experience_data import public_result
from finevolab.runtime import RunBudget, ToolExecutor, fingerprint
from finevolab.scope_evaluation import assess_scope, task_store
from finevolab.workflow import run_question
from benchmark_retrieval_services import configuration
from benchmark_runtime_refinement import review
from service_experiment_utils import digest, initialize, write


class UncachedExecutor(ToolExecutor):
    def __init__(self, budget, events, version, cache_root=None):
        super().__init__(budget, events, version, None)


async def main(args):
    dataset_path = ROOT / "data/evaluation/service_agents/validation.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if digest(ROOT / dataset["source"]) != dataset["source_sha256"]:
        raise ValueError("Frozen validation source changed")
    settings = ModelConfig.load()
    candidate = json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8"))
    config = {"dataset_sha256": digest(dataset_path), "model": settings.model, "base_url": settings.base_url,
        "thinking": settings.extra_body, "arms": {"S": "off", "R": "self_check", "M": "reviewer"},
        "repeats": 1, "budget": {"model_calls": 6, "tool_calls": 10, "tokens": 20000, "seconds": 240},
        "temperature": 0, "max_tokens": 1600, "retrieval_arm": "D", "model_output_cache": False,
        "deterministic_tool_cache": False, "arm_order": "rotate by question index",
        "source_tasks": dataset["independent_source_tasks"], "requests_per_arm": len(dataset["tasks"])}
    initialize(args.output, config, ["scripts/benchmark_agent_roles.py", "scripts/benchmark_retrieval_services.py",
        "scripts/benchmark_runtime_refinement.py", "scripts/review_closure_answers.py", dataset_path.relative_to(ROOT).as_posix()])
    if (args.output / "summary.json").exists():
        print(json.dumps({"status": "already_completed", "new_model_requests": 0}))
        return
    configuration("D", args.output.name)
    documents = json.loads((ROOT / "data/evaluation/scope_v3/documents.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in dataset["tasks"]}
    stores = {tuple(case["upload_ids"]): task_store(ROOT, case, documents) for case in cases.values()}
    records_path = args.output / "records.json"
    rows = json.loads(records_path.read_text(encoding="utf-8")) if records_path.exists() else []
    done = {(row["case_id"], row["arm"]) for row in rows}
    for index, case in enumerate(cases.values()):
        arms = list(config["arms"].items())
        arms = arms[index % 3:] + arms[:index % 3]
        for arm, mode in arms:
            if (case["id"], arm) in done:
                continue
            task_id = "roles" + fingerprint({"run": args.output.name, "case": case["id"], "arm": arm})[:24]
            telemetry = {}
            row = {"case_id": case["id"], "arm": arm, "task_id": task_id, "language": case["language"],
                   "source_case_id": case["source_case_id"]}
            started = time.perf_counter()
            try:
                with patch("finevolab.grounded.ToolExecutor", UncachedExecutor):
                    state = await run_question(task_id, case["question"], settings, candidate,
                        upload_ids=case["upload_ids"], review_mode=mode, telemetry=telemetry,
                        timeout_seconds=240, budget=RunBudget(max_context_chars=60000, timeout_seconds=240))
                output = dict(state["phases"]["analyze"]["result"])
                output.pop("messages", None)
                row.update(output=output, **assess_scope(ROOT, stores[tuple(case["upload_ids"])], case, output),
                    execution_status="completed", phases=public_result(state)["phases"], operations=public_result(state)["operations"])
            except Exception as error:
                row.update(score=0, checks={}, execution_status="failed", error_type=type(error).__name__,
                           partial_usage=telemetry.get("usage", {}))
                if not isinstance(error, (ValueError, KeyError, TypeError, TimeoutError)):
                    write(args.output / "interruption.json", row)
                    raise
            row["elapsed_seconds"] = time.perf_counter() - started
            rows.append(row)
            write(records_path, rows)
            print(json.dumps({"completed": len(rows), "planned": len(cases)*3, "case_id": case["id"],
                "arm": arm, "mechanical_score": row["score"], "review": row.get("output",{}).get("review",{}).get("status")}),flush=True)
    calibration = await review(settings, rows, cases, args.output)
    write(records_path, rows)
    groups = {}
    for arm in config["arms"]:
        subset = [row for row in rows if row["arm"] == arm]
        def usage(row): return row.get("output",{}).get("usage",row.get("partial_usage",{}))
        groups[arm] = {"tasks":len(subset), "mechanical_successes":sum(r["score"] for r in subset),
            "strict_successes":sum(r["strict_score"] for r in subset),
            "requests":sum(usage(r).get("requests",0) for r in subset),
            "total_tokens":sum(usage(r).get("total_tokens",0) for r in subset),
            "mean_seconds":sum(r["elapsed_seconds"] for r in subset)/len(subset),
            "repairs":sum(r.get("output",{}).get("repair",{}).get("count",0) for r in subset),
            "review_rejections":sum(r.get("output",{}).get("review",{}).get("status")=="rejected" for r in subset)}
    numeric = []
    for case in cases.values():
        if not case["operation"].startswith("hybrid"):continue
        outputs = [next(r for r in rows if r["case_id"]==case["id"] and r["arm"]==arm).get("output",{}).get("answer") for arm in config["arms"]]
        numeric.append(all(output is not None and output==outputs[0] for output in outputs))
    write(args.output / "summary.json", {"status":"completed","groups":groups,"review":calibration,
        "numeric_questions":len(numeric),"numeric_answers_equal":sum(numeric),
        "scope":"24 bilingual requests from 12 validation tasks; one run per arm; same model and total budget"})
    print(json.dumps({"status":"completed","groups":groups}),flush=True)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    asyncio.run(main(parser.parse_args()))
