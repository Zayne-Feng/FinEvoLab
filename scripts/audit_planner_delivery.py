"""Verify retained planner identities, paired runs, live tasks, and usage reconciliation."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.planning import PlannerClient
from finevolab.runtime import write_json


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main():
    quant = read("project_notes/evaluations/planner_quantization.json")
    deployment = read("project_notes/run_logs/planner_deployment_checks.json")
    web = read("project_notes/run_logs/planner_web_live.json")
    browser = read("project_notes/run_logs/planner_browser_checks.json")
    ledger_path = ROOT / "project_notes/run_logs/planner_service_requests.jsonl"
    ledger = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    if quant["status"] != "completed" or quant["cases"] != 240:
        raise ValueError("The paired precision comparison is incomplete")
    if deployment["status"] != "passed" or len(deployment["tasks"]) != 14:
        raise ValueError("The live deployment grid is incomplete")
    if web["status"] != "passed" or web["tasks"] != 2 or web["errors"]:
        raise ValueError("The live frontend verification is incomplete")
    if browser["status"] != "passed" or browser["console_errors"]:
        raise ValueError("The rendered browser verification is incomplete")
    by_id = {row["request_id"]: row for row in ledger}
    if len(by_id) != len(ledger):
        raise ValueError("Duplicate planner generation identities")
    client = PlannerClient()
    for row in ledger:
        if row["identity"] != client.identity(row["identity"]["backend"]):
            raise ValueError("A served planner identity differs")
        usage = row["usage"]
        if usage["requests"] != 1 or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
            raise ValueError("Invalid service token accounting")
    tasks = {row["task_id"] for row in deployment["tasks"]} | set(browser["task_ids"])
    tasks.update(web["task_ids"])
    narrative_requests = narrative_tokens = 0
    observed_ids = set()
    for task_id in sorted(tasks):
        state = read(f"output/tasks/{task_id}/state.json")
        if state["status"] != "complete":
            raise ValueError("A retained live task is incomplete")
        output = state["phases"]["analyze"]["result"]
        planner = output["planner"]
        if planner["service_request_id"] not in by_id:
            raise ValueError("A live task lacks a real generation record")
        observed_ids.add(planner["service_request_id"])
        if state["versions"]["planner_backend"] != planner["backend"]:
            raise ValueError("Web routing differs from the actual model")
        for name, digest in state["versions"]["sources"].items():
            if sha256(ROOT / "finevolab" / name) != digest:
                raise ValueError("The verified workflow source changed")
        roles = {row["role"]: row["usage"] for row in output["model_usage"]}
        if roles["planner"] != by_id[planner["service_request_id"]]["usage"]:
            raise ValueError("Planner role usage differs from the server ledger")
        narrative_requests += roles["narrative"]["requests"]
        narrative_tokens += roles["narrative"]["total_tokens"]
        for field in ("requests", "input_tokens", "output_tokens", "total_tokens"):
            if output["usage"][field] != sum(value[field] for value in roles.values()):
                raise ValueError("Task usage does not equal its model roles")
    if observed_ids != set(by_id):
        raise ValueError("The model service ledger contains unaccounted generations")
    frozen = ROOT / "data/training/planner_formal/frozen_v1"
    for name, digest in read("data/training/planner_formal/frozen_v1/manifest.json")["files"].items():
        if sha256(frozen / name) != digest:
            raise ValueError("The frozen dataset changed")
    weights = {}
    for seed in (17, 29):
        directory = ROOT / f"output/training/formal-seed{seed}/checkpoint-75"
        files = {path.name: sha256(path) for path in directory.iterdir() if path.is_file()}
        benchmark = read(f"project_notes/experiments/planner-formal-lora{seed}-test/manifest.json")
        if files != benchmark["config"]["model"]["adapter"]["files"]:
            raise ValueError("An exported adapter differs from the formal test")
        weights[str(seed)] = files
    generation_tokens = 0
    for mode in ("none", "nf4"):
        directory = ROOT / f"project_notes/experiments/planner-quant-{mode}-v1"
        scored = [json.loads(line)["attempts"][0] for line in (directory / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
        warmups = json.loads((directory / "warmups.json").read_text(encoding="utf-8"))["attempts"]
        generation_tokens += sum(row["usage"]["total_tokens"] for row in scored + warmups)
    result = {**manifest(ROOT, "planner-deployment-delivery", {}), "status": "passed",
        "quantization_scored_generations": 480, "quantization_warmups": 6,
        "service_generations": len(ledger), "retained_tasks": len(tasks),
        "gpu_generations": 486 + len(ledger),
        "gpu_tokens": generation_tokens + sum(row["usage"]["total_tokens"] for row in ledger),
        "api_narrative_requests": narrative_requests, "api_narrative_tokens": narrative_tokens,
        "business_passes": deployment["business_passes"], "business_attempts": 14,
        "weights": weights, "service_ledger_sha256": sha256(ledger_path),
        "fmp_requests": 0, "unknown_new_usage": 0,
        "evidence": {name: sha256(ROOT / name) for name in (
            "project_notes/evaluations/planner_quantization.json",
            "project_notes/run_logs/planner_deployment_checks.json",
            "project_notes/run_logs/planner_web_live.json",
            "project_notes/run_logs/planner_browser_checks.json")}}
    write_json(ROOT / "project_notes/run_logs/planner_delivery_audit.json", result)
    print(json.dumps({key: result[key] for key in ("status", "gpu_generations", "gpu_tokens",
        "retained_tasks", "api_narrative_requests", "api_narrative_tokens", "business_passes")}))


if __name__ == "__main__":
    main()
