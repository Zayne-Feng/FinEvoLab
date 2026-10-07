"""Verify task-level clustering and complete-pair requirements."""

import unittest
from scripts.estimate_paired_uncertainty import estimate


class PairedUncertaintyTests(unittest.TestCase):
    def test_repeated_answers_stay_in_their_task_clusters(self):
        pairs = [{"case_id": case, "repeat": repeat, "difference": difference}
                 for case, difference in (("first", 1), ("second", -1)) for repeat in (1, 2, 3)]
        report = {"complete_pairing": True, "observed_pairs": 6, "expected_pairs": 6,
                  "left_run": "initial", "right_run": "selected", "pairs": pairs}
        result = estimate(report)
        self.assertEqual(result["case_clusters"], 2)
        self.assertEqual(result["repeats_per_case"], 3)
        self.assertEqual(result["mean_difference"], 0)
        self.assertEqual(result["interval"], [-1, 1])
        self.assertFalse(result["repeats_treated_as_independent_cases"])
        report["complete_pairing"] = False
        with self.assertRaises(ValueError):
            estimate(report)


if __name__ == "__main__":
    unittest.main()
