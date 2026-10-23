"""Archive verified inference weights and retained task artifacts without credentials."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.runtime import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    if not args.output.is_relative_to(ROOT):
        raise ValueError("A workspace-local archive path is required")
    audit = json.loads((ROOT / "project_notes/run_logs/planner_delivery_audit.json").read_text(encoding="utf-8"))
    if audit["status"] != "passed" or args.output.exists():
        raise ValueError("A passed audit and a new archive path are required")
    weights = ROOT / "output/cloud/planner_formal_test_export.zip"
    expected = json.loads((ROOT / "project_notes/evaluations/planner_formal_test_export.json").read_text(encoding="utf-8"))["sha256"]
    if sha256(weights) != expected:
        raise ValueError("The retained weight archive differs")
    with ZipFile(weights) as archive:
        if archive.testzip() is not None:
            raise ValueError("The weight archive failed CRC verification")
    deployment = json.loads((ROOT / "project_notes/run_logs/planner_deployment_checks.json").read_text(encoding="utf-8"))
    browser = json.loads((ROOT / "project_notes/run_logs/planner_browser_checks.json").read_text(encoding="utf-8"))
    web = json.loads((ROOT / "project_notes/run_logs/planner_web_live.json").read_text(encoding="utf-8"))
    identifiers = {row["task_id"] for row in deployment["tasks"]} | set(browser["task_ids"]) | set(web["task_ids"])
    paths = [p for identifier in sorted(identifiers) for p in (ROOT / "output/tasks" / identifier).rglob("*") if p.is_file()]
    paths += [ROOT / "project_notes/run_logs/planner_delivery_audit.json"]
    hashes = {p.relative_to(ROOT).as_posix(): sha256(p) for p in paths}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "x", compression=ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, path.relative_to(ROOT).as_posix())
        archive.writestr("archive_manifest.json", json.dumps(hashes, indent=2))
    with ZipFile(args.output) as archive:
        if archive.testzip() is not None:
            raise ValueError("The task archive failed CRC verification")
    result = {"status": "verified", "base_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "audit_sha256": sha256(ROOT / "project_notes/run_logs/planner_delivery_audit.json"),
        "weights_archive": str(weights.relative_to(ROOT)), "weights_sha256": expected,
        "tasks_archive": str(args.output.relative_to(ROOT)), "tasks_sha256": sha256(args.output),
        "task_ids": sorted(identifiers), "artifact_files": len(paths), "files": hashes,
        "credentials_included": False, "base_weights_included": False}
    write_json(ROOT / "project_notes/run_logs/planner_release_archive.json", result)
    print(json.dumps({key: value for key, value in result.items() if key != "files"}))


if __name__ == "__main__":
    main()
