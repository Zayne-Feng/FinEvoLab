"""Verify task-scoped scoring against independent financial references."""

import copy
import json
from pathlib import Path
import unittest

from finevolab.grounded import RequestPlan, assemble_numeric
from finevolab.scope_evaluation import assess_scope
from finevolab.tools import FinancialStore

ROOT = Path(__file__).resolve().parents[1]


class ScopeEvaluationTests(unittest.TestCase):
    def test_financial_reference_rejects_a_different_valid_metric(self):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        plan = RequestPlan(intent="metric", companies=["MSFT"], fiscal_year=2024, metrics=["revenue"])
        events = []
        numeric = assemble_numeric(plan, store, events)
        case = {"category": "metric_query", "operation": "metric", "reference_answer": numeric,
                "companies": ["MSFT"], "fiscal_year": 2024}
        output = {"answer": numeric, "answer_type": "numeric", "request": plan.model_dump(), "events": events}
        self.assertEqual(assess_scope(ROOT, store, case, output)["score"], 1)
        other = plan.model_copy(update={"metrics": ["net_income"]})
        changed = {**output, "answer": assemble_numeric(other, store, []), "request": other.model_dump()}
        self.assertEqual(assess_scope(ROOT, store, case, changed)["score"], 0)

    def test_frozen_bank_semantics_and_hashes(self):
        from finevolab.experiments import sha256
        directory = ROOT / "data/evaluation/scope_v3"
        frozen = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        keys = []
        for split, digest in frozen["split_sha256"].items():
            self.assertEqual(sha256(directory / f"{split}.json"), digest)
            cases = json.loads((directory / f"{split}.json").read_text(encoding="utf-8"))["tasks"]
            self.assertEqual(len(cases), frozen["counts"][split])
            keys.extend(case["semantic_key"] for case in cases)
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
