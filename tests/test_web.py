"""Verify the offline analysis flow and report routes."""

from fastapi.testclient import TestClient
import unittest

from finevolab.web import app


class WebTests(unittest.TestCase):
    def test_offline_workflow_and_download(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/").status_code, 200)
            response = client.post("/api/tasks", json={"company": "MSFT", "fiscal_year": 2024, "mode": "offline"})
            self.assertEqual(response.status_code, 200, response.text)
            state = response.json()
            self.assertEqual(state["status"], "complete")
            task_id = state["task_id"]
            self.assertEqual(client.get(f"/api/tasks/{task_id}/report/pdf").content[:5], b"%PDF-")
            resumed = client.post(f"/api/tasks/{task_id}/resume")
            self.assertEqual(resumed.status_code, 200, resumed.text)
            self.assertEqual(resumed.json()["phases"], state["phases"])

    def test_resume_preserves_saved_question_execution_options(self):
        from unittest.mock import AsyncMock, patch
        from finevolab.config import ModelConfig
        versions = {"workflow": "question", "question": "MSFT FY2024 revenue", "tool_backend": "mcp",
                    "retrieval_variant": "baseline", "review_mode": "self_check", "render": False,
                    "timeout_seconds": 73, "corpus_profile": "contextual", "planner_backend": "api",
                    "upload_ids": [], "budget": {"max_model_calls": 3}}
        state = {"versions": versions, "status": "complete"}
        runner = AsyncMock(return_value=state)
        with patch("finevolab.web.get_task", return_value=state), patch("finevolab.web.run_question", runner), \
             patch("finevolab.web.ModelConfig.load", return_value=ModelConfig("fixture", "https://fixture.invalid/", "fixture")):
            with TestClient(app) as client:
                response = client.post("/api/tasks/" + "a" * 32 + "/resume")
        self.assertEqual(response.status_code, 200)
        for name in ("tool_backend", "retrieval_variant", "review_mode", "render", "timeout_seconds",
                     "corpus_profile", "planner_backend", "upload_ids"):
            self.assertEqual(runner.await_args.kwargs.get(name), versions[name], name)
        self.assertEqual(runner.await_args.kwargs["budget"].max_model_calls, 3)

    def test_invalid_parameters_and_missing_task(self):
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/tasks", json={"company": "../config"}).status_code, 422)
            self.assertEqual(client.get("/api/tasks/invalid").status_code, 404)

    def test_router_is_available_inside_upstream_application(self):
        from finevolab.integrated_web import app as integrated_app
        with TestClient(integrated_app) as client:
            self.assertEqual(client.get("/finevolab").status_code, 200)
            self.assertEqual(client.get("/static/style.css").status_code, 200)
            self.assertEqual(client.get("/api/finevolab/history").status_code, 200)


if __name__ == "__main__":
    unittest.main()
