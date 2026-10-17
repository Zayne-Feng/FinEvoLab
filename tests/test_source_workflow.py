"""Verify selected-document isolation, combined answers, and active tool budgets."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openai.types.chat import ChatCompletion
from finevolab.config import ModelConfig
from finevolab.grounded import RequestPlan, analyze_grounded, validate_explanation
from finevolab.retrieval import chunk_filing, explanation_passages, load_index
from finevolab.runtime import BudgetExceeded, RunBudget, ToolExecutor
from finevolab.tools import FinancialStore
from finevolab.uploads import ingest_upload, verified_upload
from finevolab.validation import validate_answer

ROOT = Path(__file__).resolve().parents[1]


def report_fixture(revenue="1200"):
    contexts = "".join(f'<xbrli:context id="y{year}"><xbrli:period><xbrli:startDate>{year}-01-01</xbrli:startDate>'
        f'<xbrli:endDate>{year}-12-31</xbrli:endDate></xbrli:period></xbrli:context>' for year in (2023, 2024))
    return ('<html><body><h1>Synthetic annual statement fixture</h1>'
        '<ix:nonNumeric name="dei:DocumentFiscalYearFocus">2024</ix:nonNumeric>'
        '<ix:nonNumeric name="dei:DocumentPeriodEndDate">2024-12-31</ix:nonNumeric>'
        '<ix:nonNumeric name="dei:TradingSymbol">MSFT</ix:nonNumeric>' + contexts +
        '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
        '<p>Revenue increased because subscription demand improved and customer retention strengthened.</p>'
        '<p>Competition may adversely affect demand and pricing, which represents a business risk.</p>'
        '<table><tr><th>Metric</th><th>2024</th><th>2023</th></tr><tr><td>Revenue</td>'
        f'<td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="y2024" unitRef="usd" scale="6">{revenue}</ix:nonFraction></td>'
        '<td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="y2023" unitRef="usd" scale="6">1000</ix:nonFraction></td>'
        '</tr></table></body></html>').encode()


class FakeClient:
    base_url = "https://example.com/"

    def __init__(self, text):
        self.text, self.chat = text, type("Chat", (), {})()
        self.chat.completions = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def create(self, **kwargs):
        return ChatCompletion(id="source-test", created=0, model="model", object="chat.completion",
            choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": self.text}}],
            usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20})


class SourceWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.upload_root = Path(self.directory.name)
        self.document = ingest_upload(report_fixture(), ".html", "MSFT", 2024, self.upload_root)
        self.store = FinancialStore.from_uploads(ROOT / "data/processed/reports.json", [self.document["id"]], self.upload_root)
        self.settings = ModelConfig("model", "https://example.com/", "unused")

    async def test_selected_upload_values_do_not_fall_back_to_snapshot(self):
        plan = RequestPlan(intent="metric", companies=["MSFT"], fiscal_year=2024, metrics=["revenue"])
        result = await analyze_grounded(self.settings, self.store, "Read the selected report revenue.", {"system": ""}, request_plan=plan)
        self.assertEqual(result["answer"]["metrics"][0]["value"], "1200000000")
        self.assertEqual(result["usage"]["requests"], 0)
        self.assertEqual(result["upload_ids"], [self.document["id"]])
        self.assertTrue(result["source_issues"])
        validate_answer(self.store, result)
        absent = plan.model_copy(update={"metrics": ["net_income"]})
        result = await analyze_grounded(self.settings, self.store, "Read net income.", {"system": ""}, request_plan=absent)
        self.assertEqual(result["answer"]["status"], "insufficient_data")

    async def test_combined_growth_and_explanation_share_selected_document(self):
        plan = RequestPlan(intent="hybrid", numeric_intent="growth", companies=["MSFT"], fiscal_year=2024,
                           metrics=["revenue"], topic="revenue")
        chunk = next(item for item in self.document["chunks"] if "subscription demand" in item["text"])
        quote = "Revenue increased because subscription demand improved and customer retention strengthened."
        draft = {"status": "complete", "claims": [{"statement": "Revenue increased because subscription demand improved.",
                    "chunk_id": "passage:" + chunk["id"], "quote": quote}]}
        with patch("finevolab.grounded.async_client", return_value=FakeClient(json.dumps(draft))):
            result = await analyze_grounded(self.settings, self.store, "Compute revenue growth and explain its drivers.",
                {"system": ""}, request_plan=plan)
        self.assertEqual(result["answer_type"], "hybrid")
        self.assertEqual(result["overall_status"], "complete")
        self.assertEqual(result["answer"]["calculations"][0]["value"], "20.0")
        self.assertEqual(result["runtime_backend"], "sdk")
        self.assertEqual(result["tool_calls"], 2)
        validate_answer(self.store, result)

    async def test_zero_tool_budget_is_enforced_before_financial_execution(self):
        plan = RequestPlan(intent="metric", companies=["MSFT"], fiscal_year=2024, metrics=["revenue"])
        with self.assertRaises(BudgetExceeded):
            await analyze_grounded(self.settings, self.store, "Read revenue.", {"system": ""}, request_plan=plan,
                                   budget=RunBudget(max_tool_calls=0))

    async def test_revenue_change_requires_a_metric_and_direction_claim(self):
        from finevolab.grounded import checked_passages
        chunk = next(item for item in self.document["chunks"] if "subscription demand" in item["text"])
        plan = RequestPlan(intent="explanation", companies=["MSFT"], fiscal_year=2024, metrics=["revenue"])
        quote = "Revenue increased because subscription demand improved and customer retention strengthened."
        output = {"question": "Explain the revenue change.", "request": plan.model_dump(),
            "answer": {"status": "complete", "claims": [{"chunk_id": "passage:" + chunk["id"],
                "quote": quote, "statement": "The report discusses subscription demand and customer retention."}]},
            "evidence": checked_passages(ROOT, [chunk], self.store)}
        with self.assertRaisesRegex(ValueError, "Directly describe"):
            validate_explanation(ROOT, output, self.store)
        output["answer"]["claims"][0]["statement"] = "Revenue increased as subscription demand improved."
        self.assertEqual(validate_explanation(ROOT, output, self.store)["status"], "passed")

    async def test_company_subtasks_receive_only_their_own_passages(self):
        second = ingest_upload(report_fixture().replace(b"MSFT", b"TSLA"), ".html", "TSLA", 2024, self.upload_root)
        store = FinancialStore.from_uploads(ROOT / "data/processed/reports.json",
            [self.document["id"], second["id"]], self.upload_root)
        plan = RequestPlan(intent="explanation", companies=["MSFT", "TSLA"], fiscal_year=2024, metrics=["revenue"])
        observed = []
        class CompanyClient(FakeClient):
            async def create(client, **kwargs):
                payload = json.loads(kwargs["messages"][1]["content"])
                company = payload["company"]
                self.assertEqual(payload["request"]["companies"], [company])
                self.assertTrue(all(company in p["text"] for p in payload["passages"]))
                self.assertEqual(payload["metric_changes"][0]["direction"], "increased")
                passage = next(p for p in payload["passages"] if "subscription demand" in p["text"])
                client.text = json.dumps({"status": "complete", "claims": [{"chunk_id": passage["id"],
                    "quote": "Revenue increased because subscription demand improved and customer retention strengthened.",
                    "statement": company + " revenue increased as subscription demand improved."}]})
                observed.append(company)
                return await super(CompanyClient, client).create(**kwargs)
        with patch("finevolab.grounded.async_client", return_value=CompanyClient("")):
            result = await analyze_grounded(self.settings, store, "Explain both companies' revenue changes.",
                {"system": ""}, request_plan=plan)
        self.assertEqual(observed, ["MSFT", "TSLA"])
        self.assertEqual(result["answer"]["status"], "complete")
        self.assertEqual(result["tool_calls"], 4)

    async def test_combined_missing_metric_preserves_partial_status(self):
        plan = RequestPlan(intent="hybrid", numeric_intent="metric", companies=["MSFT"], fiscal_year=2024,
                           metrics=["net_income"], topic="risk")
        chunk = next(item for item in self.document["chunks"] if "Competition may" in item["text"])
        quote = "Competition may adversely affect demand and pricing, which represents a business risk."
        draft = {"status": "complete", "claims": [{"statement": "Competition may adversely affect demand and pricing.",
            "chunk_id": "passage:" + chunk["id"], "quote": quote}]}
        with patch("finevolab.grounded.async_client", return_value=FakeClient(json.dumps(draft))):
            result = await analyze_grounded(self.settings, self.store, "Read net income and explain the risks.",
                {"system": ""}, request_plan=plan)
        self.assertEqual(result["overall_status"], "partial")
        self.assertEqual(result["answer"]["status"], "insufficient_data")
        self.assertEqual(result["answer"]["metrics"], [])
        validate_answer(self.store, result)
        result["overall_status"] = "complete"
        with self.assertRaisesRegex(ValueError, "Combined task status"):
            validate_answer(self.store, result)

    async def test_passage_from_an_unselected_upload_is_rejected(self):
        other = ingest_upload(report_fixture("1400"), ".html", "MSFT", 2024, self.upload_root)
        chunk = next(item for item in other["chunks"] if "subscription demand" in item["text"])
        plan = RequestPlan(intent="explanation", companies=["MSFT"], fiscal_year=2024, metrics=["revenue"])
        output = {"request": plan.model_dump(), "upload_ids": self.store.upload_ids,
            "answer": {"status": "complete", "claims": [{"statement": "Revenue increased because subscription demand improved.",
                "chunk_id": "passage:" + chunk["id"], "quote": chunk["body_text"]}]},
            "evidence": [{**chunk, "id": "passage:" + chunk["id"]}]}
        with self.assertRaisesRegex(ValueError, "pinned index"):
            validate_explanation(ROOT, output, self.store)

    async def test_upload_source_and_extracted_metadata_tampering_are_rejected(self):
        path = self.upload_root / (self.document["id"] + ".json")
        changed = copy.deepcopy(self.document)
        changed["metric_results"]["observations"][0]["metric"]["value"] = "1"
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "metadata differs"):
            verified_upload(self.document["id"], self.upload_root)
        raw_path = self.upload_root / self.document["path"]
        raw_path.write_bytes(raw_path.read_bytes() + b"modified")
        with self.assertRaisesRegex(ValueError, "source hash"):
            verified_upload(self.document["id"], self.upload_root)

    async def test_unit_context_is_derived_from_inline_facts(self):
        table = next(item for item in self.document["chunks"] if item["kind"] == "table")
        self.assertEqual(table["unit_context"]["currency"], "USD")
        self.assertEqual(table["unit_context"]["scale"], 6)
        self.assertIn("1000000", table["text"])
        self.assertIn("ix:nonfraction", table["unit_context"]["locator"])

    async def test_cache_is_version_bound_and_cache_hits_still_consume_budget(self):
        calls, events = [], []
        def value():
            calls.append(1)
            return {"value": len(calls)}
        first = ToolExecutor(RunBudget(max_tool_calls=1), events, {"source": "a"}, self.upload_root / "cache")
        self.assertEqual(first.call("lookup", {}, value), {"value": 1})
        second = ToolExecutor(RunBudget(max_tool_calls=1), events, {"source": "a"}, self.upload_root / "cache")
        self.assertEqual(second.call("lookup", {}, value), {"value": 1})
        self.assertTrue(events[-1]["cache_hit"])
        with self.assertRaises(BudgetExceeded):
            second.call("lookup", {}, value)
        third = ToolExecutor(RunBudget(max_tool_calls=1), events, {"source": "b"}, self.upload_root / "cache")
        self.assertEqual(third.call("lookup", {}, value), {"value": 2})

    async def test_failed_and_rejected_tools_preserve_execution_events(self):
        events = []
        executor = ToolExecutor(RunBudget(max_tool_calls=1), events, {"source": "a"})
        def missing():
            raise ValueError("Annual observation unavailable")
        with self.assertRaises(ValueError):
            executor.call("lookup", {"year": 1900}, missing)
        self.assertEqual(events[-1]["status"], "failed")
        with self.assertRaises(BudgetExceeded):
            executor.call("lookup", {"year": 2024}, lambda: 1)
        self.assertEqual(events[-1]["event"], "tool_rejected")

    async def test_profit_retrieval_distinguishes_derivative_losses_from_net_income(self):
        texts = ["2024 | 2023\nNet losses on derivatives | 8 | 9",
                 "2024 | 2023\nRevenue | 120 | 100\nOperating income | 40 | 30\nNet income | 35 | 25",
                 "Income tax expense decreased primarily due to changes in statutory tax provisions and foreign earnings."]
        chunks = [{"id": str(i), "company": "MSFT", "report_year": 2024, "section": "item-7",
                   "kind": "table" if i < 2 else "div", "text": text, "body_text": text} for i, text in enumerate(texts)]
        passages = explanation_passages(ROOT, "Explain MSFT FY2024 net income changes.", "MSFT", 2024,
            "profit", profile="contextual", uploads=[{"chunks": chunks}], metric_focus="net_income")
        self.assertEqual(passages[0]["id"], "1")
        self.assertEqual(len(passages), len({p["id"] for p in passages}))

    async def test_revenue_retrieval_prefers_consolidated_totals_over_recognition_and_segments(self):
        texts = ["2024 | 2023\nRevenue recognized | 120 | 100",
                 "2024 | 2023\nAutomotive segment\nRevenues | 120 | 100",
                 "2024 | 2023\nTotal revenues | 140 | 110"]
        chunks = [{"id": str(i), "company": "MSFT", "report_year": 2024, "section": "item-8",
            "kind": "table", "text": text, "body_text": text} for i, text in enumerate(texts)]
        passages = explanation_passages(ROOT, "Explain revenue changes.", "MSFT", 2024,
            "revenue", profile="contextual", uploads=[{"chunks": chunks}], metric_focus="revenue")
        self.assertEqual(passages[0]["id"], "2")

    async def test_revenue_drivers_rank_above_costs_and_quarterly_seasonality(self):
        texts = ["Cost of revenue in 2024 increased compared to 2023 primarily due to higher depreciation and infrastructure expenses. " * 2,
            "Revenue increased between quarters primarily due to seasonal demand. Quarterly revenue growth reflects holiday sales. " * 2,
            "Total revenue for 2024 increased compared to 2023 due to increased advertising revenue. Advertising demand and delivered impressions increased. " * 2]
        chunks = [{"id": str(i), "company": "MSFT", "report_year": 2024, "section": "item-7",
            "kind": "p", "text": text, "body_text": text} for i, text in enumerate(texts)]
        passages = explanation_passages(ROOT, "Explain annual revenue growth.", "MSFT", 2024,
            "revenue", profile="contextual", uploads=[{"chunks": chunks}], metric_focus="revenue")
        self.assertEqual(passages[0]["id"], "2")

    async def test_annual_net_income_loss_row_precedes_partial_investee_loss(self):
        texts = ["2023 | 2022\nNet income (loss) | 30,425 | (2,722)",
            "Nine Months Ended September 30, 2023 | 2022\nNet loss | (3,911) | (6,752)"]
        chunks = [{"id": str(i), "company": "MSFT", "report_year": 2023, "section": "item-8",
            "kind": "table", "text": text, "body_text": text} for i, text in enumerate(texts)]
        passages = explanation_passages(ROOT, "Explain annual net income changes.", "MSFT", 2023,
            "profit", profile="contextual", uploads=[{"chunks": chunks}], metric_focus="net_income")
        self.assertEqual(passages[0]["id"], "0")

    async def test_short_direct_revenue_driver_precedes_margin_cooccurrence(self):
        texts = ["Gross margin increased primarily due to higher revenue and lower costs. " * 3,
                 "Revenue grew due to stronger enterprise demand.",
                 "Inventory accounting follows the stated valuation policy."]
        chunks = [{"id": str(i), "company": "MSFT", "report_year": 2024, "section": "item-7",
            "kind": "p", "text": text, "body_text": text} for i, text in enumerate(texts)]
        passages = explanation_passages(ROOT, "Explain annual revenue growth.", "MSFT", 2024,
            "revenue", profile="contextual", uploads=[{"chunks": chunks}], metric_focus="revenue")
        self.assertEqual(passages[0]["id"], "1")

    async def test_income_statement_alias_precedes_cash_flow_and_common_size(self):
        texts = ["2024 | 2023\nCash flows from operating activities\nNet income | 35 | 25\nDepreciation | 4 | 3",
            "2024 | 2023\nRevenue | 120 | 100\nIncome from operations | 40 | 30\nNet income | 35 | 25",
            "2024 | 2023\nRevenue | 100 | % | 100 | %\nIncome from operations | 33 | 30\nNet income | 29 | 25"]
        chunks = [{"id": str(i), "company": "MSFT", "report_year": 2024, "section": "item-8",
            "kind": "table", "text": text, "body_text": text} for i, text in enumerate(texts)]
        passages = explanation_passages(ROOT, "Explain net income changes.", "MSFT", 2024,
            "profit", profile="contextual", uploads=[{"chunks": chunks}], metric_focus="net_income")
        self.assertEqual(passages[0]["id"], "1")

    async def test_quarterly_total_does_not_precede_annual_revenue_driver(self):
        texts = ["Total revenue increased during the quarter primarily due to seasonal demand. " * 3,
            "Revenue increased during the fiscal year primarily due to stronger enterprise demand. " * 3,
            "Inventory accounting uses the stated valuation policy."]
        chunks = [{"id": str(i), "company": "MSFT", "report_year": 2024, "section": "item-7",
            "kind": "p", "text": text, "body_text": text} for i, text in enumerate(texts)]
        passages = explanation_passages(ROOT, "Explain annual revenue growth.", "MSFT", 2024,
            "revenue", profile="contextual", uploads=[{"chunks": chunks}], metric_focus="revenue")
        self.assertEqual(passages[0]["id"], "1")

    async def test_risks_prioritize_the_requested_revenue_outcome(self):
        texts = ["Regulation could adversely affect cash flows and operating expenses. " * 3,
            "Customer demand could decline and adversely affect revenue and product sales. " * 3,
            "Inventory accounting uses the stated valuation policy."]
        chunks = [{"id": str(i), "company": "MSFT", "report_year": 2024, "section": "item-1a",
            "kind": "p", "text": text, "body_text": text} for i, text in enumerate(texts)]
        passages = explanation_passages(ROOT, "Which risks can affect revenue?", "MSFT", 2024,
            "risk", profile="contextual", uploads=[{"chunks": chunks}], metric_focus="revenue")
        self.assertEqual(passages[0]["id"], "1")
