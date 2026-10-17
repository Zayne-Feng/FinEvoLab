"""Verify label-free validation and durable question recovery."""

import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from finevolab.config import ModelConfig
from finevolab.evaluation import reference_answer
from finevolab.tools import FinancialStore
from finevolab.validation import validate_answer
from finevolab.workflow import run_question

ROOT = Path(__file__).resolve().parents[1]


class QuestionWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = json.loads((ROOT / "data/evaluation/development.json").read_text())["tasks"][0]
        self.output = {"answer": reference_answer(self.case), "events": [], "usage": {"requests": 1, "total_tokens": 1}}
        self.candidate = json.loads((ROOT / "prompts/benchmark.json").read_text())
        self.settings = ModelConfig("example", "https://example.com/", "unused")

    async def test_recovery_skips_prepared_and_completed_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/processed").mkdir(parents=True)
            shutil.copyfile(ROOT / "data/processed/reports.json", root / "data/processed/reports.json")
            with patch("finevolab.workflow.save_task"), patch("finevolab.evaluation.analyze_task", AsyncMock(side_effect=ConnectionError)):
                with self.assertRaises(ConnectionError):
                    await run_question("question1", self.case["question"], self.settings, self.candidate, root, render=False)
            with patch("finevolab.workflow.save_task"), patch("finevolab.evaluation.analyze_task", AsyncMock(return_value=self.output)) as execute:
                state = await run_question("question1", self.case["question"], self.settings, self.candidate, root, render=False)
                resumed = await run_question("question1", self.case["question"], self.settings, self.candidate, root, render=False)
                self.assertEqual(state["status"], "complete")
                self.assertEqual(resumed["phases"], state["phases"])
                execute.assert_awaited_once()
                self.assertNotIn("expected", execute.call_args.kwargs)

    async def test_tampered_values_fail_without_evaluation_labels(self):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        self.assertEqual(validate_answer(store, self.output)["status"], "passed")
        wrong = copy.deepcopy(self.output)
        wrong["answer"]["metrics"][0]["value"] = "1"
        with self.assertRaisesRegex(ValueError, "source snapshot"):
            validate_answer(store, wrong)

    async def test_false_alignment_and_unsupported_citations_fail(self):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        wrong = copy.deepcopy(self.output)
        wrong["answer"]["periods_aligned"] = True
        with self.assertRaisesRegex(ValueError, "two distinct"):
            validate_answer(store, wrong)
        wrong = copy.deepcopy(self.output)
        wrong["answer"]["evidence_ids"] = ["fabricated"]
        with self.assertRaisesRegex(ValueError, "Evidence"):
            validate_answer(store, wrong)


if __name__ == "__main__":
    unittest.main()
