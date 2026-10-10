"""Verify retrieval filters, deduplication, and rank fusion."""

import json
from pathlib import Path
import unittest

import numpy as np
from finevolab.retrieval import ReportIndex, reciprocal_rank_fusion


ROOT = Path(__file__).resolve().parents[1]


class RetrievalTests(unittest.TestCase):
    def test_company_and_report_year_are_filtered_before_ranking(self):
        chunks = json.loads((ROOT / "data/indexes/structured.json").read_text())
        results = ReportIndex(chunks).search("Total revenue Year Ended June", company="MSFT", year=2024)
        self.assertTrue(results)
        self.assertTrue(all(item["company"] == "MSFT" and item["report_year"] == 2024 for item in results))
        self.assertEqual(ReportIndex(chunks).search("revenue", company="MISSING"), [])

    def test_table_preserves_header_and_units(self):
        chunks = json.loads((ROOT / "data/indexes/structured.json").read_text())
        statements = [item for item in chunks if item["kind"] == "table"
                      and item["section"] == "income-statements" and "Total revenue" in item["text"]]
        self.assertGreaterEqual(len(statements), 2)
        self.assertTrue(all("In millions" in item["text"] and "Year Ended June 30" in item["text"]
                            for item in statements))

    def test_no_partial_evidence_when_context_budget_is_too_small(self):
        chunks = json.loads((ROOT / "data/indexes/structured.json").read_text())
        self.assertEqual(ReportIndex(chunks).search("revenue", max_chars=1), [])

    def test_rrf_ignores_duplicates_within_one_ranking(self):
        fused = reciprocal_rank_fusion([["a", "a", "b"], ["b", "a"]])
        self.assertEqual(dict(fused)["a"], dict(fused)["b"])

    def test_vector_filter_precedes_rank_fusion(self):
        class Encoder:
            def encode(self, texts, query=False):
                return np.array([[1.0, 0.0]])
        chunks = [
            {"id": "a", "company": "MSFT", "report_year": 2024, "report_type": "annual", "text": "Revenue increased"},
            {"id": "b", "company": "OTHER", "report_year": 2024, "report_type": "annual", "text": "Revenue declined"},
        ]
        index = ReportIndex(chunks, np.array([[0.9, 0.1], [1.0, 0.0]]), Encoder())
        results = index.search("sales", company="MSFT", mode="hybrid")
        self.assertEqual([item["id"] for item in results], ["a"])


if __name__ == "__main__":
    unittest.main()
