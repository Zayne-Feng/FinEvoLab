"""Verify local delivery artifacts, observed usage, source contracts, and HTTP reports."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json
from scripts.check_fmp_readiness import readiness
from scripts.settle_project_costs import Ledger, collect, read


def main(args):
    started = time.monotonic()
    capture = io.StringIO()
    result = unittest.TextTestRunner(stream=capture).run(unittest.defaultTestLoader.discover(str(ROOT / "tests")))
    if not result.wasSuccessful():
        print(capture.getvalue())
        raise ValueError("The regression suite failed")
    tests = {"passed": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
             "seconds": round(time.monotonic() - started, 3)}
    delivery_path = ROOT / "project_notes/run_logs/final_delivery.json"
    delivery = read(delivery_path)
    if delivery["status"] != "passed" or len(delivery["recovery"]) != 9:
        raise ValueError("The final online delivery record is incomplete")
    core_hashes = {name: digest for name, digest in delivery["source_hashes"].items() if name.startswith("finevolab/")}
    if any(sha256(ROOT / name) != digest for name, digest in core_hashes.items()):
        raise ValueError("Core sources differ from the online delivery execution")
    costs_path = ROOT / "project_notes/evaluations/project_costs.json"
    costs = read(costs_path)
    if any(sha256(ROOT / name) != digest for name, digest in costs["source_sha256"].items()):
        raise ValueError("A canonical cost input changed")
    if any(sha256(ROOT / name) != digest for name, digest in costs["calculator_sha256"].items()):
        raise ValueError("The cost calculator differs from the recorded version")
    ledger = collect(Ledger(ROOT, read(ROOT / "configs/provider_pricing.json")))
    if ledger.entries != costs["entries"] or ledger.unknown != costs["unknown_usage"]:
        raise ValueError("The cost ledger is not reproducible from canonical inputs")
    baseline = "2717499b8e30f242640af08c4ad9afd1113c2d45"
    for name in ("LICENSE", "NOTICE", "TRADEMARK_POLICY.md"):
        historical = subprocess.check_output(["git", "show", f"{baseline}:{name}"], cwd=ROOT)
        if historical != (ROOT / "licenses/finrobot" / name).read_bytes():
            raise ValueError("A mandatory upstream legal file changed")
    private_config = "finrobot_equity/core/config/config.ini"
    tracked = subprocess.check_output(["git", "ls-files", "--", private_config], cwd=ROOT, text=True)
    if tracked.strip():
        raise ValueError("The private configuration must not be tracked")
    with httpx.Client(base_url=args.url, timeout=20) as client:
        response = client.get("/finevolab")
        response.raise_for_status()
        if "language-toggle" not in response.text:
            raise ValueError("The served page does not include the language control")
        page_sha = hashlib.sha256(response.content).hexdigest()
        if page_sha != sha256(ROOT / "finevolab/templates/dashboard.html"):
            raise ValueError("The service is serving a different dashboard")
        for suffix in ("html", "pdf"):
            response = client.get(f"/api/tasks/{delivery['web']['task_id']}/report/{suffix}")
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != delivery["web"]["reports"][suffix]["sha256"]:
                raise ValueError("The report bytes differ from the delivery record")
            if suffix == "pdf" and not response.content.startswith(b"%PDF"):
                raise ValueError("The saved report does not contain PDF bytes")
    frontend = {}
    if args.frontend_dir:
        specifications = (("dashboard_checks.json", "count", 18), ("dashboard_quality.json", "passed", 18),
                          ("dashboard_language_checks.json", "count", 12),
                          ("dashboard_live_quality.json", "count", 35))
        for filename, field, expected in specifications:
            path = args.frontend_dir / filename
            record = read(path)
            if record.get("status") != "passed" or record[field] != expected:
                raise ValueError("A frontend verification record is incomplete")
            frontend[filename] = {"sha256": sha256(path), "result": record}
        path = args.frontend_dir / "dashboard_language_live_checks.json"
        record = read(path)
        if record["status"] != "passed" or len(record["checks"]) != 6:
            raise ValueError("The live language verification is incomplete")
        frontend[path.name] = {"sha256": sha256(path), "result": record}
    fmp = readiness(ROOT / private_config)
    record = {**manifest(ROOT, "final-release-checks", {"url": args.url}),
        "status": "passed_with_external_dependencies", "tests": tests, "core_source_files_verified": len(core_hashes),
        "online_delivery": {"path": delivery_path.relative_to(ROOT).as_posix(), "sha256": sha256(delivery_path),
            "recovery_trajectories": 9, "web_task_id": delivery["web"]["task_id"]},
        "cost_ledger": {"path": costs_path.relative_to(ROOT).as_posix(), "sha256": sha256(costs_path),
            "canonical_entries": len(ledger.entries), "estimated_usd_lower": costs["known_estimated_cost_lower"],
            "estimated_usd_upper": costs["known_estimated_cost_upper"]},
        "frontend": frontend, "dashboard_sha256": page_sha, "upstream_legal_files": "unchanged",
        "private_config_tracked": False, "fmp": fmp,
        "external_dependencies": {"fmp_original_report": "not_run_without_data_key",
            "provider_invoice": "not_supplied", "rendered_browser_automation": "runtime_unavailable"}}
    write_json(ROOT / "project_notes/run_logs/final_release_checks.json", record)
    print(json.dumps({"status": record["status"], "tests": tests,
        "canonical_cost_entries": len(ledger.entries), "frontend_suites": len(frontend)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8003")
    parser.add_argument("--frontend-dir", type=Path)
    main(parser.parse_args())
