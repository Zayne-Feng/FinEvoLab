"""Verify live planner routing, source-bound execution, reports, and cached recovery."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json


def service_health():
    with httpx.Client(timeout=3, trust_env=False) as client:
        response = client.get("http://127.0.0.1:8787/health")
        response.raise_for_status()
        return response.json()


def main(args):
    destination = ROOT / "project_notes/run_logs/planner_deployment_checks.json"
    if destination.exists():
        raise ValueError("Preserve the existing receipt before starting a new verification")
    while True:
        try:
            if service_health()["status"] == "ready":
                break
        except (httpx.HTTPError, KeyError):
            pass
        if not args.wait_service:
            raise ValueError("The planner service is unavailable")
        print(json.dumps({"status": "waiting_for_gpu_service"}), flush=True)
        time.sleep(30)
    cases = [
        ("metric", "Give MSFT FY2024 revenue.", "complete"),
        ("growth", "Calculate MSFT FY2024 revenue year-over-year growth in percent.", "complete"),
        ("comparison", "Compare MSFT and AAPL FY2024 revenue and state their actual fiscal dates.", "complete"),
        ("alignment", "Check only whether MSFT and AAPL FY2024 fiscal date ranges are identical; do not compare amounts.", "periods_not_aligned"),
        ("quarter_boundary", "Give MSFT Q1 FY2024 revenue.", "insufficient_data"),
        ("selected_upload", "From the selected annual report, give revenue for FY2024.", "complete"),
        ("hybrid", "Give MSFT FY2024 revenue and explain its change using the annual report.", "complete"),
    ]
    records = []
    record = {**manifest(ROOT, "planner-deployment-verification", {"cases": cases,
        "backends": ["lora_bf16", "lora_nf4"], "origin": args.origin}), "status": "running", "tasks": records}
    write_json(destination, record)
    with httpx.Client(base_url=args.origin, timeout=200, trust_env=False) as client:
        with (ROOT / "data/raw/sec_msft_2024.html").open("rb") as handle:
            upload = client.post("/api/uploads", data={"company": "MSFT", "fiscal_year": "2024"},
                                 files={"file": ("msft_2024.html", handle, "text/html")})
        upload.raise_for_status()
        uploaded = upload.json()
        document_id = uploaded.get("id") or uploaded.get("document", {}).get("id")
        if document_id != sha256(ROOT / "data/raw/sec_msft_2024.html"):
            raise ValueError("The uploaded document identity differs")
        capabilities = client.get("/api/planners").json()
        if not all(capabilities[name]["available"] for name in ("lora_bf16", "lora_nf4")):
            raise ValueError("A required LoRA backend is unavailable")
        start_count = service_health()["completed_requests"]
        bad_contract = httpx.post("http://127.0.0.1:8787/v1/plans", json={"backend": "lora_bf16",
            "question": "Give revenue", "contract_sha256": "0" * 64}, timeout=5, trust_env=False)
        if bad_contract.status_code != 409 or service_health()["completed_requests"] != start_count:
            raise ValueError("An invalid contract reached generation")
        record["contract_rejection"] = {"status_code": 409, "model_requests": 0}
        for backend in ("lora_bf16", "lora_nf4"):
            for name, question, status in cases:
                before = service_health()["completed_requests"]
                response = client.post("/api/questions", json={"question": question, "planner_backend": backend,
                    "upload_ids": [document_id] if name == "selected_upload" else []})
                response.raise_for_status()
                state = response.json()
                if state["status"] != "complete" or state["versions"]["planner_backend"] != backend:
                    raise ValueError("The selected planner workflow did not complete")
                output = state["phases"]["analyze"]["result"]
                if output["planner"]["backend"] != backend or not output["planner"]["service_request_id"]:
                    raise ValueError("The response did not use the selected real planner")
                after = service_health()["completed_requests"]
                if after != before + 1:
                    raise ValueError("The planner request count differs")
                task_id = state["task_id"]
                pdf = client.get(f"/api/tasks/{task_id}/report/pdf")
                html = client.get(f"/api/tasks/{task_id}/report/html")
                pdf.raise_for_status()
                html.raise_for_status()
                if not pdf.content.startswith(b"%PDF-"):
                    raise ValueError("The generated report is not a PDF")
                resume = client.post(f"/api/tasks/{task_id}/resume")
                resume.raise_for_status()
                if resume.json()["phases"] != state["phases"] or service_health()["completed_requests"] != after:
                    raise ValueError("Completed recovery repeated model work")
                business_passed = output["answer"]["status"] == status
                if name == "hybrid":
                    business_passed = business_passed and output.get("overall_status") == "complete"
                if name == "comparison":
                    business_passed = business_passed and output["answer"]["periods_aligned"] is False
                if name == "selected_upload":
                    business_passed = business_passed and output["upload_ids"] == [document_id]
                records.append({"case": name, "backend": backend, "task_id": task_id,
                    "pipeline_passed": True, "business_passed": business_passed,
                    "business_status": output["answer"]["status"], "request": output["request"],
                    "planner": output["planner"], "usage": output["usage"], "model_usage": output["model_usage"],
                    "report_hashes": {"pdf": hashlib.sha256(pdf.content).hexdigest(), "html": hashlib.sha256(html.content).hexdigest()},
                    "recovery_reused": True})
                write_json(destination, record)
                print(json.dumps({"backend": backend, "case": name, "pipeline_passed": True,
                                  "business_passed": business_passed}), flush=True)
        record.update(status="passed", completed_at=datetime.now(timezone.utc).isoformat(),
            business_passes=sum(row["business_passed"] for row in records), expected_tasks=14,
            actual_gpu_requests=service_health()["completed_requests"] - start_count)
        write_json(destination, record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://127.0.0.1:8003")
    parser.add_argument("--wait-service", action="store_true")
    main(parser.parse_args())
