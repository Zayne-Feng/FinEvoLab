"""Check complete paired coverage and calibrated review aggregation."""

import json
import unittest
from unittest.mock import patch

from finevolab.runtime import read_json
from scripts.summarize_analysis_extensions import ROOT, summarize


class AnalysisAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.reference = ROOT / "project_notes/experiments/analysis-extensions-release"
        self.documents = {name: json.loads((self.reference / name).read_text(encoding="utf-8")) for name in
            ("manifest.json", "model_records.json", "blind_reviews.json", "blind_samples.json")}
        self.original_read = read_json

    def run_modified(self):
        def read(path):
            return self.documents[path.name] if path.name in self.documents else self.original_read(path)
        with patch("scripts.summarize_analysis_extensions.read_json", side_effect=read):
            return summarize(self.reference)

    def test_complete_workload(self):
        result = self.run_modified()
        self.assertEqual(result["analysis_usage"]["requests"], 32)
        self.assertEqual(result["review_usage"]["requests"], 14)
        self.assertEqual(sum(arm["tasks"] for arm in result["arms"].values()), 32)
        self.assertTrue(result["controls_passed"])
        for row in result["rows"]:
            self.assertEqual(row["strict_passed"], row["mechanical_passed"] and row["both_supported"] and row["both_complete"])

    def test_missing_analysis_record(self):
        self.documents["model_records.json"].pop()
        with self.assertRaises(ValueError):
            self.run_modified()

    def test_duplicate_review(self):
        reviews = self.documents["blind_reviews.json"]
        reviews.append(reviews[0])
        with self.assertRaisesRegex(ValueError, "reviewed twice"):
            self.run_modified()

    def test_missing_review_context(self):
        self.documents["blind_reviews.json"] = [r for r in self.documents["blind_reviews.json"] if r["reviewer"] == "A"]
        with self.assertRaisesRegex(ValueError, "both review contexts"):
            self.run_modified()

    def test_failed_calibration(self):
        self.documents["blind_reviews.json"][0]["controls_passed"] = False
        result = self.run_modified()
        self.assertFalse(result["controls_passed"])
        self.assertTrue(all(arm["strict_passed"] == 0 for arm in result["arms"].values()))

    def test_modified_review_payload(self):
        self.documents["blind_samples.json"]["samples"][0]["question"] = "Different scope"
        with self.assertRaisesRegex(ValueError, "Review samples differ"):
            self.run_modified()


if __name__ == "__main__":
    unittest.main()
