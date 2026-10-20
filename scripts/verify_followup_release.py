"""Audit frozen follow-up inputs, paired results, source versions, and legal files."""

import json
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json
from finevolab.upstream_baseline import UPSTREAM


def main():
    datasets = {}
    for version in ("followup_v2", "followup_v3"):
        base = ROOT / "data/evaluation" / version
        frozen = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
        for name, digest in frozen["files"].items():
            if sha256(base / name) != digest:
                raise ValueError("A frozen task file changed")
        for name, digest in frozen["sources"].items():
            if sha256(ROOT / name) != digest:
                raise ValueError("A frozen source changed")
        datasets[version] = sha256(base / "manifest.json")
    runs = {}
    for name, count in (("upstream-common-v1", 60), ("upload-test-v2", 72),
                        ("upload-table-test-v3", 18)):
        directory = ROOT / "project_notes/experiments" / name
        assessment = json.loads((directory / "assessment.json").read_text(encoding="utf-8"))
        execution = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        dataset = ROOT / execution["config"].get("dataset_path", "data/evaluation/followup_v1/common.json")
        if sha256(dataset) != execution["config"]["dataset_sha256"]:
            raise ValueError("The evaluated task split changed")
        if assessment["status"] != "completed" or sha256(directory / "tasks.jsonl") != assessment["tasks_sha256"]:
            raise ValueError("An assessed task ledger changed")
        if sum(group["attempts"] for group in assessment["groups"].values()) != count:
            raise ValueError("The planned pairing is incomplete")
        attestation = directory / "source_commit_attestation.json"
        implementation_commit = (json.loads(attestation.read_text(encoding="utf-8"))["resolved_implementation_commit"]
            if attestation.exists() else execution["commit"])
        for path, digest in execution["config"]["source_hashes"].items():
            source = subprocess.check_output(["git", "show", f"{implementation_commit}:{path}"], cwd=ROOT)
            if hashlib.sha256(source).hexdigest() != digest:
                raise ValueError("A tested source differs from its archived commit: " + path)
        if execution["config"].get("thinking", {}).get("thinking", {}).get("type") != "disabled":
            raise ValueError("Thinking mode was not explicitly disabled")
        runs[name] = {"commit": implementation_commit, "tasks_sha256": assessment["tasks_sha256"],
            "assessment_sha256": sha256(directory / "assessment.json"), "attempts": count,
            "strict_successes": {arm: group["strict_successes"] for arm, group in assessment["groups"].items()}}
    for name in ("LICENSE", "NOTICE", "TRADEMARK_POLICY.md"):
        original = subprocess.check_output(["git", "show", f"{UPSTREAM}:{name}"], cwd=ROOT)
        if (ROOT / name).read_bytes().replace(b"\r\n", b"\n") != original.replace(b"\r\n", b"\n"):
            raise ValueError("An upstream legal file changed")
    tracked = subprocess.check_output(["git", "ls-files", "--", "**/config.ini", ".env"], cwd=ROOT, text=True)
    if tracked.strip():
        raise ValueError("A private configuration is tracked")
    result = {**manifest(ROOT, "followup-release-audit", {"datasets": datasets}),
        "status": "passed", "runs": runs, "source_and_pairing_integrity": True, "fmp_requests": 0,
        "legal_files_unchanged": True, "private_config_untracked": True,
        "browser_visual_acceptance": "blocked_browser_launch_approval_timeout"}
    write_json(ROOT / "project_notes/run_logs/followup_release_audit.json", result)
    print(json.dumps({"status": "passed", "runs": runs, "fmp_requests": 0}))


if __name__ == "__main__":
    main()
