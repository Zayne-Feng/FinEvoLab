"""Verify narrative review, shared repair budgets, and numerical preservation."""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from finevolab.config import ModelConfig
from finevolab.grounded import RequestPlan, analyze_grounded
from finevolab.retrieval import load_index
from finevolab.runtime import RunBudget, BudgetExceeded
from finevolab.tools import FinancialStore
from finevolab.validation import validate_answer
from test_grounded import completion

ROOT = Path(__file__).resolve().parents[1]


class ReviewExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = FinancialStore(ROOT / "data/processed/reports.json")
        self.plan = RequestPlan(intent="hybrid", numeric_intent="metric", companies=["MSFT"], fiscal_year=2024,
                                report_year=2024, topic="risk", metrics=["revenue"])
        self.chunk = next(item for item in load_index(ROOT, mode="bm25", profile="contextual").chunks
                          if item["company"] == "MSFT" and item["report_year"] == 2024
                          and item["kind"] != "table" and len(item["body_text"]) > 150 and "risk" in item["body_text"].lower())
        self.draft = {"status": "complete", "claims": [{"statement": "Disclosed risks may affect business performance.",
            "chunk_id": "passage:" + self.chunk["id"], "quote": self.chunk["body_text"][:120]}]}

    async def execute(self, texts, budget=None):
        payloads = [json.dumps(item) if not isinstance(item, str) else item for item in texts]
        class Client:
            base_url = "https://example.com/"
            def __init__(inner): inner.chat = SimpleNamespace(completions=inner)
            async def __aenter__(inner): return inner
            async def __aexit__(inner, *args): pass
            async def create(inner, **kwargs): return completion(payloads.pop(0))
        with patch("finevolab.grounded.async_client", return_value=Client()), patch(
                "finevolab.retrieval.explanation_passages", return_value=[{**self.chunk, "score": 1.0}]):
            return await analyze_grounded(ModelConfig("model", "https://example.com/", "unused"), self.store,
                "Discuss risks affecting MSFT FY2024 revenue and give its revenue.", {"system": ""},
                backend="direct", review_mode="reviewer", budget=budget)

    def review(self, verdict):
        return {"complete": True, "claims": [{"index": 0, "verdict": verdict, "reason": "Evidence support checked."}]}

    async def test_supported_review_preserves_numeric_answer_and_usage(self):
        output = await self.execute([self.plan.model_dump(), self.draft, self.review("supported")])
        validate_answer(self.store, output)
        self.assertEqual(output["review"]["status"], "passed")
        self.assertEqual(output["usage"]["requests"], 3)
        self.assertEqual(output["answer"]["metrics"][0]["value"], "245122000000")
        self.assertEqual(output["repair"]["count"], 0)

    async def test_reviewer_rejection_repairs_once_then_rechecks(self):
        output = await self.execute([self.plan.model_dump(), self.draft, self.review("insufficient"), self.draft, self.review("supported")])
        self.assertEqual(output["repair"]["count"], 1)
        self.assertEqual(output["usage"]["requests"], 5)
        self.assertTrue(output["review"]["repaired"])
        self.assertEqual(output["review"]["status"], "passed")
        validate_answer(self.store, output)

    async def test_repeated_rejection_returns_partial_without_unsupported_claims(self):
        output = await self.execute([self.plan.model_dump(), self.draft, self.review("insufficient"), self.draft, self.review("contradicted")])
        self.assertEqual(output["overall_status"], "partial")
        self.assertEqual(output["review"]["status"], "rejected")
        self.assertEqual(output["narrative"]["claims"], [])
        self.assertEqual(output["answer"]["metrics"][0]["value"], "245122000000")
        validate_answer(self.store, output)

    async def test_planner_repair_and_review_share_one_repair_allowance(self):
        output = await self.execute(["invalid", self.plan.model_dump(), self.draft, self.review("insufficient")])
        self.assertEqual(output["repair"]["count"], 1)
        self.assertEqual(output["usage"]["requests"], 4)
        self.assertEqual(output["review"]["status"], "rejected")
        self.assertFalse(output["review"]["repaired"])

    async def test_review_cannot_exceed_model_budget(self):
        with self.assertRaises(BudgetExceeded):
            await self.execute([self.plan.model_dump(), self.draft], RunBudget(max_model_calls=2,max_context_chars=60000))

    async def test_malformed_review_cannot_pass(self):
        invalid = {"complete": True, "claims": []}
        with self.assertRaises(ValueError):
            await self.execute([self.plan.model_dump(), self.draft, invalid, invalid])

    async def test_review_status_and_decisions_cannot_disagree_with_completed_narrative(self):
        import copy
        output = await self.execute([self.plan.model_dump(), self.draft, self.review("supported")])
        self.assertEqual(validate_answer(self.store, output)["narrative"]["semantic_support"], "online_review_passed")
        for field, value in (("status", "rejected"), ("complete", False), ("claims", [])):
            wrong = copy.deepcopy(output)
            wrong["review"][field] = value
            with self.assertRaises(ValueError):validate_answer(self.store, wrong)
