"""Verify live agent execution through durable business phases."""

import asyncio
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.workflow import run_task


def main() -> None:
    task_id = uuid4().hex
    try:
        state = asyncio.run(run_task(task_id, "MSFT", 2024, mode="agent"))
    except Exception as error:
        print(json.dumps({"task_id": task_id, "status": "failed", "error_type": type(error).__name__,
                          "status_code": getattr(error, "status_code", None)}))
        raise SystemExit(1) from None
    resumed = asyncio.run(run_task(task_id, "MSFT", 2024, mode="agent"))
    if state["phases"] != resumed["phases"]:
        raise ValueError("Completed phases changed during recovery")
    report_path = Path(state["phases"]["report"]["result"]["pdf"])
    summary = {"task_id": task_id, "status": state["status"], "versions": state["versions"],
               "answer": state["phases"]["analyze"]["result"]["answer"],
               "usage": state["phases"]["analyze"]["result"]["usage"],
               "history": state["history"], "resumed_without_model_call": True,
               "pdf_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest()}
    target = ROOT / "project_notes/run_logs/workflow_smoke.json"
    if target.exists():
        summary["previous_checks"] = [json.loads(target.read_text(encoding="utf-8-sig"))]
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"task_id": task_id, "status": state["status"], "usage": summary["usage"],
                      "resumed_without_model_call": True}, indent=2))


if __name__ == "__main__":
    main()
