"""Check planner semantics, checkpoint selection, and immutable artifact identities."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from finevolab.planner_benchmark import PlannerCase, score_prediction
from finevolab.provenance import digest, verify_manifest, verify_task_identity
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


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.blobs = {}
        self.git = patch("finevolab.provenance.subprocess.check_output", side_effect=self.read_blob)
        self.git.start()
        self.addCleanup(self.git.stop)

    def read_blob(self, command, **kwargs):
        return self.blobs[command[-1]]

    def reference(self, path, raw):
        commit = "a" * 40
        self.blobs[f"{commit}:{path}"] = raw
        return {"commit": commit, "path": path, "sha256": digest(raw)}

    def write(self, path, document):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document), encoding="utf-8")
        return target

    def fixture(self):
        rows = [{"score": 1, "evidence": [{"source_path": "C:/archive/data/raw/report.html",
                                            "source_sha256": "b" * 64, "text": "Revenue increased."}]}]
        raw = (json.dumps(rows[0]) + "\n").encode()
        source = self.reference("tasks.jsonl", raw)
        rows[0]["evidence"][0]["source_path"] = "data/raw/report.html"
        tasks = self.root / "tasks.jsonl"
        tasks.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
        config = {"tasks_sha256": source["sha256"]}
        original = {"config": config, "config_sha256": digest(json.dumps(config, sort_keys=True).encode()),
                    "source_hashes": {"module.py": "c" * 64}}
        current = copy.deepcopy(original)
        current["publication"] = {"source": self.reference("manifest.json", json.dumps(original).encode()),
            "artifacts": [{"config_key": "tasks_sha256", "source": source,
                           "published_sha256": digest(tasks.read_bytes()), "transform": "source_paths_v1"}]}
        return self.write("manifest.json", current), tasks, current

    def test_source_path_normalization_preserves_original_task_identity(self):
        manifest, tasks, current = self.fixture()
        verify_task_identity(self.root, manifest, tasks)
        self.assertNotEqual(digest(tasks.read_bytes()), current["config"]["tasks_sha256"])

    def test_changed_scores_and_citations_are_rejected_even_with_updated_publication_hash(self):
        for field, value in (("score", 0), ("score", True), ("text", "Different claim."),
                             ("source_sha256", "d" * 64)):
            with self.subTest(field=field, value=value):
                manifest, tasks, current = self.fixture()
                row = json.loads(tasks.read_bytes())
                if field == "score":
                    row[field] = value
                else:
                    row["evidence"][0][field] = value
                tasks.write_text(json.dumps(row) + "\n", encoding="utf-8")
                current["publication"]["artifacts"][0]["published_sha256"] = digest(tasks.read_bytes())
                self.write("manifest.json", current)
                with self.assertRaisesRegex(ValueError, "outside the allowed paths"):
                    verify_manifest(self.root, manifest)

    def test_changed_original_git_bytes_are_rejected(self):
        manifest, _, current = self.fixture()
        reference = current["publication"]["source"]
        self.blobs[f"{reference['commit']}:{reference['path']}"] += b" "
        with self.assertRaisesRegex(ValueError, "original Git artifact hash"):
            verify_manifest(self.root, manifest)

    def test_missing_input_publication_is_rejected(self):
        manifest, _, current = self.fixture()
        current["publication"]["artifacts"] = []
        self.write("manifest.json", current)
        with self.assertRaisesRegex(ValueError, "lacks an original input identity"):
            verify_manifest(self.root, manifest)

    def test_replay_publication_keeps_original_configuration_hash(self):
        for seed in (17, 29):
            with self.subTest(seed=seed):
                config = {"runs": {f"C:/archive/project_notes/experiments/replay{seed}": "b" * 64}}
                original = {"config": config, "config_sha256": digest(json.dumps(config, sort_keys=True).encode())}
                name = f"replay{seed}/manifest.json"
                current = {"config_sha256": original["config_sha256"],
                           "published_config": {"runs": {f"project_notes/experiments/replay{seed}": "b" * 64}},
                           "publication": {"source": self.reference(name, json.dumps(original).encode()), "artifacts": []}}
                path = self.write(name, current)
                self.assertEqual(verify_manifest(self.root, path), config)
                self.assertNotIn("C:/archive", path.read_text())
                current["published_config"]["runs"][f"project_notes/experiments/replay{seed}"] = "d" * 64
                self.write(name, current)
                with self.assertRaisesRegex(ValueError, "published replay configuration"):
                    verify_manifest(self.root, path)


if __name__ == "__main__":
    unittest.main()
