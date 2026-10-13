"""Execute and score source-bound annual financial tasks."""

import asyncio
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Literal

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, RunConfig, Runner
from pydantic import BaseModel, ConfigDict

from finevolab.agent import make_tools
from finevolab.config import ModelConfig
from finevolab.ingestion import FinancialMetric
from finevolab.provider import async_client
from finevolab.retrieval import ReportIndex
from finevolab.runtime import BudgetHooks, RunBudget
from finevolab.tools import FinancialStore, GrowthResult


class TaskAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["complete", "insufficient_data", "clarification_required",
                    "periods_not_aligned", "invalid_parameters"]
    answer: str
    metrics: list[FinancialMetric]
    calculations: list[GrowthResult]
    periods_aligned: bool | None
    evidence_ids: list[str]


def reference_answer(case: dict) -> dict:
    if "reference_answer" in case:
        return TaskAnswer.model_validate(case["reference_answer"]).model_dump(mode="json")
    category, expected = case["category"], case["expected"]
    metrics, calculations, aligned = [], [], None
    status = "complete"
    if category == "metric_query":
        metrics = [expected]
    elif category == "annual_growth":
        calculations = [expected]
    elif category == "company_comparison":
        metrics, aligned = expected["values"], expected["periods_aligned"]
    elif category == "integrated_analysis":
        metrics = list(expected.values())
    elif category == "boundary":
        status = expected["status"]
        if case["scenario"] == "fiscal_alignment":
            metrics, aligned = expected["values"], False
    else:
        raise ValueError("Unknown task category")
    identifiers = {item["evidence_id"] for item in metrics}
    identifiers.update(key for item in calculations for key in item["evidence_ids"])
    return TaskAnswer(status=status, answer="Source-bound reference", metrics=metrics,
                      calculations=calculations, periods_aligned=aligned,
                      evidence_ids=sorted(identifiers)).model_dump(mode="json")


def _number_equal(left, right, tolerance=Decimal("0")) -> bool:
    try:
        left, right = Decimal(str(left)), Decimal(str(right))
        return left.is_finite() and right.is_finite() and abs(left - right) <= tolerance
    except (InvalidOperation, ValueError, TypeError):
        return False


def score_task(case: dict, output: dict) -> dict:
    expected = reference_answer(case)
    checks = {name: False for name in ("schema", "status", "numeric", "units", "periods", "citations", "formula")}
    try:
        actual = TaskAnswer.model_validate(output["answer"]).model_dump(mode="json")
    except (ValueError, KeyError, TypeError):
        return {"score": 0.0, "checks": checks, "tool_correct": False}
    checks["schema"] = True
    checks["status"] = actual["status"] == expected["status"]
    def metrics(value):
        items = value["metrics"] + [item for calc in value["calculations"] for item in calc["inputs"]]
        return {(item["company"], item["name"], item["fiscal_year"]): item for item in items}, len(items)
    actual_metrics, actual_count = metrics(actual)
    expected_metrics, expected_count = metrics(expected)
    identities_match = set(actual_metrics) == set(expected_metrics) and actual_count == expected_count
    checks["numeric"] = identities_match and all(_number_equal(actual_metrics[key]["value"], item["value"])
                                                 for key, item in expected_metrics.items())
    checks["units"] = identities_match and all(
        all(actual_metrics[key][field] == item[field] for field in ("currency", "unit", "period_kind"))
        for key, item in expected_metrics.items())
    checks["periods"] = actual["periods_aligned"] == expected["periods_aligned"] and identities_match and all(
        all(actual_metrics[key][field] == item[field] for field in ("period_start", "period_end", "report_year"))
        for key, item in expected_metrics.items())
    checks["citations"] = (set(actual["evidence_ids"]) == set(expected["evidence_ids"])
                           and identities_match and all(actual_metrics[key]["evidence_id"] == item["evidence_id"]
                                                        for key, item in expected_metrics.items()))
    actual_calcs = {(item["company"], item["metric"], item["fiscal_year"]): item for item in actual["calculations"]}
    expected_calcs = {(item["company"], item["metric"], item["fiscal_year"]): item for item in expected["calculations"]}
    checks["formula"] = len(actual["calculations"]) == len(expected["calculations"]) and set(actual_calcs) == set(expected_calcs)
    for key, item in expected_calcs.items():
        found = actual_calcs.get(key, {})
        valid = (found.get("formula") == item["formula"] and found.get("unit") == "percent"
                 and found.get("previous_year") == item["previous_year"]
                 and set(found.get("evidence_ids", [])) == set(item["evidence_ids"]))
        checks["formula"] = checks["formula"] and valid
        checks["numeric"] = checks["numeric"] and _number_equal(found.get("value"), item["value"], Decimal("0.000001"))
    required = {"metric_query": "get_financial_metrics", "annual_growth": "calculate_metrics",
                "company_comparison": "compare_companies", "integrated_analysis": "get_financial_metrics"}
    events = output.get("events", [])
    successful = [event for event in events if event.get("tool") and "error" not in event.get("output", {})]
    returned = {}
    def collect(value):
        if isinstance(value, dict):
            if {"company", "name", "fiscal_year", "value", "evidence_id"} <= set(value):
                returned[(value["company"], value["name"], value["fiscal_year"])] = value
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    for event in successful:
        collect(event.get("output"))
    tool_correct = (bool(successful) and any(event["tool"] == required.get(case["category"]) for event in successful)
                    and all(key in returned and _number_equal(returned[key]["value"], item["value"])
                            and returned[key]["evidence_id"] == item["evidence_id"] for key, item in expected_metrics.items()))
    if case["category"] == "boundary":
        tool_correct = all(checks.values())
    return {"score": float(all(checks.values())), "checks": checks, "tool_correct": tool_correct}


async def analyze_task(settings: ModelConfig, store: FinancialStore, question: str,
                       candidate: dict, events: list | None = None, variant: str = "tools",
                       budget: RunBudget | None = None, telemetry: dict | None = None,
                       root: Path | None = None, corpus_profile: str = "contextual", backend: str = "sdk") -> dict:
    if variant == "tools":
        from finevolab.grounded import analyze_grounded
        options = {"corpus_profile": corpus_profile, "backend": backend}
        if root is not None:
            options["root"] = root
        return await analyze_grounded(settings, store, question, candidate, events, budget, telemetry, **options)
    return await analyze_baseline_task(settings, store, question, candidate, events, variant, budget, telemetry)


async def analyze_baseline_task(settings: ModelConfig, store: FinancialStore, question: str,
                       candidate: dict, events: list | None = None, variant: str = "tools",
                       budget: RunBudget | None = None, telemetry: dict | None = None) -> dict:
    if variant not in {"tools", "context", "rag", "unprotected"}:
        raise ValueError("Unknown task execution variant")
    events = [] if events is None else events
    lifecycle = []
    if telemetry is not None:
        telemetry["lifecycle_events"] = lifecycle
    budget = budget or RunBudget(max_context_chars=60000)
    hooks = BudgetHooks(budget, lifecycle) if variant != "unprotected" else None
    tools = make_tools(store, events, candidate) if variant in {"tools", "unprotected"} else []
    inputs = question
    if variant in {"context", "rag"}:
        identities = sorted({(metric.company, metric.name, metric.fiscal_year) for metric in store.metrics})
        canonical = [store.get_metric(*identity).model_dump(mode="json") for identity in identities]
        if variant == "rag":
            chunks = [{"id": item["evidence_id"], "company": item["company"], "report_year": item["fiscal_year"],
                       "report_type": "annual_metric", "text":
                           f"{item['company']} FY{item['fiscal_year']} {item['name'].replace('_', ' ')}\n" + json.dumps(item)}
                      for item in canonical]
            companies = sorted(set(re.findall(r"\b(?:MSFT|AAPL|GOOGL|AMZN|META|ZZZZ)\b", question)))
            index = ReportIndex(chunks)
            passages = [item for company in companies or [None]
                        for item in index.search(question, company=company, limit=5)]
            inputs += "\nRetrieved source-bound observations:\n" + json.dumps(passages)
        else:
            inputs += "\nAvailable source-bound observations:\n" + json.dumps(canonical)
    async with async_client(settings) as client:
        instructions = candidate["system"] + (" No tools are available; use only supplied source observations." if not tools else "")
        agent = Agent(name="FinancialTaskAnalyst", instructions=instructions, tools=tools,
                      model=OpenAIChatCompletionsModel(model=settings.model, openai_client=client),
                      model_settings=ModelSettings(max_tokens=2200, parallel_tool_calls=False,
                                                   extra_body=settings.extra_body))
        config = RunConfig(tracing_disabled=True)
        result = await Runner.run(agent, inputs, max_turns=6, hooks=hooks, run_config=config)
        finalizer = agent.clone(tools=[], output_type=TaskAnswer,
            instructions=(candidate["system"] + " Return only a JSON data object matching this schema: "
                          + json.dumps(TaskAnswer.model_json_schema())
                          + " Copy full metric objects and calculation objects from the source results. "
                          "Return empty arrays when data is unavailable. Do not include Markdown or a JSON schema."),
            model_settings=ModelSettings(max_tokens=3000, extra_body={
                **settings.extra_body, "response_format": {"type": "json_object"}}))
        final = await Runner.run(finalizer, result.to_input_list() + [{"role": "user", "content":
            "Return the final data object. Include all required fields and no additional fields."}],
            max_turns=1, hooks=hooks, run_config=config)
    usages = [item.context_wrapper.usage for item in (result, final)]
    return {"answer": final.final_output.model_dump(mode="json"), "events": events,
            "lifecycle_events": lifecycle, "data_version": store.version,
            "usage": {field: sum(getattr(item, field) for item in usages)
                      for field in ("requests", "input_tokens", "output_tokens", "total_tokens")}}


def source_audit(root: Path, store: FinancialStore) -> dict:
    documents = {}
    observations = 0
    for report in store.data["reports"]:
        if report["source"].get("profile") != "sec_companyfacts":
            continue
        raw = (root / report["source"]["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != report["source"]["sha256"]:
            raise ValueError("Reference source hash differs from the pinned snapshot")
        document = json.loads(raw)
        documents[report["source"]["sha256"]] = document
    for company in ("MSFT", "AAPL", "GOOGL", "AMZN", "META"):
        for year in (2022, 2023, 2024):
            for name in ("revenue", "operating_income", "net_income"):
                metric = store.get_metric(company, name, year)
                evidence = store.evidence[metric.evidence_id]
                node = documents[evidence["source_sha256"]]
                for part in evidence["locator"].strip("/").split("/"):
                    node = node[int(part)] if isinstance(node, list) else node[part]
                if (not _number_equal(node["val"], metric.value) or node["start"] != metric.period_start
                    or node["end"] != metric.period_end or node["form"] != "10-K" or node["fp"] != "FY"):
                    raise ValueError("Reference observation differs from its SEC source")
                observations += 1
    return {"canonical_observations_verified": observations, "method": "raw_SEC_JSON_pointer_value_and_context"}
