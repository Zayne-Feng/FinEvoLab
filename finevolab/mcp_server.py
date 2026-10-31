"""Expose policy-scoped, snapshot-only financial tools over MCP stdio."""

import asyncio
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from finevolab.derived import DERIVED_METRICS
from finevolab.runtime import observe, read_json
from finevolab.tools import FinancialStore
from finevolab.tracing import trace_scope, flush_exports

ROOT = Path(__file__).resolve().parents[1]
Metric = Literal["revenue", "operating_income", "net_income"]
Calculation = Literal["revenue", "operating_income", "net_income", "operating_margin",
                      "net_margin", "operating_margin_change", "net_margin_change"]
Year = Annotated[int, Field(strict=True, ge=1990, le=2100)]
Company = Annotated[str, Field(strict=True, min_length=1, max_length=80)]
TOOLS = {"get_financial_metrics", "calculate_metrics", "compare_companies", "search_reports"}


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    company: Company
    fiscal_year: Year


class MetricRequest(Request):
    metric: Metric


class CalculationRequest(Request):
    metric: Calculation


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    companies: Annotated[list[Company], Field(min_length=2, max_length=5)]
    metric: Metric
    fiscal_year: Year


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    company: Company
    report_year: Year
    query: Annotated[str, Field(min_length=1, max_length=12000)]
    topic: Literal["revenue", "profit", "risk"] | None = None
    variant: Literal["baseline", "improved", "no_expansion", "no_priority", "none", "pooled", "reranked"] = "improved"
    profile: Literal["legacy", "contextual"] = "legacy"
    metric_focus: Metric | None = None


class ToolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict | list[dict]
    data_version: str
    source_signature: str
    trace_id: str
    trace_mode: str
    span_count: int
    export_stats: dict = Field(default_factory=dict)


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    allowed_tools: Annotated[list[str], Field(min_length=1)]
    allowed_companies: Annotated[list[str], Field(min_length=1)]
    retrieval_mode: Literal["bm25", "vector", "hybrid"]


class FinancialToolService:
    def __init__(self, root: Path = ROOT, policy_path: Path | None = None):
        self.root = root
        self.store = FinancialStore(root / "data/processed/reports.json")
        self.policy = Policy.model_validate(read_json(policy_path or root / "configs/mcp.json"))
        available = {item.company for item in self.store.metrics}
        if (not set(self.policy.allowed_tools) <= TOOLS or
                not set(self.policy.allowed_companies) <= available):
            raise ValueError("MCP policy contains unknown tools or companies")

    def authorize(self, name: str, companies: list[str]):
        if name not in self.policy.allowed_tools:
            raise PermissionError("Tool is outside the configured read-only scope")
        if any(self.store.company_id(value) not in self.policy.allowed_companies for value in companies):
            raise PermissionError("Company is outside the configured snapshot scope")

    def invoke(self, name: str, request: BaseModel):
        companies = request.companies if isinstance(request, ComparisonRequest) else [request.company]
        self.authorize(name, companies)
        if name == "get_financial_metrics":
            value = self.store.get_metric(request.company, request.metric, request.fiscal_year)
        elif name == "calculate_metrics":
            value = (self.store.derive(request.company, request.metric, request.fiscal_year)
                     if request.metric in DERIVED_METRICS else
                     self.store.growth(request.company, request.metric, request.fiscal_year))
        elif name == "compare_companies":
            value = self.store.compare(request.companies, request.metric, request.fiscal_year)
        elif name == "search_reports":
            from finevolab.retrieval import load_index, explanation_passages
            if request.topic is not None:
                return explanation_passages(self.root, request.query.strip(), self.store.company_id(request.company),
                    request.report_year, request.topic, request.variant, request.profile, [], request.metric_focus)
            # The fixed index excludes uploaded documents and private user scope.
            value = load_index(self.root, mode=self.policy.retrieval_mode).search(
                request.query.strip(), company=self.store.company_id(request.company),
                year=request.report_year, mode=self.policy.retrieval_mode)
        else:
            raise PermissionError("Unknown tool")
        return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def create_server(root: Path = ROOT, policy_path: Path | None = None, preload_retrieval: bool = False):
    service = FinancialToolService(root, policy_path)
    from finevolab.retrieval import load_index
    if "search_reports" in service.policy.allowed_tools:
        load_index(root, mode=service.policy.retrieval_mode)
    if preload_retrieval:
        # Native encoder dependencies initialize before worker threads start.
        from importlib import import_module
        import_module("sentence_transformers")
    server = FastMCP("FinEvoLab", log_level="ERROR")
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                  idempotentHint=True, openWorldHint=False)

    async def call(name, request, ctx):
        meta = ctx.request_context.meta
        incoming = getattr(meta, "traceparent", None) if meta is not None else None
        with trace_scope("mcp." + name, root / "output/mcp/traces", incoming) as (trace, _):
            with observe([], "tool", name):
                data = await asyncio.to_thread(service.invoke, name, request)
            response = {"data": data, "data_version": service.store.version, "source_signature": service.store.source_signature, "trace_id": trace.trace_id,
                        "trace_mode": trace.mode, "span_count": len(trace.spans)}
        if getattr(meta, "flush_trace", False):
            response["export_stats"] = await asyncio.to_thread(flush_exports)
        return response

    async def get_financial_metrics(request: MetricRequest, ctx: Context) -> ToolResponse:
        """Return an annual metric with its exact fiscal dates, units, and source citation."""
        return await call("get_financial_metrics", request, ctx)

    async def calculate_metrics(request: CalculationRequest, ctx: Context) -> ToolResponse:
        """Calculate annual growth or profit margins with source-bound inputs and audits."""
        return await call("calculate_metrics", request, ctx)

    async def compare_companies(request: ComparisonRequest, ctx: Context) -> ToolResponse:
        """Compare annual values and expose whether the actual fiscal periods align."""
        return await call("compare_companies", request, ctx)

    async def search_reports(request: SearchRequest, ctx: Context) -> ToolResponse:
        """Retrieve cited passages from the configured public report snapshot."""
        if not request.query.strip():
            raise ValueError("A nonempty query is required")
        return await call("search_reports", request, ctx)

    for function in (get_financial_metrics, calculate_metrics, compare_companies, search_reports):
        if function.__name__ in service.policy.allowed_tools:
            server.tool(annotations=annotations, structured_output=True)(function)
    return server
