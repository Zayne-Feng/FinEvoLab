"""Verify numeric-only reports identify estimates and omit unsupported qualitative sections."""

from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "finrobot_equity/core/src"))
from modules.professional_pdf_report import generate_professional_report


class FmpNumericReportTests(unittest.TestCase):
    def test_pdf_has_numeric_scope_without_empty_qualitative_headings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "numeric_report.pdf"
            generate_professional_report(str(path), {
                "company_ticker": "MSFT", "company_name_full": "Microsoft Corporation",
                "numeric_only": True, "rating": "Not assessed", "report_date": "October 2026",
                "closing_price_date": "2026-10-23 20:00 UTC",
                "analysis_df": pd.DataFrame({"metrics": ["Revenue", "EBITDA", "EPS"],
                                             "2026A": [100, 20, 3], "2027E": [105, 21, 3.15]})})
            text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        self.assertIn("Not assessed", text)
        self.assertIn("assumption-based", text)
        self.assertIn("Snapshot:", text)
        self.assertIn("2026-10-23 20:00 UTC", text)
        for phrase in ("Competitive Landscape", "Risk Factors", "Company Overview", "expand its market presence"):
            self.assertNotIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
