"""Verify typed output contracts, metadata preservation, and cache validation."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from finevolab.runtime import RunBudget, ToolExecutor
from finevolab.tool_outputs import ReportArtifacts, SearchReportsOutput, ToolError


ROOT = Path(__file__).resolve().parents[1]


class ToolOutputTests(unittest.TestCase):
    def setUp(self):
        self.passage = json.loads((ROOT / "data/indexes/contextual/structured.json").read_text(encoding="utf-8"))[0]

    def test_passages_preserve_context_and_do_not_insert_optional_fields(self):
        original = [self.passage]
        result = SearchReportsOutput.model_validate(original).model_dump(mode="json", exclude_unset=True)
        self.assertEqual(result, original)
        self.assertEqual(SearchReportsOutput.model_validate([]).model_dump(), [])

    def test_missing_provenance_and_nonfinite_scores_are_rejected(self):
        for field in ("source_url", "locator", "source_sha256", "text"):
            with self.subTest(field=field):
                value = copy.deepcopy(self.passage)
                value.pop(field)
                with self.assertRaises(ValidationError):
                    SearchReportsOutput.model_validate([value])
        for score in (float("nan"), float("inf")):
            with self.assertRaises(ValidationError):
                SearchReportsOutput.model_validate([{**self.passage, "score": score}])

    def test_report_formats_are_required_and_unknown_fields_are_rejected(self):
        result = ReportArtifacts(html="report.html", pdf="report.pdf").model_dump(exclude_none=True)
        self.assertEqual(result, {"html": "report.html", "pdf": "report.pdf"})
        for payload in ({"html": "report.html"}, {"html": "report.pdf", "pdf": "report.pdf"},
                        {"html": "report.html", "pdf": "report.pdf", "unexpected": True}):
            with self.assertRaises(ValidationError):
                ReportArtifacts.model_validate(payload)

    def test_cached_search_outputs_keep_the_same_model_and_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            executor = ToolExecutor(RunBudget(), events, {"source": "fixed"}, Path(directory))
            first = executor.call("search_reports", {"query": "revenue"},
                lambda: SearchReportsOutput.model_validate([self.passage]), SearchReportsOutput)
            second = executor.call("search_reports", {"query": "revenue"},
                lambda: self.fail("A cached search must not execute again"), SearchReportsOutput)
            self.assertEqual(first.model_dump(exclude_unset=True), second.model_dump(exclude_unset=True))
            self.assertTrue(events[-1]["cache_hit"])

    def test_errors_have_a_nonempty_explicit_contract(self):
        self.assertEqual(ToolError(error="Missing annual value").model_dump(), {"error": "Missing annual value"})
        with self.assertRaises(ValidationError):
            ToolError(error="")


if __name__ == "__main__":
    unittest.main()
