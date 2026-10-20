"""Verify selected-source HTTP reports and controlled report-phase recovery."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.config import ModelConfig
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json
from finevolab.scope_evaluation import task_store
from finevolab.workflow import run_question


async def fault_controls(settings, candidate):
    documents = json.loads((ROOT / "data/evaluation/scope_v3/documents.json").read_text(encoding="utf-8"))
    scenarios = [("MSFT", 2024, "Give MSFT FY2024 revenue with its fiscal dates.", False),
        ("NVDA", 2024, "Using only the selected NVDA FY2024 report, calculate annual revenue growth and explain its disclosed business drivers with citations.", True),
        ("TSLA", 2023, "Using only the selected TSLA FY2023 report, give annual operating income with original units and fiscal dates.", True)]
    rows = []
    for company, year, question, upload in scenarios:
        identifiers = [d["id"] for d in documents if d["company"] == company and d["report_year"] == year] if upload else []
        if identifiers:
            task_store(ROOT, {"upload_ids": identifiers}, documents)
        for arm in ("no_fault", "checkpoint", "restart"):
            task_id = uuid4().hex
            failed_state = None
            if arm != "no_fault":
                with patch("finevolab.reporting.build_analysis_report", side_effect=RuntimeError("Injected report failure")):
                    try:
                        await run_question(task_id, question, settings, candidate, upload_ids=identifiers)
                    except RuntimeError:
                        failed_state = json.loads((ROOT / "output/tasks" / task_id / "state.json").read_text(encoding="utf-8"))
                if failed_state is None or failed_state["phases"]["analyze"]["status"] != "complete":
                    raise ValueError("The failure was not isolated to report generation")
            completed_id = uuid4().hex if arm == "restart" else task_id
            state = await run_question(completed_id, question, settings, candidate, upload_ids=identifiers)
            output = state["phases"]["analyze"]["result"]
            if state["status"] != "complete" or output["answer"]["status"] != "complete":
                raise ValueError("A recovery control did not complete")
            observed = [output["usage"]]
            if arm == "restart":
                observed.insert(0, failed_state["phases"]["analyze"]["result"]["usage"])
            rows.append({"company": company, "arm": arm, "task_id": completed_id,
                "failed_task_id": task_id if failed_state else None, "recovered": True,
                "requests": sum(u["requests"] for u in observed), "tokens": sum(u["total_tokens"] for u in observed),
                "analysis_phase_executions": len(observed), "usage": observed,
                "history": state["history"]})
            print(json.dumps({"suite": "recovery", "company": company, "arm": arm, "status": "passed"}), flush=True)
    return rows


def main(args):
    settings = ModelConfig.load()
    candidate = json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8"))
    document = next(d for d in json.loads((ROOT / "data/evaluation/scope_v3/documents.json").read_text(encoding="utf-8"))
                    if d["company"] == "NVDA" and d["report_year"] == 2024)
    question = "Using only the selected NVDA FY2024 annual report, calculate annual revenue growth and explain its disclosed business drivers, citing original passages."
    with httpx.Client(base_url=args.url, timeout=240) as client:
        page = client.get("/finevolab")
        page.raise_for_status()
        if "question-documents" not in page.text:
            raise ValueError("The served dashboard does not expose source selection")
        response = client.post("/api/uploads", data={"company": "NVDA", "fiscal_year": "2024"},
            files={"file": ("nvda_2024.html", (ROOT / document["path"]).read_bytes(), "text/html")})
        response.raise_for_status()
        if response.json()["id"] != document["id"]:
            raise ValueError("HTTP upload identity differs from the pinned source")
        response = client.post("/api/questions", json={"question": question, "upload_ids": [document["id"]]})
        response.raise_for_status()
        state = response.json()
        output = state["phases"]["analyze"]["result"]
        if output["answer_type"] != "hybrid" or output["overall_status"] != "complete" or output["upload_ids"] != [document["id"]]:
            raise ValueError("The selected-source HTTP analysis did not complete")
        if any(e["source_sha256"] != document["id"] for e in output["evidence"]):
            raise ValueError("An unselected source entered the report")
        reports = {}
        for format in ("html", "pdf"):
            report = client.get(f"/api/tasks/{state['task_id']}/report/{format}")
            report.raise_for_status()
            path = ROOT / "output/pdf" / f"selected_source_{state['task_id']}.{format}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(report.content)
            if format == "pdf" and not report.content.startswith(b"%PDF"):
                raise ValueError("The report is not a PDF document")
            reports[format] = {"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "bytes": len(report.content)}
        resumed = client.post(f"/api/tasks/{state['task_id']}/resume")
        resumed.raise_for_status()
        if resumed.json()["phases"] != state["phases"] or resumed.json()["history"] != state["history"]:
            raise ValueError("Resuming a completed task repeated a business phase")
        web = {"task_id": state["task_id"], "upload_id": document["id"], "answer_type": output["answer_type"],
            "status": output["overall_status"], "usage": output["usage"], "reports": reports,
            "completed_resume_reexecuted_phases": 0, "source_validation": state["phases"]["validate"]["result"]}
    recovery = asyncio.run(fault_controls(settings, candidate))
    result = {**manifest(ROOT, args.run_id, {"url": args.url, "model": settings.model, "thinking": settings.extra_body}),
        "status": "passed", "web": web, "recovery": recovery}
    write_json(ROOT / "project_notes/run_logs" / f"{args.run_id}.json", result)
    print(json.dumps({"status": "passed", "web_task_id": web["task_id"], "recovery_trajectories": len(recovery)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8003")
    parser.add_argument("--run-id", default="scope_delivery_v1")
    main(parser.parse_args())
