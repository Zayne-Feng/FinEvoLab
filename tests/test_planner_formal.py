"""Check semantic defaults and preregistered checkpoint selection tie breaks."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from finevolab.planner_benchmark import PlannerCase, score_prediction
from finevolab.runtime import write_json
from scripts.analyze_planner_formal import prediction_runs, semantic_score
from scripts import run_planner_formal


class FormalTests(unittest.TestCase):
    def test_usage_inventory_requires_inference_runs_and_excludes_replays(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                prediction_runs(root)
            names = [f"planner-formal-{arm}-{split}" for arm in ("base", "deepseek") for split in ("validation", "test")]
            names += [f"planner-formal-lora{seed}-epoch{epoch}-validation" for seed in (17, 29) for epoch in (1, 2)]
            names += [f"planner-formal-lora{seed}-test" for seed in (17, 29)]
            for name in names:
                directory = root / name
                directory.mkdir()
                for filename in ("manifest.json", "summary.json", "predictions.jsonl"):
                    (directory / filename).touch()
            (root / "planner-formal-replay17").mkdir()
            write_json(root / "planner-formal-replay17/summary.json", {"status": "completed"})
            self.assertEqual({p.name for p in prediction_runs(root)}, set(names))

    def test_semantic_defaults_cannot_supply_missing_required_fields(self):
        case = PlannerCase.model_validate({"id": "risk", "group_id": "risk", "split": "test", "category": "explanation",
            "language": "en", "input": {"question": "Read MSFT FY2024 revenue drivers."},
            "expected": {"intent": "explanation", "companies": ["MSFT"], "fiscal_year": 2024, "metrics": ["revenue"]},
            "semantic_fields": ["intent", "companies", "fiscal_year", "topic", "scope"]})
        raw = {"intent": "explanation", "companies": ["MSFT"], "fiscal_year": 2024, "metrics": ["revenue"]}
        self.assertEqual(score_prediction(case, json.dumps(raw), {})["score"], 1)
        self.assertEqual(semantic_score(case, json.dumps(raw), {}), 1)
        del raw["fiscal_year"]
        self.assertEqual(semantic_score(case, json.dumps(raw), {}), 0)
        self.assertEqual(semantic_score(case, '{"intent":"metric","intent":"explanation"}', {}), 0)

    def test_selection_averages_seeds_before_semantic_tie_break(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for seed in (17, 29):
                for epoch in (1, 2):
                    write_json(root / f"planner-formal-lora{seed}-epoch{epoch}-validation/summary.json",
                        {"status": "completed", "attempted": 120,
                         "first": {"successes": 100, "semantic_successes": 100 + epoch}})
            selection = root / "selection.json"
            calls = []
            def local(*arguments):
                calls.append(arguments)
                if arguments[0] == "seal":
                    write_json(selection, {"sealed": True})
            with patch.object(run_planner_formal, "EXPERIMENTS", root), patch.object(run_planner_formal, "EVALUATIONS", root), \
                 patch.object(run_planner_formal, "SELECTION", selection), patch.object(run_planner_formal, "local", local):
                self.assertEqual(run_planner_formal.select(), 2)
            self.assertEqual([c[0] for c in calls], ["compare", "compare", "seal"])
            self.assertIn("--replicate-runs", calls[-1])
            details = json.loads((root / "planner_formal_selection_details.json").read_text())
            self.assertFalse(details["test_used"])


if __name__ == "__main__":
    unittest.main()
