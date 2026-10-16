"""Verify optimizer component restrictions and held-out data isolation."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from gepa.core.adapter import EvaluationBatch
from finevolab.optimization import FinancialGEPAAdapter, expand, flatten, select_validation_candidate
from finevolab.config import ModelConfig

ROOT = Path(__file__).resolve().parents[1]


class OptimizationTests(unittest.TestCase):
    def test_candidate_cannot_change_model_or_tool_implementation(self):
        candidate = flatten(json.loads((ROOT / "prompts/default.json").read_text()))
        with self.assertRaisesRegex(ValueError, "Only nonempty"):
            expand({**candidate, "model": "different-model"})

    def test_test_case_is_rejected_before_model_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = FinancialGEPAAdapter(ROOT, Path(directory), ModelConfig("example", "https://example.com/", "unused"))
            candidate = flatten(json.loads((ROOT / "prompts/default.json").read_text()))
            case = {"split": "test", "company": "MSFT", "fiscal_year": 2024}
            with patch("finevolab.optimization.analyze_revenue", AsyncMock()) as execute:
                with self.assertRaisesRegex(ValueError, "Test cases"):
                    adapter.evaluate([case], candidate)
                execute.assert_not_called()

    def test_validation_feedback_cannot_generate_prompt_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = FinancialGEPAAdapter(ROOT, Path(directory), ModelConfig("example", "https://example.com/", "unused"))
            evaluation = EvaluationBatch(outputs=[{}], scores=[0], trajectories=[{"case": {"split": "validation"}}])
            with self.assertRaisesRegex(ValueError, "Only development"):
                adapter.make_reflective_dataset({}, evaluation, ["system"])

    def test_validation_selection_uses_success_then_known_tokens(self):
        rows = []
        for candidate, score, tokens in (("expensive", 1, 200), ("cheap", 1, 100), ("incorrect", 0, 1)):
            for identifier in ("a", "b"):
                rows.append({"candidate": candidate, "case": {"id": identifier, "split": "validation"},
                             "score": score, "output": {"usage": {"total_tokens": tokens}}})
        rows.append({"candidate": "test-leak", "case": {"id": "a", "split": "test"}, "score": 1, "output": {}})
        self.assertEqual(select_validation_candidate(rows, {"a", "b"})["candidate"], "cheap")

    def test_infrastructure_failures_do_not_generate_reflections(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = FinancialGEPAAdapter(ROOT, Path(directory), ModelConfig("example", "https://example.com/", "unused"))
            candidate = flatten(json.loads((ROOT / "prompts/benchmark.json").read_text()))
            trace = {"case": {"split": "development"}, "execution_status": "infrastructure_failure"}
            batch = EvaluationBatch(outputs=[{}], scores=[0], trajectories=[trace])
            dataset = adapter.make_reflective_dataset(candidate, batch, ["system"])
            with patch("finevolab.optimization.sync_client") as client:
                proposed = adapter.propose_new_texts(candidate, dataset, ["system"])
            self.assertEqual(proposed, {"system": candidate["system"]})
            client.assert_not_called()

    def test_failed_reflection_is_counted_with_unknown_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = FinancialGEPAAdapter(ROOT, Path(directory), ModelConfig("example", "https://example.com/", "unused"))
            candidate = flatten(json.loads((ROOT / "prompts/benchmark.json").read_text()))
            with patch("finevolab.optimization.sync_client") as client:
                client.return_value.__enter__.return_value.chat.completions.create.side_effect = TimeoutError
                with self.assertRaises(TimeoutError):
                    adapter.propose_new_texts(candidate, {"system": [{"Feedback": "Incorrect period"}]}, ["system"])
            self.assertEqual(adapter.reflection_calls, 1)
            records = [json.loads(line) for line in (Path(directory) / "reflections.jsonl").read_text().splitlines()]
            self.assertEqual(records[-1]["status"], "failed")
            self.assertIsNone(records[-1]["usage"])


if __name__ == "__main__":
    unittest.main()
