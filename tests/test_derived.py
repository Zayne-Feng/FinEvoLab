"""Verify margin arithmetic, audit integrity, tool execution, and report units."""

import asyncio
from copy import deepcopy
from decimal import Decimal, localcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfReader

from finevolab.agent import make_tools
from finevolab.derived import DERIVED_METRICS, DerivedResult, validate_derived
from finevolab.evaluation import TaskAnswer, score_task
from finevolab.reporting import build_analysis_report
from finevolab.runtime import BudgetExceeded, RunBudget, ToolExecutor, fingerprint
from finevolab.tools import BoundedFinancialStore, FinancialStore
from finevolab.validation import validate_answer
from finevolab.web import app

ROOT = Path(__file__).resolve().parents[1]


class DerivedTests(unittest.TestCase):
    def setUp(self):
        self.store = FinancialStore(ROOT / "data/processed/reports.json")

    def set_values(self):
        for year, revenue, operating, net in ((2023, "100", "20", "10"), (2024, "200", "60", "40")):
            for name, value in (("revenue", revenue), ("operating_income", operating), ("net_income", net)):
                self.store.get_metric("MSFT", name, year).value = Decimal(value)

    def answer(self, results):
        return {"answer": TaskAnswer(status="complete", answer="Source-bound profit margins.", metrics=[],
            calculations=results, periods_aligned=None,
            evidence_ids=sorted({key for item in results for key in item.evidence_ids})).model_dump(mode="json")}

    def test_known_ratios_and_percentage_point_changes(self):
        self.set_values()
        for metric, value, unit, count in (("operating_margin", "30", "percent", 2),
                ("net_margin", "20", "percent", 2), ("operating_margin_change", "10", "percentage_points", 4),
                ("net_margin_change", "10", "percentage_points", 4)):
            with self.subTest(metric=metric):
                result = self.store.derive("Microsoft", metric, 2024)
                self.assertEqual(result.company, "MSFT")
                self.assertEqual((result.value, result.unit, len(result.inputs)), (Decimal(value), unit, count))
                self.assertEqual(len(result.audit), count + 4)
                self.assertEqual(validate_derived(self.store, result), result)

    def test_snapshot_values_match_independent_decimal_reference(self):
        for company in sorted({item.company for item in self.store.metrics}):
            years = sorted({item.fiscal_year for item in self.store.metrics if item.company == company})
            for year in years:
                for name, profit in (("operating_margin", "operating_income"), ("net_margin", "net_income")):
                    with self.subTest(company=company, year=year, metric=name):
                        numerator = self.store.get_metric(company, profit, year).value
                        denominator = self.store.get_metric(company, "revenue", year).value
                        with localcontext() as context:
                            context.prec = 50
                            reference = numerator / denominator * 100
                        result = self.store.derive(company, name, year)
                        self.assertLess(abs(result.value - reference), Decimal("1e-29"))
                        self.assertEqual(validate_derived(self.store, result), result)

    def test_disclosure_years_are_preserved_without_confusing_fact_periods(self):
        result = self.store.derive("GOOGL", "net_margin", 2024)
        self.assertNotEqual(result.inputs[0].report_year, result.inputs[1].report_year)
        self.assertEqual(result.inputs[0].period_end, result.inputs[1].period_end)
        self.assertIn("latest_available_disclosure_per_input", [item.rule for item in result.audit])
        self.assertEqual(validate_derived(self.store, result), result)

    def test_losses_preserve_negative_margin_and_signed_change(self):
        self.set_values()
        self.store.get_metric("MSFT", "net_income", 2024).value = Decimal("-20")
        self.assertEqual(self.store.derive("MSFT", "net_margin", 2024).value, Decimal("-10"))
        self.assertEqual(self.store.derive("MSFT", "net_margin_change", 2024).value, Decimal("-20"))

    def test_zero_and_negative_revenue_are_rejected(self):
        for value in ("0", "-1"):
            with self.subTest(value=value):
                self.store.get_metric("MSFT", "revenue", 2024).value = Decimal(value)
                with self.assertRaisesRegex(ValueError, "positive revenue"):
                    self.store.derive("MSFT", "net_margin", 2024)

    def test_nonfinite_inputs_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                self.store.get_metric("MSFT", "net_income", 2024).value = Decimal(value)
                with self.assertRaisesRegex(ValueError, "finite"):
                    self.store.derive("MSFT", "net_margin", 2024)

    def test_pair_definition_conflicts_are_rejected(self):
        for field, value in (("currency", "EUR"), ("unit", "million_USD"), ("period_kind", "instant"),
                             ("period_start", "2024-01-01"), ("period_end", "2024-12-31")):
            with self.subTest(field=field):
                store = FinancialStore(ROOT / "data/processed/reports.json")
                setattr(store.get_metric("MSFT", "operating_income", 2024), field, value)
                with self.assertRaises(ValueError):
                    store.derive("MSFT", "operating_margin", 2024)

    def test_nonannual_and_short_periods_are_rejected(self):
        for field, value in (("period_kind", "instant"), ("period_start", "2024-01-01")):
            store = FinancialStore(ROOT / "data/processed/reports.json")
            for name in ("operating_income", "revenue"):
                setattr(store.get_metric("MSFT", name, 2024), field, value)
            with self.assertRaises(ValueError):
                store.derive("MSFT", "operating_margin", 2024)

    def test_changes_require_consecutive_periods_and_same_currency(self):
        for field, value in (("period_end", "2023-06-29"), ("currency", "EUR")):
            store = FinancialStore(ROOT / "data/processed/reports.json")
            for name in ("operating_income", "revenue"):
                setattr(store.get_metric("MSFT", name, 2023), field, value)
            with self.assertRaises(ValueError):
                store.derive("MSFT", "operating_margin_change", 2024)

    def test_missing_or_conflicting_inputs_do_not_fall_back(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.store.derive("MSFT", "net_margin_change", 2020)
        self.store.invalid_metrics.add(("MSFT", "net_income", 2024))
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.store.derive("MSFT", "net_margin", 2024)

    def test_missing_or_wrong_company_evidence_is_rejected(self):
        identifier = self.store.get_metric("MSFT", "net_income", 2024).evidence_id
        self.store.evidence[identifier]["company"] = "AAPL"
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.store.derive("MSFT", "net_margin", 2024)
        del self.store.evidence[identifier]
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.store.derive("MSFT", "net_margin", 2024)

    def test_precision_is_independent_of_callers_decimal_context(self):
        expected = self.store.derive("MSFT", "net_margin_change", 2024)
        with localcontext() as context:
            context.prec = 7
            actual = self.store.derive("MSFT", "net_margin_change", 2024)
        self.assertEqual(actual, expected)

    def test_result_inputs_do_not_alias_store_objects(self):
        result = self.store.derive("MSFT", "net_margin", 2024)
        result.inputs[0].value = Decimal("1")
        self.assertNotEqual(self.store.get_metric("MSFT", "net_income", 2024).value, Decimal("1"))

    def test_unknown_operators_and_forged_results_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.store.derive("MSFT", "gross_margin", 2024)
        original = self.store.derive("MSFT", "net_margin", 2024).model_dump(mode="json")
        mutations = [("value", "1"), ("formula", "net_income * revenue"), ("unit", "percentage_points"),
                     ("source_signature", "0" * 64), ("data_version", "unknown"), ("previous_year", 2023)]
        for field, value in mutations:
            with self.subTest(field=field):
                forged = {**original, field: value}
                with self.assertRaises(ValueError):
                    validate_derived(self.store, forged)
        for key in ("inputs", "audit", "evidence_ids"):
            forged = deepcopy(original)
            forged[key] = []
            with self.assertRaises(ValueError):
                validate_derived(self.store, forged)
        forged = deepcopy(original)
        forged["audit"][0]["rule"] = "unchecked"
        with self.assertRaises(ValueError):
            validate_derived(self.store, forged)

    def test_answer_validation_supports_shared_inputs_and_checks_audits(self):
        results = [self.store.derive("MSFT", name, 2024) for name in sorted(DERIVED_METRICS)]
        output = self.answer(results)
        self.assertEqual(validate_answer(self.store, output)["status"], "passed")
        output["answer"]["calculations"][0]["audit"][0]["evidence_ids"] = []
        with self.assertRaises(ValueError):
            validate_answer(self.store, output)

    def test_duplicate_calculations_are_rejected(self):
        result = self.store.derive("MSFT", "net_margin", 2024)
        with self.assertRaisesRegex(ValueError, "Duplicate financial calculations"):
            validate_answer(self.store, self.answer([result, result]))

    def test_derived_scoring_checks_values_units_and_audit_metadata(self):
        result = self.store.derive("MSFT", "net_margin_change", 2024)
        output = self.answer([result])
        output["events"] = [{"tool": "calculate_metrics", "output": result.model_dump(mode="json")}]
        case = {"category": "annual_growth", "reference_answer": deepcopy(output["answer"])}
        self.assertEqual(score_task(case, output)["score"], 1.0)
        for field, value in (("unit", "percent"), ("value", "1"), ("audit", [])):
            forged = deepcopy(output)
            forged["answer"]["calculations"][0][field] = value
            self.assertEqual(score_task(case, forged)["score"], 0.0)

    def test_bounded_execution_caches_and_rechecks_audits(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            executor = ToolExecutor(RunBudget(max_tool_calls=2), events, {"source": self.store.source_signature}, Path(directory))
            bounded = BoundedFinancialStore(self.store, executor)
            first = bounded.derive("MSFT", "net_margin", 2024)
            self.assertEqual(first, bounded.derive("MSFT", "net_margin", 2024))
            self.assertTrue(any(item.get("cache_hit") for item in events))
            with self.assertRaises(BudgetExceeded):
                bounded.derive("MSFT", "net_margin", 2024)
            path = next(Path(directory).glob("*.json"))
            cached = json.loads(path.read_text())
            cached["value"]["value"] = "1"
            cached["value_hash"] = fingerprint({"value": cached["value"]})
            path.write_text(json.dumps(cached), encoding="utf-8")
            executor = ToolExecutor(RunBudget(), [], {"source": self.store.source_signature}, Path(directory))
            with self.assertRaisesRegex(ValueError, "audit differs"):
                BoundedFinancialStore(self.store, executor).derive("MSFT", "net_margin", 2024)

    def test_agent_tool_executes_derived_and_legacy_calculations(self):
        events = []
        tool = next(item for item in make_tools(self.store, events, None) if item.name == "calculate_metrics")
        self.assertIn("net_margin_change", tool.params_json_schema["properties"]["metric"]["enum"])
        for metric in ("net_margin", "operating_margin_change", "revenue"):
            payload = json.dumps({"company": "MSFT", "metric": metric, "fiscal_year": 2024})
            asyncio.run(tool.on_invoke_tool(None, payload))
            self.assertNotIn("error", events[-1]["output"])
        self.assertEqual(events[-1]["output"]["formula"], "(current / previous - 1) * 100")

    def test_api_returns_audited_results_and_rejects_invalid_requests(self):
        with TestClient(app) as client:
            for metric in sorted(DERIVED_METRICS):
                response = client.post("/api/derived-metrics", json={"company": "Microsoft", "fiscal_year": 2024, "metric": metric})
                self.assertEqual(response.status_code, 200, response.text)
                validate_derived(self.store, response.json())
            for payload in ({"company": "MSFT", "fiscal_year": 2024, "metric": "gross_margin"},
                            {"company": "MSFT", "fiscal_year": 2020, "metric": "net_margin"},
                            {"company": "MSFT", "fiscal_year": 2024, "metric": "net_margin", "extra": True}):
                self.assertEqual(client.post("/api/derived-metrics", json=payload).status_code, 422)

    def test_selected_uploads_never_fall_back_to_snapshot(self):
        self.store.metrics = []
        with TestClient(app) as client, patch.object(FinancialStore, "from_uploads", return_value=self.store) as selected:
            response = client.post("/api/derived-metrics", json={"company": "MSFT", "fiscal_year": 2024,
                "metric": "net_margin", "upload_ids": ["a" * 64]})
            self.assertEqual(response.status_code, 422)
            selected.assert_called_once()

    def test_html_and_pdf_report_change_as_percentage_points_with_audits(self):
        self.set_values()
        result = self.store.derive("MSFT", "operating_margin_change", 2024)
        with tempfile.TemporaryDirectory() as directory:
            artifacts = build_analysis_report(self.store, "Annual operating margin change", self.answer([result]), Path(directory))
            html = Path(artifacts["html"]).read_text(encoding="utf-8")
            text = "\n".join(page.extract_text() for page in PdfReader(artifacts["pdf"]).pages)
            for content in (html, text):
                self.assertIn("percentage_points", content)
                self.assertIn("source_snapshot_match", content)
                self.assertNotIn("operating_margin_change growth", content)


if __name__ == "__main__":
    unittest.main()
