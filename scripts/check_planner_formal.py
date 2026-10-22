"""Report formal experiment completion using verified export and report receipts."""

import hashlib
import json
from pathlib import Path


def status(root: Path) -> dict:
    directory = root / "project_notes/evaluations"
    report = directory / "planner_formal_results.md"
    result = {"status": "in_progress", "phase": "training_or_validation",
              "gpu_exports_verified": False, "final_report_verified": False, "report": str(report)}
    if (directory / "planner_formal_selection.json").exists():
        result["phase"] = "test_or_export"
    completion = directory / "planner_formal_completion.json"
    if completion.exists():
        receipt = json.loads(completion.read_text(encoding="utf-8"))
        if receipt.get("status") == "completed" and receipt.get("test_exports_verified") is True:
            result.update(phase="local_analysis_and_replay", gpu_exports_verified=True)
    finalization = directory / "planner_formal_finalization.json"
    if finalization.exists():
        receipt = json.loads(finalization.read_text(encoding="utf-8"))
        machine_report = directory / "planner_formal_results.json"
        expected_results = receipt.get("results_sha256")
        if expected_results and (not machine_report.exists()
                or hashlib.sha256(machine_report.read_bytes()).hexdigest() != expected_results):
            result.update(status="verification_failed", phase="machine_report_hash_mismatch")
            return result
        if (receipt.get("status") == "completed" and result["gpu_exports_verified"]
                and receipt.get("report_sha256") and report.exists()):
            if hashlib.sha256(report.read_bytes()).hexdigest() == receipt.get("report_sha256"):
                result.update(status="completed", phase="completed", final_report_verified=True)
            else:
                result.update(status="verification_failed", phase="report_hash_mismatch")
        elif receipt.get("status") == "completed" and result["gpu_exports_verified"]:
            if machine_report.exists():
                data = json.loads(machine_report.read_text(encoding="utf-8"))
                bundle = root / "data/training/planner_formal/frozen_v1/manifest.json"
                if (data.get("status") == "completed" and bundle.exists()
                        and data.get("bundle_sha256") == hashlib.sha256(bundle.read_bytes()).hexdigest()
                        and data.get("code_sha256") == receipt.get("analysis_sha256")
                        and (not receipt.get("results_sha256") or receipt["results_sha256"]
                             == hashlib.sha256(machine_report.read_bytes()).hexdigest())):
                    result.update(status="completed", phase="completed", report=str(machine_report),
                                  report_format="json", machine_report_available=True)
                else:
                    result.update(status="verification_failed", phase="machine_report_identity_mismatch")
    return result


if __name__ == "__main__":
    print(json.dumps(status(Path(__file__).resolve().parents[1]), indent=2))
