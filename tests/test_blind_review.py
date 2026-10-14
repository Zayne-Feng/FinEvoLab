"""Verify rubric bounds and identity masking in review requests."""

import unittest
from pydantic import ValidationError

from scripts.run_blind_review import BlindRating, DIMENSIONS, blind_payload


class BlindReviewTests(unittest.TestCase):
    def test_candidate_and_score_metadata_are_not_sent_to_reviewers(self):
        sample = {"question": "Summarize annual income.", "observations": [], "submission": {"answer": "Available facts."},
                  "group": "selected", "candidate": "optimized_candidate", "score": 1.0, "origin_id": "private"}
        payload = blind_payload(sample)
        self.assertEqual(set(payload), {"question", "observations", "submission"})
        self.assertNotIn("selected", str(payload))
        self.assertNotIn("optimized_candidate", str(payload))

    def test_dimension_scores_require_exact_integer_bounds(self):
        values = {"scores": {name: 2 for name in DIMENSIONS}, "reasons": {name: "Supported by the source." for name in DIMENSIONS}}
        self.assertEqual(sum(BlindRating.model_validate(values).scores.values()), 8)
        for invalid in (True, 2.0, 3, -1):
            values["scores"]["factual_support"] = invalid
            with self.assertRaises(ValidationError):
                BlindRating.model_validate(values)

    def test_missing_dimensions_and_rationales_are_rejected(self):
        values = {"scores": {name: 1 for name in DIMENSIONS}, "reasons": {name: "Supported." for name in DIMENSIONS}}
        values["reasons"]["interpretation"] = " "
        with self.assertRaises(ValidationError):
            BlindRating.model_validate(values)
        values["reasons"]["interpretation"] = "Supported."
        del values["scores"]["period_and_units"]
        with self.assertRaises(ValidationError):
            BlindRating.model_validate(values)


if __name__ == "__main__":
    unittest.main()
