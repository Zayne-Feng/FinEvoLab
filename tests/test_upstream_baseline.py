"""Verify pinned report computation and identical observable input scopes."""

from decimal import Decimal
from pathlib import Path
import unittest

from finevolab.tools import FinancialStore
from finevolab.upstream_baseline import report_components, snapshot_rows
from scripts.run_upstream_comparison import scoped_store, score

ROOT = Path(__file__).resolve().parents[1]


class UpstreamBaselineTests(unittest.TestCase):
    def test_original_processor_computes_displayed_growth(self):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        _, processor, hashes = report_components(ROOT)
        self.assertEqual(hashes["finrobot_equity/core/src/modules/financial_data_processor.py"],
            "0cfc251b8f8a1fba8dc16ba1ca52d6eb92ccf160a5bc881c24adfa885425c726")
        table = processor.extract_historical_metrics_from_api_data({"income_statement": snapshot_rows(store, "MSFT", 2023)})
        self.assertEqual(table.loc[table["metrics"] == "Revenue Growth", "2023A"].iloc[0], "6.9%")

    def test_scope_contains_exactly_the_shared_company_year_observations(self):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        case = {"company": "MSFT", "fiscal_year": 2023}
        selected = scoped_store(store, case)
        self.assertEqual(len(selected.metrics), 6)
        self.assertEqual({m.company for m in selected.metrics}, {"MSFT"})
        self.assertEqual({m.fiscal_year for m in selected.metrics}, {2022, 2023})
        with self.assertRaises(ValueError):
            selected.get_metric("AAPL", "revenue", 2023)
        values = {"revenue_current": "211915000000", "revenue_previous": "198270000000", "growth_percent": "6.9"}
        self.assertTrue(all(score(values, store, case).values()))
        self.assertFalse(score({**values, "growth_percent": str(Decimal("7.0"))}, store, case)["displayed_growth"])
