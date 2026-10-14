"""Verify calibration completeness, rater independence, and disagreement."""

import copy
import json
from pathlib import Path
import unittest

from finevolab.rubric import calibrate

ROOT = Path(__file__).resolve().parents[1]


class RubricTests(unittest.TestCase):
    def setUp(self):
        self.rubric = json.loads((ROOT / "configs/open_text_rubric.json").read_text())
        self.controls = {"samples": [{"id": "sample"}]}
        self.first = {"rater": "first", "rubric_version": self.rubric["version"],
                      "ratings": [{"sample_id": "sample", "scores": {key: 2 for key in self.rubric["dimensions"]}}]}
        self.second = copy.deepcopy(self.first)
        self.second["rater"] = "second"

    def test_distinct_complete_ratings_produce_agreement(self):
        result = calibrate(self.rubric, self.controls, self.first, self.second)
        self.assertEqual(result["exact_dimension_agreement"], 1)
        self.assertFalse(result["automatic_judge_calibrated"])

    def test_missing_same_rater_or_noninteger_scores_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            calibrate(self.rubric, self.controls, self.first, self.first)
        for change in ("missing", "invalid_score"):
            second = copy.deepcopy(self.second)
            if change == "missing":
                second["ratings"] = []
            else:
                second["ratings"][0]["scores"]["factual_support"] = 1.5
            with self.assertRaises(ValueError):
                calibrate(self.rubric, self.controls, self.first, second)

    def test_disagreement_requires_recalibration(self):
        self.second["ratings"][0]["scores"] = {key: 0 for key in self.rubric["dimensions"]}
        self.assertEqual(calibrate(self.rubric, self.controls, self.first, self.second)["status"], "recalibration_required")


if __name__ == "__main__":
    unittest.main()
