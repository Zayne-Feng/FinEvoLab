"""Verify scoring metadata and deterministic-cache isolation across comparison arms."""

from pathlib import Path
import tempfile
import unittest

from finevolab.runtime import RunBudget
from scripts.run_followup_uploads import selected_reference, UncachedExecutor


class FollowupComparisonTests(unittest.TestCase):
    def test_selected_source_changes_only_reference_disclosure_year(self):
        case = {"upload_ids": ["source"], "report_year": 2024, "reference_answer": {
            "metrics": [{"report_year": 2025, "value": "123", "period_start": "2023-07-01"}], "calculations": []}}
        selected = selected_reference(case)
        self.assertEqual(selected["reference_answer"]["metrics"][0],
            {"report_year": 2024, "value": "123", "period_start": "2023-07-01"})
        self.assertEqual(case["reference_answer"]["metrics"][0]["report_year"], 2025)

    def test_archived_and_current_functions_cannot_share_cached_values(self):
        with tempfile.TemporaryDirectory() as directory:
            values = []
            for implementation in ("archived", "current"):
                events = []
                executor = UncachedExecutor(RunBudget(), events, {"source": "same"}, Path(directory))
                values.append(executor.call("lookup", {"year": 2024}, lambda: implementation))
                self.assertFalse(events[-1]["cache_hit"])
            self.assertEqual(values, ["archived", "current"])
