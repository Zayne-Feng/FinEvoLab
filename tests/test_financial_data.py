"""Verify financial period, unit, and evidence invariants."""

from decimal import Decimal
import json
from pathlib import Path
import unittest

from bs4 import BeautifulSoup
from finevolab.tools import FinancialStore


ROOT = Path(__file__).resolve().parents[1]


class FinancialDataTests(unittest.TestCase):
    def setUp(self):
        self.store = FinancialStore(ROOT / "data/processed/reports.json")

    def test_income_growth_uses_latest_disclosure(self):
        result = self.store.growth("MSFT", "revenue", 2024)
        self.assertEqual(result.inputs[0].value, Decimal("211915000000"))
        self.assertEqual(result.inputs[1].value, Decimal("245122000000"))
        self.assertLess(abs(result.value - Decimal("15.66996201307128")), Decimal("1e-12"))
        self.assertEqual(result.inputs[0].report_year,
                         max(m.report_year for m in self.store.metrics if m.company == "MSFT"
                             and m.name == "revenue" and m.fiscal_year == 2023))
        self.assertEqual(result.inputs[1].period_start, "2023-07-01")

    def test_company_aliases_resolve_before_query_and_distinctness(self):
        self.assertEqual(self.store.get_metric("Microsoft", "revenue", 2024).company, "MSFT")
        self.assertEqual(self.store.growth("microsoft corporation", "revenue", 2024).company, "MSFT")
        self.assertEqual(self.store.get_metric("Apple Inc.", "net_income", 2024).company, "AAPL")
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.store.compare(["MSFT", "Microsoft"], "revenue", 2024)

    def test_evidence_resolves_to_original_row(self):
        data = json.loads((ROOT / "data/processed/reports.json").read_text())
        for report in data["reports"]:
            if report["source"].get("profile") != "microsoft_annual":
                continue
            soup = BeautifulSoup((ROOT / report["source"]["path"]).read_bytes(), "html.parser")
            for evidence in report["evidence"]:
                selector, table_index, row_index = evidence["locator"].split("|")
                table = soup.select(selector.removeprefix("css:"))[int(table_index.split("=")[1])]
                row = table.find_all("tr")[int(row_index.split("=")[1])]
                text = " | ".join(" ".join(cell.stripped_strings) for cell in row.find_all(["td", "th"]))
                self.assertEqual(evidence["text"].splitlines()[-1], text)

    def test_overlapping_disclosures_agree(self):
        for name in ("revenue", "operating_income", "net_income"):
            for year in (2022, 2023):
                values = {m.value for m in self.store.metrics if m.company == "MSFT" and m.name == name and m.fiscal_year == year}
                self.assertEqual(len(values), 1)

    def test_missing_year_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.store.growth("MSFT", "revenue", 2020)

    def test_zero_denominator_is_rejected(self):
        self.store.get_metric("MSFT", "revenue", 2023).value = Decimal(0)
        with self.assertRaisesRegex(ValueError, "zero baseline"):
            self.store.growth("MSFT", "revenue", 2024)

    def test_unit_conflict_is_rejected(self):
        self.store.get_metric("MSFT", "revenue", 2023).unit = "EUR"
        with self.assertRaisesRegex(ValueError, "not comparable"):
            self.store.growth("MSFT", "revenue", 2024)

    def test_balance_cannot_be_used_as_annual_flow(self):
        for year in (2023, 2024):
            self.store.get_metric("MSFT", "revenue", year).period_kind = "instant"
        with self.assertRaisesRegex(ValueError, "not comparable"):
            self.store.growth("MSFT", "revenue", 2024)

    def test_sec_json_pointer_resolves_to_original_fact(self):
        data = json.loads((ROOT / "data/processed/reports.json").read_text())
        for report in data["reports"]:
            if report["source"].get("profile") != "sec_companyfacts":
                continue
            document = json.loads((ROOT / report["source"]["path"]).read_text())
            for item in report["evidence"]:
                current = document
                for part in item["locator"].strip("/").split("/"):
                    current = current[int(part)] if isinstance(current, list) else current[part]
                self.assertEqual(current["val"], json.loads(item["text"])["val"])

    def test_cross_company_comparison_preserves_fiscal_dates(self):
        result = self.store.compare(["MSFT", "AAPL"], "revenue", 2024)
        self.assertFalse(result.periods_aligned)
        self.assertEqual(result.values[0].value, Decimal("245122000000"))
        self.assertEqual(result.values[1].currency, "USD")


if __name__ == "__main__":
    unittest.main()
