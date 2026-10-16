"""Adapt financial tool execution to GEPA without exposing test feedback."""

import asyncio
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time

from gepa.core.adapter import EvaluationBatch

from finevolab.agent import analyze_revenue
from finevolab.config import ModelConfig
from finevolab.tools import FinancialStore
from finevolab.provider import sync_client
from finevolab.evaluation import analyze_task, score_task


COMPONENTS = {"system", "search_reports", "get_financial_metrics", "calculate_metrics", "compare_companies", "build_report"}


def flatten(candidate: dict) -> dict[str, str]:
    return {"system": candidate["system"], **candidate["tools"]}


def expand(candidate: dict[str, str]) -> dict:
    if set(candidate) != COMPONENTS or not all(isinstance(value, str) and value.strip() for value in candidate.values()):
        raise ValueError("Only nonempty prompt and tool-description components may be optimized")
    return {"system": candidate["system"], "tools": {key: candidate[key] for key in candidate if key != "system"}}


def score_output(case: dict, output: dict) -> float:
    answer = output["answer"]
    expected = case["expected"]
    if answer["status"] != expected["status"] or set(answer["evidence_ids"]) != set(expected["evidence_ids"]):
        return 0.0
    if expected["status"] == "complete":
        if abs(Decimal(answer["growth_percent"]) - Decimal(expected["value"])) > Decimal("0.000001"):
            return 0.0
    return 1.0


def select_validation_candidate(records: list[dict], validation_ids: set[str]) -> dict:
    candidates = {}
    for record in records:
        if record["case"]["split"] == "validation" and record["case"]["id"] in validation_ids:
            candidates.setdefault(record["candidate"], {})[record["case"]["id"]] = record
    eligible = []
    for identifier, cases in candidates.items():
        if set(cases) != validation_ids:
            continue
        rows = list(cases.values())
        if any(row.get("execution_status") == "infrastructure_failure" for row in rows):
            continue
        score = sum(row["score"] for row in rows) / len(rows)
        usages = [row["output"].get("usage", {}).get("total_tokens") for row in rows]
        tokens = sum(usages) / len(usages) if all(value is not None for value in usages) else None
        eligible.append({"candidate": identifier, "success_rate": score, "mean_tokens": tokens})
    if not eligible:
        raise ValueError("No candidate has a complete validation evaluation")
    return min(eligible, key=lambda row: (-row["success_rate"], row["mean_tokens"] if row["mean_tokens"] is not None else float("inf"),
                                          row["candidate"]))


class FinancialGEPAAdapter:
    def __init__(self, root: Path, log_directory: Path, settings: ModelConfig | None = None,
                 scope: str = "revenue_growth_pilot", task_budget: int = 200):
        self.root, self.directory = root, log_directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.settings = settings or ModelConfig.load(root / "finrobot_equity/core/config/config.ini")
        self.store = FinancialStore(root / "data/processed/reports.json")
        self.calls = self.reflection_calls = 0
        self.scope, self.task_budget = scope, task_budget
        self.infrastructure_failures = 0

    def append(self, name: str, record: dict) -> None:
        with (self.directory / name).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def evaluate(self, batch, candidate, capture_traces=False):
        nested = expand(candidate)
        identifier = hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest()[:20]
        candidate_path = self.root / "prompts/candidates" / (identifier + ".json")
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(json.dumps(nested, indent=2) + "\n", encoding="utf-8")
        outputs, scores, traces = [], [], []
        for case in batch:
            if case["split"] not in {"development", "validation"}:
                raise ValueError("Test cases cannot enter the optimizer")
            if self.calls >= self.task_budget:
                raise RuntimeError("The task evaluation budget is exhausted")
            events, started, telemetry = [], time.monotonic(), {}
            infrastructure_failure = False
            try:
                if self.scope == "structured_benchmark":
                    output = asyncio.run(asyncio.wait_for(analyze_task(
                        self.settings, self.store, case["question"], nested, events, telemetry=telemetry), timeout=180))
                    assessment = score_task(case, output)
                    score = assessment["score"]
                    feedback = json.dumps(assessment)
                else:
                    output = asyncio.run(analyze_revenue(self.settings, self.store, case["company"],
                                                        case["fiscal_year"], events, nested))
                    score = score_output(case, output)
                    feedback = "All numerical, period, status, and citation checks passed." if score else "A required output check failed."
            except Exception as error:
                output = {"error_type": type(error).__name__, "status_code": getattr(error, "status_code", None),
                          "events": events, "partial_telemetry": telemetry}
                provider_code = str(getattr(error, "code", ""))
                if provider_code.isdecimal():
                    output["provider_error_code"] = provider_code
                score, feedback = 0.0, "Execution failed: " + type(error).__name__
                infrastructure_failure = getattr(error, "status_code", None) in {429, 500, 502, 503, 504} or type(error).__name__ in {
                    "APITimeoutError", "APIConnectionError", "TimeoutError"}
            self.infrastructure_failures = self.infrastructure_failures + 1 if infrastructure_failure else 0
            self.calls += 1
            record = {"evaluation": self.calls, "candidate": identifier, "case": case,
                      "score": score, "feedback": feedback, "output": output,
                      "elapsed_seconds": round(time.monotonic() - started, 3), "cost": None,
                      "data_version": self.store.version,
                      "execution_status": "infrastructure_failure" if infrastructure_failure else
                          "execution_failure" if "error_type" in output else "completed"}
            self.append("evaluations.jsonl", record)
            outputs.append(output)
            scores.append(score)
            traces.append(record)
            print(json.dumps({"evaluation": self.calls, "case": case["id"], "score": score}), flush=True)
            if self.infrastructure_failures >= 3:
                raise ConnectionError("Consecutive provider failures reached the experiment threshold")
        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=traces if capture_traces else None)

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        rows = []
        for trace in eval_batch.trajectories or []:
            if trace["case"]["split"] != "development":
                raise ValueError("Only development traces may generate proposals")
            if trace.get("execution_status") == "infrastructure_failure":
                continue
            rows.append({"Inputs": {key: value for key, value in trace["case"].items() if key != "expected"},
                         "Generated Outputs": trace["output"], "Feedback": trace["feedback"]})
        return {component: rows for component in components_to_update}

    def propose_new_texts(self, candidate, reflective_dataset, components_to_update):
        if not set(components_to_update) <= COMPONENTS:
            raise ValueError("Unsupported optimization component")
        if not any(reflective_dataset.get(component) for component in components_to_update):
            return {key: candidate[key] for key in components_to_update}
        self.reflection_calls += 1
        self.append("reflections.jsonl", {"reflection": self.reflection_calls, "status": "started"})
        try:
            with sync_client(self.settings) as client:
                response = client.chat.completions.create(
                    model=self.settings.model, max_tokens=3000, response_format={"type": "json_object"},
                    extra_body=self.settings.extra_body,
                    messages=[{"role": "system", "content":
                           "Improve only the requested financial agent prompt components from development feedback. "
                           "Preserve exact numerical tools and source citations. Be concise. Return a JSON object mapping "
                           "only the requested component names to revised English instruction strings."},
                          {"role": "user", "content": json.dumps({"candidate": candidate,
                           "components_to_update": components_to_update, "feedback": reflective_dataset})}],
                )
        except Exception as error:
            self.append("reflections.jsonl", {"reflection": self.reflection_calls, "status": "failed",
                                              "error_type": type(error).__name__, "status_code": getattr(error, "status_code", None),
                                              "usage": None})
            raise
        text = response.choices[0].message.content or ""
        try:
            proposed = json.loads(text)
            if set(proposed) != set(components_to_update):
                raise ValueError("Invalid proposal keys")
            expand({**candidate, **proposed})
        except (ValueError, TypeError):
            self.append("reflections.jsonl", {"reflection": self.reflection_calls, "status": "invalid",
                                              "output": text, "usage": response.usage.model_dump() if response.usage else None})
            return {key: candidate[key] for key in components_to_update}
        self.append("reflections.jsonl", {"reflection": self.reflection_calls, "status": "valid", "proposal": proposed,
                                          "usage": response.usage.model_dump() if response.usage else None})
        return proposed
