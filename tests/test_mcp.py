"""Verify MCP discovery, snapshot equivalence, parameter validation, and scope enforcement."""

import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from finevolab.mcp_client import financial_session, call_financial_tool
from finevolab.mcp_server import FinancialToolService, MetricRequest
from finevolab.retrieval import load_index
from finevolab.tracing import trace_scope

ROOT = Path(__file__).resolve().parents[1]


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_discovery_equivalence_and_invalid_arguments(self):
        service = FinancialToolService()
        requests = [
            ("get_financial_metrics", {"company": "MSFT", "metric": "revenue", "fiscal_year": 2024}),
            ("calculate_metrics", {"company": "MSFT", "metric": "revenue", "fiscal_year": 2024}),
            ("calculate_metrics", {"company": "MSFT", "metric": "operating_margin_change", "fiscal_year": 2024}),
            ("compare_companies", {"companies": ["AAPL", "MSFT"], "metric": "revenue", "fiscal_year": 2024}),
            ("search_reports", {"company": "MSFT", "query": "revenue growth", "report_year": 2024})]
        expected = [service.store.get_metric("MSFT", "revenue", 2024).model_dump(mode="json"),
            service.store.growth("MSFT", "revenue", 2024).model_dump(mode="json"),
            service.store.derive("MSFT", "operating_margin_change", 2024).model_dump(mode="json"),
            service.store.compare(["AAPL", "MSFT"], "revenue", 2024).model_dump(mode="json"),
            load_index(ROOT, mode="bm25").search("revenue growth", company="MSFT", year=2024, mode="bm25")]
        async with financial_session(ROOT) as session:
            tools = (await session.list_tools()).tools
            self.assertEqual({tool.name for tool in tools}, set(service.policy.allowed_tools))
            self.assertTrue(all(t.annotations.readOnlyHint and not t.annotations.destructiveHint for t in tools))
            for (name, request), value in zip(requests, expected):
                with self.subTest(tool=name, metric=request.get("metric")):
                    with trace_scope("test.client") as (trace, _):
                        response = await call_financial_tool(session, name, request)
                        self.assertEqual(response["trace_id"], trace.trace_id)
                    self.assertEqual(response["data"], value)
                    self.assertEqual(response["data_version"], service.store.version)
            base = requests[0][1]
            for change in ({"fiscal_year": "2024"}, {"fiscal_year": True}, {"fiscal_year": 0},
                           {"metric": "price"}, {"path": "private.ini"}, {"company": "UNKNOWN"}):
                with self.subTest(rejected=change):
                    response = await session.call_tool("get_financial_metrics", {"request": {**base, **change}})
                    self.assertTrue(response.isError)
            response = await session.call_tool("search_reports", {"request": {"company": "MSFT", "report_year": 2024, "query": " "}})
            self.assertTrue(response.isError)
            response = await session.call_tool("build_report", {"company": "MSFT"})
            self.assertTrue(response.isError)

    async def test_server_enforces_tool_and_company_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            policy.write_text(json.dumps({"version": 1, "allowed_tools": ["get_financial_metrics"],
                                          "allowed_companies": ["MSFT"], "retrieval_mode": "bm25"}))
            service = FinancialToolService(ROOT, policy)
            with self.assertRaises(PermissionError):
                service.authorize("calculate_metrics", ["MSFT"])
            async with financial_session(ROOT, policy) as session:
                self.assertEqual([t.name for t in (await session.list_tools()).tools], ["get_financial_metrics"])
                request = {"company": "AAPL", "metric": "revenue", "fiscal_year": 2024}
                result = await session.call_tool("get_financial_metrics", {"request": request})
                self.assertTrue(result.isError)
                result = await session.call_tool("calculate_metrics", {"request": {**request, "company": "MSFT"}})
                self.assertTrue(result.isError)
                result = await call_financial_tool(session, "get_financial_metrics", {**request, "company": "Microsoft"})
                self.assertEqual(result["data"]["company"], "MSFT")

    def test_strict_schema_rejects_unknown_fields(self):
        with self.assertRaises(ValidationError):
            MetricRequest.model_validate({"company": "MSFT", "metric": "revenue", "fiscal_year": 2024, "upload_ids": ["private"]})
