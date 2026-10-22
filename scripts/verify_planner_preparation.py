"""Audit frozen planner inputs, offline contracts, and portable source exports."""

import ast
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.planner_benchmark import coverage, experiment_manifest, load_bundle, normalize_question, planner_messages
from finevolab.runtime import write_json
from scripts.package_planner_cloud import package


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=ROOT / "data/training/planner_pilot/frozen_v2")
    parser.add_argument("--source", type=Path, default=ROOT / "data/training/planner_pilot/cases_v2.json")
    parser.add_argument("--archive", type=Path, default=ROOT / "output/cloud/planner_pilot_reviewed.zip")
    args = parser.parse_args()
    bundle = args.bundle
    splits = {split: load_bundle(bundle, split)[0] for split in ("development", "validation", "test")}
    contract = load_bundle(bundle, "development")[1]
    if any(coverage(cases)["missing_categories"] for cases in splits.values()):
        raise ValueError("A dataset split lacks required task categories")
    names = ("finevolab/planner_benchmark.py", "scripts/planner_benchmark.py", "scripts/train_planner.py",
        "scripts/replay_planner_predictions.py", "scripts/package_planner_cloud.py",
        "scripts/verify_planner_preparation.py", "scripts/preflight_planner.py", "tests/test_planner_benchmark.py")
    for name in names:
        ast.parse((ROOT / name).read_text(encoding="utf-8"))
    train = [json.loads(line) for line in (bundle / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = [{"prompt": planner_messages(contract, case.input.model_dump()),
        "completion": [{"role": "assistant", "content": case.expected.model_dump_json()}]} for case in splits["development"]]
    if train != expected:
        raise ValueError("The training export differs from the development-only examples")
    frozen = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if frozen["source_sha256"] != sha256(args.source):
        raise ValueError("The seed cases differ from the frozen version")
    documents = json.loads((bundle / "documents.json").read_text(encoding="utf-8"))
    for document in documents:
        if sha256(ROOT / document["path"]) != document["sha256"]:
            raise ValueError("A selected SEC snapshot changed")
    history = set()
    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get("question"), str):
                history.add(normalize_question(value["question"]))
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    for path in (ROOT / "data/evaluation").rglob("*.json"):
        collect(json.loads(path.read_text(encoding="utf-8")))
    overlaps = {split: [case.id for case in cases if normalize_question(case.input.question) in history]
                for split, cases in splits.items()}
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")
    test_path = ROOT / "project_notes/run_logs/planner_preparation_tests.txt"
    test_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise ValueError("The offline test suite failed")
    test_count = re.search(r"Ran (\d+) tests in ([\d.]+)s", result.stderr)
    if test_count is None or not result.stderr.rstrip().endswith("OK"):
        raise ValueError("The test result is incomplete")
    archive_path = args.archive
    archive = package(bundle, archive_path)
    repeated_archive = package(bundle, archive_path)
    if repeated_archive["sha256"] != archive["sha256"] or not repeated_archive.get("reused"):
        raise ValueError("The archive could not be verified on a repeated invocation")
    with ZipFile(archive_path) as exported, tempfile.TemporaryDirectory() as temporary:
        if any(Path(name).name in {"config.ini", "id_rsa", "id_ed25519"} or
               name.startswith((".git/", "data/raw/", "project_notes/experiments/")) for name in exported.namelist()):
            raise ValueError("The export contains a prohibited file")
        exported.extractall(temporary)
        portable = experiment_manifest(Path(temporary), "portable-check", {})
        (Path(temporary) / "configs/planner_training.json").write_text("{}", encoding="utf-8")
        try:
            experiment_manifest(Path(temporary), "tamper-check", {})
        except ValueError:
            tamper_rejected = True
        else:
            raise ValueError("A changed export was not rejected")
    report = {**manifest(ROOT, "planner-preparation", {
        "bundle_sha256": sha256(bundle / "manifest.json"), "archive_sha256": archive["sha256"]}),
        "status": "passed", "model_requests": 0, "fmp_requests": 0, "training_executed": False,
        "real_model_comparison_executed": False, "production_model_changed": False,
        "tests": {"returncode": result.returncode, "count": int(test_count[1]), "seconds": float(test_count[2]),
            "path": test_path.relative_to(ROOT).as_posix(), "sha256": sha256(test_path)},
        "splits": {split: coverage(cases) for split, cases in splits.items()},
        "training_export": {"rows": len(train), "development_only": True, "matches_runtime_messages": True},
        "historical_exact_question_overlaps": overlaps, "near_duplicate_review": "required_before_formal_expansion",
        "selected_source_hashes_checked": len(documents), "python_files_parsed": len(names),
        "archive": archive, "portable_source_commit": portable["commit"], "export_tamper_rejected": tamper_rejected,
        "archive_repeat_verified": True, "active_dataset": bundle.relative_to(ROOT).as_posix(),
        "pending": ["cloud_dependency_resolution_and_lock", "gpu_inference_and_training_preflight",
            "curated_training_expansion", "real_three_arm_validation_and_test", "narrative_semantic_review"]}
    write_json(ROOT / "project_notes/run_logs/planner_preparation.json", report)
    print(json.dumps({"status": "passed", "tests": report["tests"]["count"], "archive": archive,
                      "historical_exact_overlaps": {k: len(v) for k, v in overlaps.items()}}))


if __name__ == "__main__":
    main()
