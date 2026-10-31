"""Route source-bound financial operations through direct or MCP execution."""

import asyncio
from contextlib import asynccontextmanager, AsyncExitStack
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
import time

from finevolab.derived import DerivedResult
from finevolab.ingestion import FinancialMetric
from finevolab.mcp_client import financial_session, call_financial_tool
from finevolab.tools import GrowthResult, ComparisonResult
from finevolab.tracing import span


class MCPFinancialStore:
    def __init__(self, store, session, loop, timeout_seconds):
        self.store, self.session, self.loop = store, session, loop
        self.deadline = time.monotonic() + timeout_seconds
        self.remote_calls = 0

    def __getattr__(self, name):
        return getattr(self.store, name)

    def call(self, name, request, schema=None):
        try:
            active_loop = asyncio.get_running_loop()
        except RuntimeError:
            active_loop = None
        if active_loop is self.loop:
            raise RuntimeError("Synchronous MCP operations require a worker thread")
        remaining = min(30.0, self.deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("The tool execution deadline expired")
        self.remote_calls += 1
        future = asyncio.run_coroutine_threadsafe(call_financial_tool(self.session, name, request, remaining), self.loop)
        try:
            response = future.result(timeout=remaining + 1)
        except FutureTimeout:
            future.cancel()
            raise TimeoutError("The MCP bridge request timed out") from None
        if (response.get("data_version") != self.store.version or
                response.get("source_signature") != self.store.source_signature):
            raise ValueError("MCP data identity differs from the active financial snapshot")
        value = response["data"]
        return schema.model_validate(value) if schema else value

    def get_metric(self, company, name, fiscal_year):
        return self.call("get_financial_metrics", {"company": company, "metric": name, "fiscal_year": fiscal_year}, FinancialMetric)

    def growth(self, company, name, fiscal_year):
        return self.call("calculate_metrics", {"company": company, "metric": name, "fiscal_year": fiscal_year}, GrowthResult)

    def derive(self, company, metric, fiscal_year):
        return self.call("calculate_metrics", {"company": company, "metric": metric, "fiscal_year": fiscal_year}, DerivedResult)

    def compare(self, companies, name, fiscal_year):
        return self.call("compare_companies", {"companies": companies, "metric": name, "fiscal_year": fiscal_year}, ComparisonResult)

    def search(self, query, company, report_year, topic, variant, profile, metric_focus):
        return self.call("search_reports", {"query": query, "company": company, "report_year": report_year,
            "topic": topic, "variant": variant, "profile": profile, "metric_focus": metric_focus})


@asynccontextmanager
async def financial_backend(store, root: Path, backend: str, timeout_seconds: float, preload_retrieval: bool = False):
    if backend not in {"direct", "mcp"}:
        raise ValueError("Unknown financial tool backend")
    if backend == "direct":
        yield store
        return
    if store.upload_ids:
        raise ValueError("The MCP backend accepts only the fixed public snapshot")
    started = time.perf_counter()
    async with AsyncExitStack() as stack:
        with span("mcp.session.initialize", kind="client"):
            session = await stack.enter_async_context(financial_session(root, preload_retrieval=preload_retrieval))
        remote = MCPFinancialStore(store, session, asyncio.get_running_loop(), timeout_seconds)
        remote.startup_seconds = time.perf_counter() - started
        yield remote
