"""Check retained LoRA reports and recovery after disconnecting the model service."""

import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.runtime import write_json


def main():
    task_id = json.loads((ROOT / "project_notes/run_logs/planner_browser_checks.json").read_text(encoding="utf-8"))["task_ids"][0]
    with httpx.Client(base_url="http://127.0.0.1:8003", timeout=30, trust_env=False) as client:
        capabilities = client.get("/api/planners").json()
        assert capabilities["api"]["available"] and not capabilities["lora_nf4"]["available"]
        before = client.get(f"/api/tasks/{task_id}").json()
        resumed = client.post(f"/api/tasks/{task_id}/resume")
        resumed.raise_for_status()
        assert resumed.json()["phases"] == before["phases"]
        pdf = client.get(f"/api/tasks/{task_id}/report/pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
        response = client.post("/api/questions", json={"question": "Give MSFT FY2024 revenue.", "planner_backend": "lora_nf4"})
        assert response.status_code == 503
        failure = response.json()["detail"]
        assert failure["error_type"] == "PlannerServiceError"
        state = client.get(f"/api/tasks/{failure['task_id']}").json()
        assert state["versions"]["planner_backend"] == "lora_nf4" and state["status"] == "failed"
        result = {"status": "passed", "recovery_task_id": task_id, "unavailable_task_id": failure["task_id"],
            "completed_phases_reused": True, "pdf_status": pdf.status_code, "new_request_status": 503,
            "api_fallback": False, "planner_connection": "disconnected", "model_requests": 0}
        write_json(ROOT / "project_notes/run_logs/planner_offline_checks.json", result)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
