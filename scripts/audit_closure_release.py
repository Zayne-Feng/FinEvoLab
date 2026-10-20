"""Audit frozen data, source fingerprints, legal files and completed run records."""

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.evaluation import source_audit
from finevolab.experiments import sha256
from finevolab.runtime import write_json
from finevolab.tools import FinancialStore


def main():
    report = {"status": "passed", "frozen_splits": {}, "runs": {}, "legal_files": {}, "json_files": 0}
    for folder in ("v2", "closure"):
        directory = ROOT / "data/evaluation" / folder
        manifest = json.loads((directory / "manifest.json").read_text())
        for split, digest in manifest["split_sha256"].items():
            assert sha256(directory / f"{split}.json") == digest
        report["frozen_splits"][folder] = sha256(directory / "manifest.json")
    for path in (ROOT / "project_notes").rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8-sig"))
        report["json_files"] += 1
    for name in ("LICENSE", "NOTICE", "TRADEMARK_POLICY.md"):
        prior = subprocess.check_output(["git", "show", "c5f4cb8b:" + name], cwd=ROOT)
        assert (ROOT / name).read_bytes() == prior
        report["legal_files"][name] = sha256(ROOT / name)
    config = "finrobot_equity/core/config/config.ini"
    assert not subprocess.check_output(["git", "ls-files", "--", config], cwd=ROOT).strip()
    assert subprocess.run(["git", "check-ignore", "-q", config], cwd=ROOT).returncode == 0
    report["credential_file"] = "ignored_and_untracked"
    report["canonical_sources"] = source_audit(ROOT, FinancialStore(ROOT / "data/processed/reports.json"))
    tests = json.loads((ROOT / "project_notes/run_logs/closure_tests.json").read_text())
    assert tests["status"] == "passed" and tests["tests"] == 69
    assert all(sha256(ROOT / "finevolab" / name) == digest for name, digest in tests["core_hashes"].items())
    blind = json.loads((ROOT / "project_notes/evaluations/closure-blind-review/summary.json").read_text())
    assert blind["status"] == "complete" and blind["control_errors"] == [0, 0]
    report["blind_review"] = {"calls": blind["calls"] + blind["adjudication_calls"], "controls_passed": True}
    delivery = json.loads((ROOT / "project_notes/run_logs/closure_delivery.json").read_text())
    pdf = json.loads((ROOT / "project_notes/run_logs/closure_pdf_review.json").read_text())
    assert delivery["status"] == "passed" and pdf["visual_review"] == "passed"
    assert sha256(ROOT / pdf["pdf"]) == pdf["sha256"]
    report["delivery"] = "four_scenarios_and_pdf_passed"
    for name in ("closure-acceptance", "closure-gepa-initial", "closure-gepa-selected"):
        directory = ROOT / "project_notes/experiments" / name
        summary = json.loads((directory / "summary.json").read_text())
        manifest = json.loads((directory / "manifest.json").read_text())
        assert summary["status"] == "complete" and summary["attempted"] == summary["planned"]
        assert all(sha256(ROOT / path) == digest for path, digest in manifest["config"]["core"].items())
        report["runs"][name] = {"tasks": summary["attempted"], "core_matches_release": True,
                                "manifest_sha256": sha256(directory / "manifest.json")}
    selected = ROOT / "project_notes/optimization/closure-gepa-compact/selected_candidate.json"
    selected_run = ROOT / "project_notes/experiments/closure-gepa-selected/manifest.json"
    assert sha256(selected) == json.loads(selected_run.read_text())["config"]["candidate_sha256"]
    deployed_candidate = ROOT / "prompts/candidates/82bebb0cb6debc602ee4.json"
    assert json.loads(deployed_candidate.read_text()) == json.loads(selected.read_text())
    report["compact_candidate"] = {"tested_snapshot_sha256": sha256(selected),
                                   "functional_file_sha256": sha256(deployed_candidate),
                                   "parsed_configuration_equal": True}
    initial_run = ROOT / "project_notes/experiments/closure-acceptance/manifest.json"
    assert sha256(ROOT / "prompts/benchmark.json") == json.loads(initial_run.read_text())["config"]["candidate_sha256"]
    report["tested_prompts"] = "default_and_compact_match_frozen_bytes"
    report["core_hashes"] = {path.relative_to(ROOT).as_posix(): sha256(path)
                             for path in (ROOT / "finevolab").rglob("*.py")}
    report["commit_before_release"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    write_json(ROOT / "project_notes/run_logs/closure_release_audit.json", report)
    print(json.dumps({"status": report["status"], "json_files": report["json_files"], "runs": report["runs"]}))


if __name__ == "__main__":
    main()
