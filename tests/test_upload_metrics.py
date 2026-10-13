"""Verify monetary scale, signs, periods, conflicts, and source locations."""

import io
from pathlib import Path
import tempfile
import unittest

from reportlab.pdfgen.canvas import Canvas
from finevolab.uploads import ingest_upload
from finevolab.upload_metrics import parse_date


def inline_report(value="245,122", sign="", duplicate=""):
    return (f'<html><body><ix:nonNumeric name="dei:DocumentFiscalYearFocus">2024</ix:nonNumeric>'
            '<ix:nonNumeric name="dei:DocumentPeriodEndDate">2024-06-30</ix:nonNumeric>'
            '<ix:nonNumeric name="dei:TradingSymbol">MSFT</ix:nonNumeric>'
            '<xbrli:context id="annual"><xbrli:period><xbrli:startDate>2023-07-01</xbrli:startDate>'
            '<xbrli:endDate>2024-06-30</xbrli:endDate></xbrli:period></xbrli:context>'
            '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
            f'<ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" '
            f'contextRef="annual" unitRef="usd" scale="6" sign="{sign}">{value}</ix:nonFraction>'
            f'{duplicate}</body></html>').encode()


class UploadMetricTests(unittest.TestCase):
    def test_fiscal_dates_preserve_punctuation_across_nested_elements(self):
        for value in ("September 28 , 2024", "June30,2024", "2024-06-30"):
            self.assertEqual(parse_date(value).year, 2024)

    def test_inline_scale_and_actual_period_match_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            result = ingest_upload(inline_report(), ".html", "MSFT", 2024, Path(directory))["metric_results"]
        self.assertEqual(result["status"], "matched_snapshot")
        self.assertEqual(result["observations"][0]["metric"]["value"], "245122000000")
        self.assertEqual(result["observations"][0]["metric"]["period_start"], "2023-07-01")
        self.assertIn("context=annual", result["observations"][0]["evidence"]["locator"])

    def test_negative_or_conflicting_disclosures_are_not_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            negative = ingest_upload(inline_report(sign="-"), ".html", "MSFT", 2024, Path(directory))["metric_results"]
        self.assertEqual(negative["status"], "conflict")
        self.assertFalse(negative["promoted_to_corpus"])
        duplicate = '<ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="annual" unitRef="usd" scale="6">1</ix:nonFraction>'
        with tempfile.TemporaryDirectory() as directory:
            result = ingest_upload(inline_report(duplicate=duplicate), ".html", "MSFT", 2024, Path(directory))["metric_results"]
        self.assertFalse(result["observations"])
        self.assertTrue(any(item["reason"] == "conflicting_annual_contexts" for item in result["unresolved"]))

    def test_company_and_fiscal_year_metadata_conflicts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "company conflicts"):
                ingest_upload(inline_report(), ".html", "AAPL", 2024, Path(directory))
            with self.assertRaisesRegex(ValueError, "fiscal year conflicts"):
                ingest_upload(inline_report(), ".html", "MSFT", 2023, Path(directory))

    def test_nested_date_spans_and_excluded_footnotes_do_not_change_value(self):
        raw = inline_report().replace(b"2024-06-30</ix:nonNumeric>", b"<span>June 30, </span><span>2024</span></ix:nonNumeric>")
        raw = raw.replace(b">245,122</ix:nonFraction>", b">245,122<ix:exclude>1</ix:exclude></ix:nonFraction>")
        with tempfile.TemporaryDirectory() as directory:
            result = ingest_upload(raw, ".html", "MSFT", 2024, Path(directory))["metric_results"]
        self.assertEqual(result["status"], "matched_snapshot")

    def test_explicit_pdf_table_is_verified_and_missing_period_is_pending(self):
        lines = ["Amounts in millions of USD", "FY2024: 2023-07-01 to 2024-06-30",
                 "FY2023: 2022-07-01 to 2023-06-30", "Metric 2024 2023", "Revenue 245,122 211,915"]
        for include_period in (True, False):
            buffer = io.BytesIO()
            canvas = Canvas(buffer)
            for index, line in enumerate(lines):
                if include_period or not line.startswith("FY"):
                    canvas.drawString(40, 750 - index * 20, line)
            canvas.save()
            with tempfile.TemporaryDirectory() as directory:
                result = ingest_upload(buffer.getvalue(), ".pdf", "MSFT", 2024, Path(directory))["metric_results"]
            self.assertEqual(result["status"], "matched_snapshot" if include_period else "review_required")
            if include_period:
                self.assertEqual(len(result["observations"]), 2)
                self.assertTrue(all(item["evidence"]["locator"].startswith("page=1") for item in result["observations"]))


if __name__ == "__main__":
    unittest.main()
