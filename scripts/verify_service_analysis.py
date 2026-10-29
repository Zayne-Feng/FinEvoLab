"""Verify authenticated analysis and report delivery with live retrieval services."""

import argparse
import hashlib
import json
import logging
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from finevolab.config import ModelConfig
from finevolab.experience import create_app
from dataclasses import asdict
from finevolab.runtime import RunBudget, write_json
from finevolab.workflow import run_question
from benchmark_retrieval_services import configuration
from benchmark_agent_roles import UncachedExecutor


def main(args):
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = ModelConfig.load()
    candidate = json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8"))
    question = "Calculate MSFT FY2024 revenue growth from FY2023, and explain the change with annual-report citations."
    headers = {"X-FinEvoLab": "1"}
    results = []
    for arm, mode in (("B", "self_check"), ("D", "reviewer")):
        configuration(arm, "service-http-verification")
        with tempfile.TemporaryDirectory(prefix="service-http-", dir=ROOT / "output/refinement") as directory:
            root = Path(directory)
            templates = root / "finevolab/templates"
            templates.mkdir(parents=True)
            shutil.copyfile(ROOT / "finevolab/templates/experience.html", templates / "experience.html")
            shutil.copytree(args.site, root / "output/showcase")
            invitation = secrets.token_urlsafe(32)
            policy = {"budget": asdict(RunBudget(timeout_seconds=240)), "daily_tasks": 1, "concurrent_tasks": 1, "enabled": True,
                "users": {"verification": {"invite_sha256": hashlib.sha256(invitation.encode()).hexdigest(), "daily_tasks": 1}}}
            policy_path = root / "access.json"
            write_json(policy_path, policy)

            async def runner(task_id, request):
                with patch("finevolab.grounded.ToolExecutor", UncachedExecutor):
                    state = await run_question(task_id, request, settings, candidate, review_mode=mode)
                source = ROOT / "output/tasks" / task_id
                target = root / "output/tasks" / task_id
                shutil.copytree(source, target)
                return state

            app = create_app(root, policy_path, runner)
            with TestClient(app) as client:
                assert client.post("/experience/api/tasks", json={"question": question, "request_id": "service-http-request"}, headers=headers).status_code == 401
                assert client.post("/experience/api/login", json={"invite": invitation}, headers=headers).status_code == 200
                submitted = client.post("/experience/api/tasks", json={"question": question, "request_id": "service-http-request"}, headers=headers)
                assert submitted.status_code == 202, submitted.text
                task_id = submitted.json()["task_id"]
                for _ in range(1200):
                    response = client.get("/experience/api/tasks/" + task_id)
                    assert response.status_code == 200
                    result = response.json()
                    if result["status"] != "running":
                        break
                    time.sleep(.25)
                assert result["status"] == "complete", result.get("status")
                assert result["review"]["status"] == "passed"
                assert result["validation"]["status"] == "passed"
                assert result["business_status"] == "complete"
                assert result["usage"]["requests"] <= 6
                assert any(e.get("backend") == "redis" for e in result["retrieval"])
                if arm == "D":
                    assert any(e.get("actual_backend") == "milvus" for e in result["retrieval"])
                html = client.get("/experience/api/tasks/" + task_id + "/report/html")
                pdf = client.get("/experience/api/tasks/" + task_id + "/report/pdf")
                assert html.status_code == pdf.status_code == 200
                assert "Evidence review" in html.text and pdf.content.startswith(b"%PDF")
                client.post("/experience/api/logout", headers=headers)
                assert client.get("/experience/api/tasks/" + task_id).status_code == 401
                results.append({"arm": arm, "review_mode": mode, "task_id": task_id,
                    "validation": "passed", "review": "passed", "reports": ["html", "pdf"],
                    "usage": result["usage"], "anonymous_access_rejected": True, "logout_access_rejected": True,
                    "retrieval": result["retrieval"]})
    write_json(args.output, {"status": "passed", "transport": "FastAPI TestClient with real model and services",
        "cases": results, "public_service_modified": False})
    print(json.dumps({"status": "passed", "workflows": len(results), "model_requests": sum(r["usage"]["requests"] for r in results)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--site", type=Path, default=ROOT / "output/showcase")
    main(parser.parse_args())
