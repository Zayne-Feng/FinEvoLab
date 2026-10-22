"""Check split isolation, raw-output scoring, and paired experiment contracts."""

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from finevolab.config import ModelConfig
from finevolab.planner_benchmark import (PlannerCase, canonical_plan, freeze_bundle, load_bundle,
    coverage, paired_report, parsed_plan, planner_messages, score_prediction, summarize_predictions,
    validate_cases, validate_prediction_rows)
from finevolab.runtime import fingerprint, write_json
from scripts.planner_benchmark import (arm_identity, check_selection, checked_run, compare, implementation_hashes,
                                       MODEL_SPEC, predict, seal)
from scripts.replay_planner_predictions import replay_score
from scripts.replay_planner_predictions import run as replay
from scripts.package_planner_cloud import package
from scripts.train_planner import TrainingConfig

ROOT = Path(__file__).resolve().parents[1]


def cases():
    return [dict(id=split, group_id=split, split=split, category="metric", language="en",
        input={"question": f"Give MSFT FY2024 revenue. Context: {split}.", "selected_documents": []},
        expected={"intent": "metric", "companies": ["MSFT"], "fiscal_year": 2024, "metrics": ["revenue"]})
        for split in ("development", "validation", "test")]


def prediction(case, raw=None, repeat=0):
    return {"case_id": case.id, "repeat": repeat, "input_sha256": fingerprint(case.input.model_dump()),
        "attempts": [{"raw": raw if raw is not None else case.expected.model_dump_json(),
                      "elapsed_seconds": 0.1, "usage": None}]}


class PlannerDataTests(unittest.TestCase):
    def setUp(self):
        self.case = PlannerCase.model_validate(cases()[0])

    def test_group_and_normalized_question_leakage_are_rejected(self):
        rows = cases()
        rows[1]["group_id"] = rows[0]["group_id"]
        with self.assertRaisesRegex(ValueError, "crosses"):
            validate_cases(rows)
        rows = cases()
        rows[1]["input"]["question"] = rows[0]["input"]["question"].upper().replace(".", "!")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_cases(rows)

    def test_case_ids_and_input_fields_are_strict(self):
        rows = cases()
        rows[1]["id"] = rows[0]["id"]
        with self.assertRaises(ValueError):
            validate_cases(rows)
        rows = cases()
        rows[0]["input"]["expected"] = rows[0]["expected"]
        with self.assertRaises(ValueError):
            validate_cases(rows)

    def test_selected_documents_require_matching_catalogue_metadata(self):
        rows = cases()
        rows[0]["input"]["selected_documents"] = [{"id": "document", "company": "MSFT", "report_year": 2024}]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.json"
            write_json(path, {"cases": rows})
            with self.assertRaisesRegex(ValueError, "catalogue"):
                freeze_bundle(path, Path(temporary) / "bundle", {"system": ""}, {})

    def test_frozen_files_and_training_split_are_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, directory = Path(temporary) / "source.json", Path(temporary) / "bundle"
            write_json(source, {"cases": cases()})
            freeze_bundle(source, directory, {"system": ""}, {})
            selected, contract = load_bundle(directory, "validation")
            self.assertEqual(len(selected), 1)
            train = [json.loads(line) for line in (directory / "train.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(train), 1)
            self.assertIn("development", train[0]["prompt"][1]["content"])
            self.assertNotIn("expected", planner_messages(contract, selected[0].input.model_dump())[1]["content"])
            with self.assertRaises(ValueError):
                freeze_bundle(source, directory, {"system": ""}, {})
            (directory / "test.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed"):
                load_bundle(directory, "validation")

    def test_defaults_and_aliases_do_not_create_spurious_failures(self):
        raw = self.case.expected.model_copy(update={"companies": ["Microsoft"], "report_year": 2024,
            "topic": "profit", "numeric_intent": "summary"}).model_dump_json()
        self.assertEqual(score_prediction(self.case, raw, {"microsoft": "MSFT"})["score"], 1)
        plan = self.case.expected.model_copy(update={"companies": ["MSFT", "Microsoft"]})
        self.assertEqual(canonical_plan(plan, {"microsoft": "MSFT"})["companies"], ["MSFT", "MSFT"])

    def test_format_schema_and_semantics_are_scored_separately(self):
        malformed = score_prediction(self.case, "```json\n{}\n```", {})
        self.assertFalse(malformed["json_valid"])
        valid = score_prediction(self.case, "{}", {})
        self.assertTrue(valid["json_valid"])
        self.assertFalse(valid["schema_valid"])
        wrong = self.case.expected.model_copy(update={"fiscal_year": 2023}).model_dump_json()
        result = score_prediction(self.case, wrong, {})
        self.assertTrue(result["schema_valid"])
        self.assertFalse(result["fields"]["fiscal_year"])
        self.assertEqual(result["score"], 0)

    def test_semantics_do_not_require_irrelevant_schema_placeholders(self):
        row = cases()[0]
        row.update(semantic_fields=["intent", "companies", "fiscal_year", "topic", "scope"])
        row["expected"].update(intent="explanation", topic="risk")
        case = PlannerCase.model_validate(row)
        raw = case.expected.model_dump()
        raw["metrics"] = []
        result = score_prediction(case, json.dumps(raw), {})
        self.assertEqual(result["semantic_score"], 1)
        self.assertEqual(result["score"], 0)
        raw["topic"] = "profit"
        self.assertEqual(score_prediction(case, json.dumps(raw), {})["semantic_score"], 0)
        self.assertEqual(score_prediction(case, '{"intent":"metric","intent":"growth"}', {})["semantic_score"], 0)

    def test_formal_template_groups_are_split_isolated(self):
        rows = json.loads((ROOT / "data/training/planner_formal/cases_v1.json").read_text(encoding="utf-8"))["cases"]
        values = validate_cases(rows)
        self.assertEqual([sum(c.split == s for c in values) for s in ("development", "validation", "test")], [600, 120, 240])
        self.assertEqual(len({c.group_id for c in values if c.split == "test"}), 60)
        self.assertTrue(all(c.semantic_fields for c in values))

    def test_duplicate_json_keys_and_coerced_types_are_rejected(self):
        raw = self.case.expected.model_dump_json()
        with self.assertRaises(ValueError):
            parsed_plan(raw.replace('"fiscal_year":2024', '"fiscal_year":"2024"'))
        with self.assertRaises(ValueError):
            parsed_plan(raw[:-1] + ',"intent":"growth"}')

    def test_first_and_repaired_results_have_distinct_scores_and_costs(self):
        row = prediction(self.case, raw="{}")
        row["attempts"].append({"raw": self.case.expected.model_dump_json(), "elapsed_seconds": 0.2,
            "usage": {"requests": 1, "input_tokens": 8, "output_tokens": 4, "total_tokens": 12}})
        result = summarize_predictions([self.case], [row], {})
        self.assertEqual(result["first"]["successes"], 0)
        self.assertEqual(result["final"]["successes"], 1)
        self.assertEqual(result["known_tokens"], 12)
        self.assertEqual(result["unknown_usage_attempts"], 1)
        self.assertAlmostEqual(result["mean_total_seconds"], 0.3)

    def test_pairing_rejects_missing_duplicate_and_extra_rows(self):
        row = prediction(self.case)
        for rows in ([], [row, row], [dict(row, repeat=1)], [dict(row, repeat=True)]):
            with self.assertRaises(ValueError):
                validate_prediction_rows(rows, [self.case], 1)
        with self.assertRaises(ValueError):
            validate_prediction_rows([dict(row, attempts=[])], [self.case], 1)

    def test_repair_budget_and_usage_cannot_be_changed_in_imported_rows(self):
        row = prediction(self.case)
        row["attempts"].append(dict(row["attempts"][0]))
        with self.assertRaisesRegex(ValueError, "schema-valid"):
            validate_prediction_rows([row], [self.case], 1)
        row["attempts"][0]["raw"] = "{}"
        with self.assertRaises(ValueError):
            validate_prediction_rows([row], [self.case], 1, max_repairs=0)
        row = prediction(self.case)
        row["attempts"][0]["usage"] = {"requests": 1, "input_tokens": -1, "output_tokens": 3, "total_tokens": 2}
        with self.assertRaisesRegex(ValueError, "token usage"):
            validate_prediction_rows([row], [self.case], 1)

    def test_reviewed_pilot_covers_all_task_categories_in_every_split(self):
        for split in ("development", "validation", "test"):
            selected, _ = load_bundle(ROOT / "data/training/planner_pilot/frozen_v2", split)
            result = coverage(selected)
            self.assertEqual(result["cases"], 40)
            self.assertEqual(result["missing_categories"], [])

    def test_training_config_rejects_invalid_values_before_ml_imports(self):
        value = json.loads((ROOT / "configs/planner_training.json").read_text(encoding="utf-8"))
        TrainingConfig.model_validate(value)
        for field, invalid in (("gradient_accumulation_steps", 0), ("lora_rank", -1),
                               ("learning_rate", float("nan")), ("max_length", "4096")):
            wrong = {**value, field: invalid}
            with self.assertRaises(ValueError):
                TrainingConfig.model_validate(wrong)

    def test_cloud_archive_can_be_reused_and_rejects_extra_files(self):
        from zipfile import ZipFile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "planner.zip"
            bundle = ROOT / "data/training/planner_pilot/frozen_v2"
            original = package(bundle, path)
            repeated = package(bundle, path)
            self.assertEqual(original["sha256"], repeated["sha256"])
            self.assertTrue(repeated["reused"])
            with ZipFile(path, "a") as archive:
                archive.writestr("unexpected.txt", "extra")
            with self.assertRaises(ValueError):
                package(bundle, path)

    def test_repeated_bilingual_variants_stay_in_one_bootstrap_cluster(self):
        second = self.case.model_copy(update={"id": "translation", "language": "zh"})
        subset = [self.case, second]
        left = [prediction(case, raw="{}", repeat=r) for case in subset for r in range(3)]
        right = [prediction(case, repeat=r) for case in subset for r in range(3)]
        report = paired_report(subset, left, right, {}, 3)
        self.assertEqual(report["wins"], 6)
        self.assertEqual(report["group_weighted_uncertainty"]["case_clusters"], 1)
        self.assertFalse(report["group_weighted_uncertainty"]["repeats_treated_as_independent_cases"])

    def test_numeric_replay_detects_wrong_scope_and_preserves_source_checks(self):
        from finevolab.grounded import assemble_numeric
        from finevolab.tools import FinancialStore
        store = FinancialStore(ROOT / "data/processed/reports.json")
        answer = assemble_numeric(self.case.expected, store, [])
        output = {"answer_type": "numeric", "answer": answer, "request": self.case.expected.model_dump()}
        result = replay_score(self.case, self.case.expected.model_dump_json(), output, store, {})
        self.assertEqual(result["score"], 1)
        wrong = self.case.expected.model_copy(update={"fiscal_year": 2023})
        output = {"answer_type": "numeric", "answer": assemble_numeric(wrong, store, []), "request": wrong.model_dump()}
        result = replay_score(self.case, wrong.model_dump_json(), output, store, {})
        self.assertEqual(result["score"], 0)
        self.assertTrue(result["checks"]["source_validation"])
        self.assertFalse(result["checks"]["financial_numeric"])


class PlannerRunTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        source = self.directory / "source.json"
        write_json(source, {"cases": cases()})
        self.bundle = self.directory / "bundle"
        freeze_bundle(source, self.bundle, json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8")), {})
        self.cases, self.contract = load_bundle(self.bundle, "validation")

    async def test_valid_but_incorrect_semantics_never_trigger_label_based_repair(self):
        case = self.cases[0]
        wrong = case.expected.model_copy(update={"intent": "growth"}).model_dump_json()
        calls = []
        opened = []
        class FakeClient:
            def __init__(self):
                self.chat = self
                self.completions = self
            async def __aenter__(self):
                opened.append(True)
                return self
            async def __aexit__(self, *args):
                pass
            def with_options(self, **kwargs):
                return self
            async def create(self, **kwargs):
                from types import SimpleNamespace
                calls.append(kwargs)
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=wrong))], usage=None)
        args = argparse.Namespace(bundle=self.bundle, split="validation", arm="deepseek", backend="api",
            config=None, repeats=1, repairs=1, max_new_tokens=512, max_input_tokens=8192,
            output=self.directory / "run", selection=None)
        with patch("scripts.planner_benchmark.ModelConfig.load", return_value=ModelConfig(
                "deepseek-flash", "https://api.deepseek.com/", "private-test-key")), patch(
                "finevolab.provider.async_client", return_value=FakeClient()):
            await predict(args)
            await predict(args)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(opened), 1)
        self.assertNotIn("expected", calls[0]["messages"][1]["content"])
        self.assertNotIn("response_format", calls[0])
        self.assertNotIn("private-test-key", (args.output / "manifest.json").read_text())
        self.assertEqual(json.loads((args.output / "summary.json").read_text())["first"]["successes"], 0)

    def make_runs(self):
        from finevolab.experiments import sha256
        paths = []
        for arm in ("deepseek", "qwen_base", "qwen_lora"):
            directory = self.directory / arm
            model = {"model": "qwen", "revision": "a" * 40, "adapter": {"sha256": "adapter"} if arm == "qwen_lora" else None}
            config = {"bundle_sha256": sha256(self.bundle / "manifest.json"), "split": "validation", "arm": arm,
                "repeats": 1, "generation": {"repairs": 0}, "implementation": implementation_hashes(), "model": model}
            write_json(directory / "manifest.json", {"config": config})
            (directory / "predictions.jsonl").write_text(json.dumps(prediction(self.cases[0])) + "\n", encoding="utf-8")
            paths.append(directory)
        return paths

    def test_cuda_preflight_stops_before_loading_model_weights(self):
        args = argparse.Namespace(arm="qwen_base", backend="transformers", revision=MODEL_SPEC["revision"],
            model="Qwen/Qwen3-4B-Instruct-2507", adapter=None, device="cuda")
        with patch("torch.cuda.is_available", return_value=False):
            with self.assertRaisesRegex(ValueError, "CUDA is unavailable"):
                arm_identity(args)

    async def test_runtime_replay_keeps_a_source_valid_wrong_plan_as_a_failure(self):
        runs = self.make_runs()
        wrong = self.cases[0].expected.model_copy(update={"fiscal_year": 2023}).model_dump_json()
        (runs[1] / "predictions.jsonl").write_text(json.dumps(prediction(self.cases[0], wrong)) + "\n", encoding="utf-8")
        output = self.directory / "replay"
        await replay(argparse.Namespace(bundle=self.bundle, split="validation", runs=runs, phase="first",
            with_narrative=False, config=None, output=output))
        result = json.loads((output / "summary.json").read_text(encoding="utf-8"))["groups"]
        self.assertEqual(result["deepseek"]["mechanical_successes"], 1)
        self.assertEqual(result["qwen_base"]["mechanical_successes"], 0)
        self.assertEqual(result["qwen_lora"]["mechanical_successes"], 1)
        rows = [json.loads(line) for line in (output / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(row["output"]["usage"]["requests"] == 0 for row in rows))

    def test_comparison_rejects_precision_mismatch_and_input_changes(self):
        runs = self.make_runs()
        args = argparse.Namespace(bundle=self.bundle, split="validation", runs=runs, output=self.directory / "comparison.json")
        compare(args)
        path = runs[2] / "manifest.json"
        value = json.loads(path.read_text())
        value["config"]["model"]["dtype"] = "float16"
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, "adapter weights"):
            compare(args)
        row = prediction(self.cases[0])
        row["input_sha256"] = "wrong"
        (runs[0] / "predictions.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different model input"):
            checked_run(runs[0], self.bundle, "validation")

    def test_test_split_requires_validation_seal_and_matching_adapter(self):
        runs = self.make_runs()
        path = self.directory / "selection.json"
        seal(argparse.Namespace(bundle=self.bundle, runs=runs, output=path))
        config = json.loads((runs[2] / "manifest.json").read_text())["config"]
        check_selection(path, config)
        with self.assertRaises(ValueError):
            check_selection(None, config)
        config["model"]["adapter"]["sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "differs"):
            check_selection(path, config)
        with self.assertRaises(ValueError):
            seal(argparse.Namespace(bundle=self.bundle, runs=runs, output=path))


if __name__ == "__main__":
    unittest.main()
