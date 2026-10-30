"""Run a financial analyst with deterministic numerical tools."""

import asyncio
from decimal import Decimal, InvalidOperation
from typing import Literal
from pathlib import Path
import json

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, RunConfig, Runner, function_tool
from pydantic import BaseModel, ConfigDict

from finevolab.config import ModelConfig
from finevolab.tools import FinancialStore
from finevolab.derived import DERIVED_METRICS
from finevolab.runtime import BudgetHooks, RunBudget
from finevolab.reporting import build_report as render_report
from finevolab.retrieval import search_corpus
from finevolab.provider import async_client
from finevolab.tool_outputs import SearchReportsOutput, ToolError


class AnalysisAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["complete", "insufficient_data"]
    company: str
    fiscal_year: int
    answer: str
    growth_percent: str
    evidence_ids: list[str]


def make_tools(store: FinancialStore, events: list, candidate: dict):
    root = Path(__file__).resolve().parents[1]
    if candidate is None:
        candidate = json.loads((root / "prompts/default.json").read_text())
    expected_tools = {"search_reports", "get_financial_metrics", "calculate_metrics", "compare_companies", "build_report"}
    if set(candidate) != {"system", "tools"} or set(candidate["tools"]) != expected_tools:
        raise ValueError("Candidate must contain only the system prompt and all tool descriptions")
    if not isinstance(candidate["system"], str) or not candidate["system"].strip():
        raise ValueError("A nonempty system prompt is required")

    @function_tool
    def search_reports(query: str, company: str, fiscal_year: int) -> list[dict]:
        """Retrieve report passages for qualitative evidence; use financial tools for exact numerical values."""
        output = SearchReportsOutput.model_validate(
            search_corpus(root, query, store.company_id(company), fiscal_year)
        ).model_dump(mode="json", exclude_unset=True)
        events.append({"tool": "search_reports", "query": query, "output": output})
        return output

    @function_tool
    def compare_companies(companies: list[str], metric: Literal["revenue", "operating_income", "net_income"], fiscal_year: int) -> dict:
        """Compare annual company metrics while exposing whether their fiscal date ranges align."""
        try:
            output = store.compare(companies, metric, fiscal_year).model_dump(mode="json")
        except ValueError as error:
            output = ToolError(error=str(error)).model_dump(mode="json")
        events.append({"tool": "compare_companies", "output": output})
        return output

    @function_tool
    def build_report(company: str, fiscal_year: int) -> dict:
        """Create an HTML and PDF revenue report with evidence when a report is explicitly requested."""
        try:
            output = render_report(store, company, fiscal_year,
                                   root / "output/agent_reports" / f"{company}_{fiscal_year}")
        except ValueError as error:
            output = ToolError(error=str(error)).model_dump(mode="json")
        events.append({"tool": "build_report", "output": output})
        return output

    @function_tool
    def get_financial_metrics(company: str, metric: Literal["revenue", "operating_income", "net_income"], fiscal_year: int) -> dict:
        """Get a source-bound annual financial metric, using the latest report in the snapshot."""
        try:
            output = store.get_metric(company, metric, fiscal_year).model_dump(mode="json")
        except ValueError as error:
            output = ToolError(error=str(error)).model_dump(mode="json")
        events.append({"tool": "get_financial_metrics", "company": company, "metric": metric,
                       "fiscal_year": fiscal_year, "output": output})
        return output

    @function_tool
    def calculate_metrics(company: str, metric: Literal["revenue", "operating_income", "net_income", "operating_margin",
                          "net_margin", "operating_margin_change", "net_margin_change"], fiscal_year: int) -> dict:
        """Compute annual growth or audited profit margins; margin changes use percentage points."""
        try:
            result = (store.derive(company, metric, fiscal_year) if metric in DERIVED_METRICS
                      else store.growth(company, metric, fiscal_year))
            output = result.model_dump(mode="json")
        except ValueError as error:
            output = ToolError(error=str(error)).model_dump(mode="json")
        events.append({"tool": "calculate_metrics", "company": company, "metric": metric,
                       "fiscal_year": fiscal_year, "output": output})
        return output

    tools = [search_reports, get_financial_metrics, calculate_metrics, compare_companies, build_report]
    for tool in tools:
        description = candidate["tools"][tool.name]
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Tool descriptions must be nonempty strings")
        tool.description = description
        if tool.name == "calculate_metrics":
            tool.description += (" For operating_margin or net_margin, compute annual profit / revenue percent. "
                                 "For operating_margin_change or net_margin_change, return the difference "
                                 "from the previous fiscal year in percentage_points, with field audits.")
    return tools


async def analyze_revenue(settings: ModelConfig, store: FinancialStore, company: str, year: int,
                          events: list | None = None, candidate: dict | None = None) -> dict:
    company = store.company_id(company)
    if events is None:
        events = []
    lifecycle_events = []
    budget = RunBudget()
    hooks = BudgetHooks(budget, lifecycle_events)
    if candidate is None:
        root = Path(__file__).resolve().parents[1]
        candidate = json.loads((root / "prompts/default.json").read_text())
    tools = make_tools(store, events, candidate)
    async with async_client(settings) as client:
        agent = Agent(
            name="FinancialAnalyst",
            instructions=candidate["system"],
            model=OpenAIChatCompletionsModel(model=settings.model, openai_client=client),
            model_settings=ModelSettings(max_tokens=1200, parallel_tool_calls=False,
                                         extra_body=settings.extra_body),
            tools=tools,
        )
        tool_result = await asyncio.wait_for(
            Runner.run(agent, f"Analyze {company} fiscal year {year} annual revenue growth.",
                       max_turns=5, hooks=hooks, run_config=RunConfig(tracing_disabled=True)), timeout=150,
        )
        finalizer = agent.clone(
            tools=[], output_type=AnalysisAnswer,
            model_settings=ModelSettings(max_tokens=1200, extra_body={
                **settings.extra_body, "response_format": {"type": "json_object"}}),
            instructions=(agent.instructions +
                          " Return a JSON data object containing exactly these fields: "
                          "status (complete or insufficient_data), company (string), fiscal_year (integer), "
                          "answer (string), growth_percent (string), evidence_ids (array of strings). "
                          "Populate the fields with actual tool results. Return no schema definitions or Markdown."),
        )
        result = await asyncio.wait_for(
            Runner.run(finalizer, tool_result.to_input_list() + [{"role": "user", "content":
                       "Return the final JSON using the exact tool values and evidence IDs."}],
                       max_turns=1, hooks=hooks, run_config=RunConfig(tracing_disabled=True)), timeout=100,
        )
    answer = result.final_output
    if answer.company != company or answer.fiscal_year != year:
        raise ValueError("Answer company or fiscal year does not match the request")
    try:
        expected = store.growth(company, "revenue", year)
    except ValueError:
        if answer.status != "insufficient_data" or answer.evidence_ids or answer.growth_percent:
            raise ValueError("Missing data was not reported correctly")
    else:
        try:
            actual = Decimal(answer.growth_percent)
        except InvalidOperation as error:
            raise ValueError("Invalid numerical answer") from error
        if not actual.is_finite() or abs(actual - expected.value) > Decimal("0.000001"):
            raise ValueError("Answer does not match the deterministic calculation")
        if answer.status != "complete" or set(answer.evidence_ids) != set(expected.evidence_ids):
            raise ValueError("Answer citations do not match the calculation")
        if not any(event["tool"] == "calculate_metrics" and store.company_id(event["company"]) == company
                   and event["metric"] == "revenue" and event["fiscal_year"] == year
                   and "error" not in event["output"] for event in events):
            raise ValueError("Required calculation tool was not executed")
    usage = result.context_wrapper.usage
    tool_usage = tool_result.context_wrapper.usage
    return {"answer": answer.model_dump(), "events": events,
            "lifecycle_events": lifecycle_events,
            "evidence": [store.evidence[key] for key in answer.evidence_ids],
            "data_version": store.version,
            "usage": {"requests": usage.requests + tool_usage.requests,
                      "input_tokens": usage.input_tokens + tool_usage.input_tokens,
                      "output_tokens": usage.output_tokens + tool_usage.output_tokens,
                      "total_tokens": usage.total_tokens + tool_usage.total_tokens}}
