"""Check staged planner artifacts against runtime byte hashes and delivery receipts."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.runtime import write_json


def main():
    names = subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"], cwd=ROOT).decode().split("\0")
    names = [name for name in names if name]
    if not names:
        raise ValueError("No staged delivery files")
    forbidden = [name for name in names if name.endswith((".ini", ".safetensors", ".db"))
                 or "node_modules/" in name or name.split("/")[-1].startswith(".env")]
    if forbidden or any(name in names for name in ("LICENSE", "NOTICE", "TRADEMARK_POLICY.md")):
        raise ValueError("Credentials, runtime weights, or legal changes entered the archive")
    mismatches = []
    verified = 0
    for name in names:
        critical = name.startswith(("finevolab/", "scripts/", "configs/", "data/training/", "tests/")) or (
            "/planner" in name and name.endswith((".json", ".jsonl", "planner_formal_results.md")))
        if critical:
            staged = subprocess.check_output(["git", "show", ":" + name], cwd=ROOT)
            if hashlib.sha256(staged).hexdigest() != sha256(ROOT / name):
                mismatches.append(name)
            verified += 1
    if mismatches:
        raise ValueError("Staged bytes differ: " + ", ".join(mismatches))
    log = (ROOT / "project_notes/run_logs/planner_final_tests.txt").read_text(encoding="utf-8")
    match = re.search(r"Ran (\d+) tests in ([\d.]+)s", log)
    if not match or int(match[1]) != 158 or "\nOK\n" not in log:
        raise ValueError("The final full regression receipt differs")
    checks = {}
    for name in ("planner_delivery_audit", "planner_offline_checks", "planner_web_live", "planner_web_mock",
                 "planner_frontend_main", "planner_frontend_quality", "planner_frontend_language",
                 "planner_frontend_http", "planner_frontend_language_http", "planner_browser_checks"):
        path = ROOT / f"project_notes/run_logs/{name}.json"
        if json.loads(path.read_text(encoding="utf-8"))["status"] != "passed":
            raise ValueError("A required delivery receipt failed")
        checks[name] = sha256(path)
    result = {"status": "passed", "staged_files": len(names), "byte_verified_files": verified,
        "full_regression_tests": 158, "full_regression_seconds": float(match[2]),
        "receipts": checks, "credentials_staged": False, "legal_files_changed": False}
    write_json(ROOT / "project_notes/run_logs/planner_final_checks.json", result)
    print(json.dumps({key: value for key,value in result.items() if key != "receipts"}))


if __name__ == "__main__":
    main()
