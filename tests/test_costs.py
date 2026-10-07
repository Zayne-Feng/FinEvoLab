"""Verify currency bounds and canonical-ledger consistency checks."""

from decimal import Decimal
import tempfile
from pathlib import Path
import unittest

from finevolab.costs import estimate_cost, normalize_usage
from scripts.settle_project_costs import Ledger


RATES = {
    "off_peak": {"cached_input": "0.003", "uncached_input": "0.15", "output": "0.6"},
    "peak": {"cached_input": "0.006", "uncached_input": "0.3", "output": "1.2"},
}


class CostTests(unittest.TestCase):
    def test_bounds_use_decimal_and_cover_cache_and_period_uncertainty(self):
        usage = {"requests": 1, "input_tokens": 1000000, "output_tokens": 1000000, "total_tokens": 2000000}
        bounds = estimate_cost(usage, RATES)
        self.assertEqual(Decimal(bounds["lower"]), Decimal("0.603"))
        self.assertEqual(Decimal(bounds["upper"]), Decimal("1.5"))
        fixed = estimate_cost(usage, RATES, "off_peak")
        self.assertEqual(Decimal(fixed["upper"]), Decimal("0.75"))

    def test_usage_rejects_missing_counts_mismatch_negative_and_boolean(self):
        good = {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
        self.assertEqual(normalize_usage(good)["input_tokens"], 2)
        for value in ({}, {**good, "total_tokens": 6}, {**good, "prompt_tokens": -2}, {**good, "total_tokens": True}):
            with self.assertRaises(ValueError):
                normalize_usage(value)

    def test_duplicate_canonical_rows_are_rejected_and_unknown_model_is_not_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "record.json"
            path.write_text("{}", encoding="utf-8")
            ledger = Ledger(root, {"models": {"deepseek-flash": RATES}})
            usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
            ledger.add("test", path, "one", usage, "unpriced")
            self.assertIsNone(ledger.entries[0]["estimated_cost"])
            with self.assertRaises(ValueError):
                ledger.add("test", path, "one", usage, "unpriced")


if __name__ == "__main__":
    unittest.main()
