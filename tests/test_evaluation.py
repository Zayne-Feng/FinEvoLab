"""Reject numerically plausible answers with incorrect source semantics."""

import copy
import json
from pathlib import Path
import unittest

from finevolab.evaluation import reference_answer, score_task
from finevolab.retrieval import ReportIndex
from finevolab.tools import FinancialStore

ROOT = Path(__file__).resolve().parents[1]


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads((ROOT / "data/evaluation/development.json").read_text(encoding="utf-8"))["tasks"]
        self.metric = next(case for case in self.cases if case["category"] == "metric_query")

    def test_wrong_units_dates_and_citations_fail_independently(self):
        for field, value, dimension in (("unit", "EUR", "units"), ("period_end", "2030-12-31", "periods"),
                                        ("evidence_id", "invented", "citations"), ("value", "0", "numeric")):
            output = reference_answer(self.metric)
            output["metrics"][0][field] = value
            result = score_task(self.metric, {"answer": output})
            self.assertEqual(result["score"], 0)
            self.assertFalse(result["checks"][dimension])

    def test_duplicate_metric_is_not_silent_success(self):
        output = reference_answer(self.metric)
        output["metrics"].append(copy.deepcopy(output["metrics"][0]))
        self.assertEqual(score_task(self.metric, {"answer": output})["score"], 0)

    def test_nonfinite_and_rounded_growth_are_rejected(self):
        case = next(case for case in self.cases if case["category"] == "annual_growth")
        for value in ("NaN", "Infinity", "0"):
            output = reference_answer(case)
            output["calculations"][0]["value"] = value
            self.assertEqual(score_task(case, {"answer": output})["score"], 0)

    def test_unknown_fields_and_false_missing_data_are_rejected(self):
        output = reference_answer(self.metric)
        output["unexpected"] = True
        self.assertFalse(score_task(self.metric, {"answer": output})["checks"]["schema"])
        output = reference_answer(self.metric)
        output.update(status="insufficient_data", metrics=[], evidence_ids=[])
        self.assertEqual(score_task(self.metric, {"answer": output})["score"], 0)

    def test_tool_correctness_requires_matching_source_bound_result(self):
        answer = reference_answer(self.metric)
        event = {"tool": "get_financial_metrics", "output": {**answer["metrics"][0], "value": "0"}}
        self.assertFalse(score_task(self.metric, {"answer": answer, "events": [event]})["tool_correct"])
        event["output"] = answer["metrics"][0]
        self.assertTrue(score_task(self.metric, {"answer": answer, "events": [event]})["tool_correct"])
        result = score_task(self.metric, {"answer": answer, "events": [
            {"event": "request_intent_resolved", "original": {}, "resolved": {}}, event,
            {"event": "quote_whitespace_resolved", "chunk_id": "passage"}]})
        self.assertEqual(result["score"], 1)
        self.assertTrue(result["tool_correct"])

    def test_small_homogeneous_corpus_keeps_discriminative_matches(self):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        chunks = []
        for year in (2022, 2023, 2024):
            for name in ("revenue", "operating_income", "net_income"):
                metric = store.get_metric("AMZN", name, year).model_dump(mode="json")
                chunks.append({"id": metric["evidence_id"], "company": "AMZN", "report_year": year,
                               "text": f"AMZN FY{year} {name.replace('_', ' ')}\n" + json.dumps(metric)})
        results = ReportIndex(chunks).search("Find AMZN FY2022 annual net_income with currency, units, period, and source.", company="AMZN")
        self.assertTrue(results)
        self.assertIn(store.get_metric("AMZN", "net_income", 2022).evidence_id, {item["id"] for item in results})


if __name__ == "__main__":
    unittest.main()
