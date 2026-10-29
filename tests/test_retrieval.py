"""Verify retrieval filters, deduplication, and rank fusion."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch

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


class RetrievalCacheTests(unittest.TestCase):
    def setUp(self):
        self.chunks = [
            {"id": "a", "company": "MSFT", "report_year": 2024, "report_type": "annual", "text": "Revenue increased cloud services"},
            {"id": "b", "company": "MSFT", "report_year": 2024, "report_type": "annual", "text": "Revenue declined products"},
            {"id": "c", "company": "MSFT", "report_year": 2023, "report_type": "annual", "text": "Revenue prior period"},
            {"id": "d", "company": "AAPL", "report_year": 2024, "report_type": "annual", "text": "Revenue devices"},
        ]

    def test_cached_and_uncached_scores_are_identical(self):
        cached, reference = ReportIndex(self.chunks), ReportIndex(self.chunks, cache_size=0)
        for _ in range(2):
            for company in (None, "MSFT", "AAPL", "MISSING"):
                for year in (None, 2024, 2023):
                    for query in ("revenue cloud", "prior period", "??", ""):
                        self.assertEqual(cached.search(query, company, year), reference.search(query, company, year))

    def test_scope_cache_is_bounded_and_filters_remain_distinct(self):
        from rank_bm25 import BM25Okapi
        with patch("finevolab.retrieval.BM25Okapi", wraps=BM25Okapi) as build:
            index = ReportIndex(self.chunks, cache_size=1)
            index.search("revenue", "MSFT", 2024)
            index.search("cloud", "MSFT", 2024)
            self.assertEqual(build.call_count, 1)
            index.search("revenue", "AAPL", 2024)
            index.search("revenue", "MSFT", 2024)
            self.assertEqual(build.call_count, 3)
            self.assertEqual(len(index._scopes), 1)

    def test_query_vectors_are_reused_without_cross_index_sharing(self):
        class Encoder:
            def __init__(self):
                self.calls = 0
            def encode(self, texts, query=False):
                self.calls += 1
                return np.array([[1.0, 0.0]])
        encoder = Encoder()
        vectors = np.tile([[1.0, 0.0]], (len(self.chunks), 1))
        index = ReportIndex(self.chunks, vectors, encoder, cache_size=1)
        index.search("revenue", "MSFT", 2024, mode="hybrid")
        index.search("revenue", "AAPL", 2024, mode="vector")
        self.assertEqual(encoder.calls, 1)
        other = ReportIndex(self.chunks, vectors, encoder)
        other.search("revenue", mode="vector")
        self.assertEqual(encoder.calls, 2)
        for i in range(5):
            index.search(f"query {i}", mode="vector")
        self.assertEqual(len(index._queries), 4)


if __name__ == "__main__":
    unittest.main()
