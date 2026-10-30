"""Verify lossless context, cumulative recovery, source scope, and scenario contracts."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from threading import Barrier, Lock, Event
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from finevolab.context_encoding import encode_context, decode_context, GUIDANCE
from finevolab.config import ModelConfig
from finevolab.grounded import RequestPlan, analyze_grounded
from finevolab.methods import AnalysisSettings, method_for
from finevolab.reconciliation import SourceObservation, reconcile, reconcile_metric
from finevolab.retrieval import load_index
from finevolab.runtime import BudgetExceeded, RunBudget, ToolExecutor
from finevolab.scenarios import ScenarioRequest, calculate_scenarios, validate_scenarios
from finevolab.singleflight import SingleFlight
from finevolab.subtasks import SubtaskJournal
from finevolab.tools import FinancialStore
from finevolab.validation import validate_answer
from finevolab.web import app

ROOT = Path(__file__).resolve().parents[1]


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.store = FinancialStore(ROOT / "data/processed/reports.json")

    def test_encoding_preserves_all_fields_and_verbatim_passages(self):
        items = [m.model_dump(mode="json") for m in self.store.metrics[:24]]
        payload = {"question": "Annual analysis", "metrics": items + items,
                   "passages": [{"id": "source", "text": "Revenue | USD millions | FY2024 | 245,122"}]}
        encoded, stats = encode_context(payload)
        self.assertEqual(decode_context(encoded), payload)
        self.assertGreater(stats["observation_rows"], 0)
        self.assertLess(len(encoded) + len(GUIDANCE), stats["original_chars"])
        self.assertIn(payload["passages"][0]["text"], encoded)

    def test_small_payload_does_not_gain_encoding_overhead(self):
        payload = {"question": "Value?", "selected_documents": []}
        encoded, stats = encode_context(payload)
        self.assertEqual(decode_context(encoded), payload)
        self.assertLessEqual(len(encoded), stats["original_chars"])
        self.assertEqual(stats["observation_rows"], 0)

    def test_source_scope_distinguishes_aliases_from_independent_filings(self):
        m = self.store.get_metric("MSFT", "revenue", 2024)
        rows = [SourceObservation(metric=m, source_id="html", filing_id="filing-a"),
                SourceObservation(metric=m, source_id="json", filing_id="filing-a")]
        self.assertEqual(reconcile(rows)["status"], "same_filing")
        self.assertEqual(reconcile(rows)["source_groups"], 1)
        rows[1].filing_id = "filing-b"
        self.assertEqual(reconcile(rows)["status"], "corroborated")

    def test_scope_mismatch_cannot_become_corroboration(self):
        m = self.store.get_metric("MSFT", "revenue", 2024)
        for field, value in (("period_end", "2024-12-31"), ("currency", "EUR"), ("name", "net_income")):
            changed = m.model_copy(update={field: value})
            rows = [SourceObservation(metric=m, source_id="a"), SourceObservation(metric=changed, source_id="b")]
            self.assertEqual(reconcile(rows)["status"], "not_comparable")

    def test_rounding_scale_and_explicit_restatements(self):
        m = self.store.get_metric("MSFT", "revenue", 2024).model_copy(update={"value": Decimal("1000000")})
        scaled = m.model_copy(update={"value": Decimal("1"), "unit": "USD_millions"})
        rows = [SourceObservation(metric=m, source_id="a", filing_id="a", disclosed_on="2024-06-30"),
                SourceObservation(metric=scaled, source_id="b", filing_id="b", scale=Decimal("1000000"), disclosed_on="2025-06-30")]
        self.assertEqual(reconcile(rows)["status"], "corroborated")
        rows[1].metric.value = Decimal("1.0000004")
        rows[1].rounding_unit = Decimal("0.000001")
        self.assertEqual(reconcile(rows)["status"], "corroborated")
        rows[1].metric.value = Decimal("1.1")
        self.assertEqual(reconcile(rows)["status"], "conflict")
        rows[1].restated = True
        self.assertEqual(reconcile(rows)["status"], "restated")
        rows[1].disclosed_on = rows[0].disclosed_on
        self.assertEqual(reconcile(rows)["status"], "conflict")

    def test_real_source_coordination_is_read_only(self):
        before = [m.model_dump() for m in self.store.metrics]
        result = reconcile_metric(self.store, "Microsoft", "revenue", 2024)
        self.assertIn(result["status"], {"single_source", "same_filing", "corroborated"})
        self.assertEqual(before, [m.model_dump() for m in self.store.metrics])

    def test_scenario_reference_includes_revenue_margin_interaction(self):
        self.store.get_metric("MSFT", "revenue", 2024).value = Decimal("100")
        self.store.get_metric("MSFT", "operating_income", 2024).value = Decimal("20")
        request = ScenarioRequest(company="MSFT", fiscal_year=2024, margin="operating_margin",
                    revenue_changes_percent=[Decimal("0"), Decimal("10"), Decimal("-100")],
                    margin_changes_percentage_points=[Decimal("0"), Decimal("2")])
        result = calculate_scenarios(self.store, request)
        self.assertEqual(result.points[0].profit, Decimal("20"))
        self.assertEqual(result.points[3].profit, Decimal("24.2"))
        self.assertEqual(result.points[-1].profit, Decimal("0"))
        self.assertTrue(result.assumption_based)
        self.assertEqual(validate_scenarios(self.store, result), result)
        result.points[0].profit = Decimal("99")
        with self.assertRaises(ValueError):
            validate_scenarios(self.store, result)

    def test_scenario_rejects_implicit_duplicate_or_nonfinite_assumptions(self):
        base = {"company": "MSFT", "fiscal_year": 2024, "margin": "net_margin",
                "revenue_changes_percent": [0], "margin_changes_percentage_points": [0]}
        for change in ({"revenue_changes_percent": []}, {"revenue_changes_percent": [-101]},
                       {"revenue_changes_percent": [0, 0]}, {"margin_changes_percentage_points": ["NaN"]}):
            with self.assertRaises(ValueError):
                ScenarioRequest.model_validate({**base, **change})

    def test_method_selection_keeps_frozen_plan_fields(self):
        plan = RequestPlan(intent="explanation", companies=["MSFT"], fiscal_year=2024, metrics=["revenue"], topic="risk")
        before = plan.model_dump()
        self.assertEqual(method_for(plan)["name"], "risk")
        self.assertEqual(method_for("scenario")["name"], "scenario")
        self.assertEqual(plan.model_dump(), before)

    def test_journal_revalidates_results_and_cumulative_usage(self):
        plan = RequestPlan(intent="metric", companies=["MSFT"], fiscal_year=2024, metrics=["revenue"])
        with tempfile.TemporaryDirectory() as directory:
            j = SubtaskJournal(Path(directory), {"source": "a"}, RunBudget(max_model_calls=1))
            ticket = j.reserve()
            j.complete(ticket, 20)
            j.save("plan", plan)
            restored = SubtaskJournal(Path(directory), {"source": "a"}, RunBudget(max_model_calls=1))
            self.assertEqual(restored.load("plan", RequestPlan), plan)
            with self.assertRaises(BudgetExceeded):restored.reserve()
            self.assertIsNone(SubtaskJournal(Path(directory), {"source": "b"}, RunBudget()).load("plan", RequestPlan))
            path = restored.root / "plan.json"
            data = json.loads(path.read_text());data["result"]["fiscal_year"] = 2020
            path.write_text(json.dumps(data))
            self.assertIsNone(restored.load("plan", RequestPlan))

    def test_unresolved_model_usage_cannot_reset_budget_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = SubtaskJournal(Path(directory), {}, RunBudget())
            journal.reserve()
            with self.assertRaises(BudgetExceeded):
                SubtaskJournal(Path(directory), {}, RunBudget()).reserve()

    def test_singleflight_isolates_results_and_releases_failed_work(self):
        flight = SingleFlight()
        with self.assertRaises(ValueError):flight.run("a", lambda: (_ for _ in ()).throw(ValueError("error")))
        self.assertEqual(flight.run("a", lambda: [1])[0], [1])
        self.assertFalse(flight._pending)
        self.assertEqual(flight.run("b", lambda: [2])[0], [2])

    def test_concurrent_tool_misses_execute_once_without_bypassing_budgets(self):
        gate, lock = Barrier(8), Lock()
        calls = 0
        with tempfile.TemporaryDirectory() as directory:
            def worker(index):
                nonlocal calls
                executor = ToolExecutor(RunBudget(max_tool_calls=1), [], {"scope": "same"}, Path(directory))
                def compute():
                    nonlocal calls
                    with lock:calls += 1
                    hashlib.pbkdf2_hmac("sha256", b"payload", b"salt", 30000)
                    return {"rows": [1]}
                gate.wait()
                result = executor.call("retrieval", {"query": "same"}, compute)
                with self.assertRaises(BudgetExceeded):executor.call("retrieval", {}, compute)
                result["rows"].append(index)
                return result
            with ThreadPoolExecutor(max_workers=8) as pool:results = list(pool.map(worker, range(8)))
        self.assertEqual(calls, 1)
        self.assertTrue(all(len(item["rows"]) == 2 for item in results))

    def test_singleflight_waiter_timeout_does_not_cancel_the_leader(self):
        flight, entered, release = SingleFlight(), Event(), Event()
        def compute():
            entered.set()
            release.wait(2)
            return [1]
        with ThreadPoolExecutor(max_workers=1) as pool:
            leader = pool.submit(flight.run, "scope", compute)
            self.assertTrue(entered.wait(1))
            try:
                with self.assertRaises(TimeoutError):flight.run("scope", lambda: [2], timeout=0.01)
            finally:release.set()
            self.assertEqual(leader.result()[0], [1])
        self.assertFalse(flight._pending)

    def test_context_reserved_fields_are_passed_through_verbatim(self):
        payload = {"_context_encoding": "observations_v1", "_observations": "user content"}
        encoded, _ = encode_context(payload)
        self.assertEqual(json.loads(encoded), payload)

    def test_source_audit_tampering_is_rejected_by_answer_validation(self):
        from finevolab.grounded import assemble_numeric
        plan = RequestPlan(intent="metric", companies=["MSFT"], fiscal_year=2024, metrics=["revenue"])
        output = {"answer": assemble_numeric(plan, self.store, []),
                  "source_reconciliation": {"MSFT|revenue|2024": reconcile_metric(self.store, "MSFT", "revenue", 2024)}}
        self.assertEqual(validate_answer(self.store, output)["status"], "passed")
        output["source_reconciliation"]["MSFT|revenue|2024"]["status"] = "fabricated"
        with self.assertRaises(ValueError):validate_answer(self.store, output)

    def test_source_nonfinite_and_unrecognized_scales_are_rejected(self):
        metric = self.store.get_metric("MSFT", "revenue", 2024)
        with self.assertRaises(ValueError):
            SourceObservation(metric=metric.model_copy(update={"value": Decimal("NaN")}), source_id="a")
        rows = [SourceObservation(metric=metric, source_id="a", scale=Decimal("1000"))]
        self.assertEqual(reconcile(rows)["reason"], "unsupported_unit_or_scale")

    def test_repair_allowance_survives_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = SubtaskJournal(Path(directory), {}, RunBudget())
            journal.repair_used()
            self.assertEqual(SubtaskJournal(Path(directory), {}, RunBudget()).usage["repairs"], 1)

    def test_structured_source_and_scenario_endpoints(self):
        with TestClient(app) as client:
            response = client.post("/api/source-reconciliation", json={"company": "MSFT", "metric": "revenue", "fiscal_year": 2024})
            self.assertEqual(response.status_code, 200, response.text)
            response = client.post("/api/scenarios", json={"company": "MSFT", "fiscal_year": 2024, "margin": "net_margin",
                                    "revenue_changes_percent": [0, 10], "margin_changes_percentage_points": [0, 2]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()["points"]), 4)
            self.assertTrue(response.json()["assumption_based"])


class RecoveryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_company_draft_survives_interruption_with_cumulative_accounting(self):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        plan = RequestPlan(intent="explanation", companies=["MSFT", "AAPL"], fiscal_year=2024,
                           metrics=["revenue"], topic="risk")
        chunks = {company: next(item for item in load_index(ROOT, mode="bm25", profile="contextual").chunks
            if item["company"] == company and item["report_year"] == 2024 and item["kind"] != "table"
            and "risk" in item["body_text"].lower() and len(item["body_text"]) > 150) for company in plan.companies}
        requests = []
        class Client:
            async def __aenter__(self):return self
            async def __aexit__(self, *args):pass
            def __init__(self):self.chat = SimpleNamespace(completions=self)
            async def create(self, **kwargs):
                payload = decode_context(kwargs["messages"][1]["content"])
                company = payload["company"];requests.append(company)
                text = json.dumps({"status": "complete", "claims": [{"statement": "The report discloses material business risks.",
                    "chunk_id": "passage:" + chunks[company]["id"], "quote": chunks[company]["body_text"][:120]}]})
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                    usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10, total_tokens=20))
        original = SubtaskJournal.save
        failed = False
        def crash_after_save(journal, key, value):
            nonlocal failed
            original(journal, key, value)
            if not failed:
                failed = True
                raise RuntimeError("Controlled interruption after completed company")
        with tempfile.TemporaryDirectory() as directory:
            args = {"request_plan": plan, "backend": "direct", "checkpoint_root": Path(directory),
                    "corpus_profile": "contextual"}
            with patch("finevolab.grounded.async_client", side_effect=lambda settings: Client()), patch(
                "finevolab.retrieval.explanation_passages", side_effect=lambda root, query, company, *a: [chunks[company]]):
                with patch.object(SubtaskJournal, "save", crash_after_save), self.assertRaises(RuntimeError):
                    await analyze_grounded(ModelConfig("model", "https://example.com/", "unused"), store, "Disclosed risks", {"system": ""}, **args)
                output = await analyze_grounded(ModelConfig("model", "https://example.com/", "unused"), store, "Disclosed risks", {"system": ""}, **args)
        self.assertEqual(requests, ["MSFT", "AAPL"])
        self.assertEqual(output["usage"]["requests"], 1)
        self.assertEqual(output["logical_usage"]["calls"], 2)
        self.assertEqual(validate_answer(store, output)["status"], "passed")
        self.assertTrue(any(item.get("event") == "subtask_restored" for item in output["events"]))


if __name__ == "__main__":unittest.main()
