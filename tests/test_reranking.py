"""Verify pinned ranking configuration, score integrity, and table retention."""

import copy
from pathlib import Path
from threading import RLock
import unittest
from unittest.mock import patch

import numpy as np
from pydantic import ValidationError

from finevolab.reranking import PassageReranker, RerankerConfig, rerank_passages
from finevolab.retrieval import explanation_passages

ROOT = Path(__file__).resolve().parents[1]


class RerankingTests(unittest.TestCase):
    def ranker(self, scores):
        class Model:
            def predict(self, pairs, **kwargs):
                return np.asarray(scores)
        ranker = PassageReranker.__new__(PassageReranker)
        ranker.model, ranker.lock = Model(), RLock()
        ranker.config = RerankerConfig(model="test", revision="a" * 40)
        return ranker

    def test_pinned_cpu_configuration_is_required(self):
        for extra in ({"revision": "main"}, {"device": "cuda"}, {"max_candidates": 0}):
            with self.assertRaises(ValidationError):
                RerankerConfig.model_validate({"model": "test", "revision": "a" * 40, **extra})

    def test_scores_reorder_without_mutating_sources(self):
        sources = [{"id": "b", "text": "Revenue"}, {"id": "a", "text": "Revenue"}]
        before = copy.deepcopy(sources)
        ranked = self.ranker([1, 1]).rank("query", sources)
        self.assertEqual([r["id"] for r in ranked], ["a", "b"])
        self.assertEqual(sources, before)
        self.assertEqual(ranked[0]["rerank_score"], 1.0)

    def test_invalid_score_counts_and_nonfinite_scores_are_rejected(self):
        for scores in ([float("nan")], [float("inf")], []):
            with self.assertRaises(ValueError):
                self.ranker(scores).rank("query", [{"id": "a", "text": "Revenue"}])

    def test_candidate_budget_is_checked_before_model_loading(self):
        with patch("finevolab.reranking._load") as load:
            with self.assertRaisesRegex(ValueError, "candidate budget"):
                rerank_passages(ROOT, "query", [{"id": str(i), "text": "Revenue"} for i in range(21)])
            load.assert_not_called()

    def test_reordering_preserves_the_required_table_and_output_budget(self):
        args = (ROOT, "Explain MSFT FY2024 revenue growth", "MSFT", 2024, "revenue")
        reference = explanation_passages(*args, variant="pooled", profile="contextual")
        with patch("finevolab.reranking.rerank_passages", side_effect=lambda root, query, passages: list(reversed(passages))):
            ranked = explanation_passages(*args, variant="reranked", profile="contextual")
        self.assertEqual(ranked[0]["id"], reference[0]["id"])
        self.assertEqual(ranked[0]["kind"], "table")
        self.assertLessEqual(len(ranked), 5)
        self.assertLessEqual(sum(len(p["text"]) for p in ranked), 10000)


if __name__ == "__main__":
    unittest.main()
