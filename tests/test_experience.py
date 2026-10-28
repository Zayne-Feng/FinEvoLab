"""Verify invitation boundaries, atomic quotas, task ownership, and public projections."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from finevolab.experience import AccessStore, AnalysisRequest, create_app
from finevolab.experience_data import build_public_data, public_result
from finevolab.runtime import write_json

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"X-FinEvoLab": "1"}
TOKENS = {"alice": "a" * 43, "bob": "b" * 43}


class ExperienceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.site = self.root / "output/showcase"
        self.site.mkdir(parents=True)
        (self.site / "experience.html").write_text('<html data-live="false"></html>')
        self.policy = {"daily_tasks": 5, "concurrent_tasks": 1, "enabled": True,
            "budget": {"max_model_calls": 6, "max_tool_calls": 10, "max_total_tokens": 20000,
                       "timeout_seconds": 240},
            "users": {name: {"invite_sha256": hashlib.sha256(token.encode()).hexdigest(), "daily_tasks": 2}
                      for name, token in TOKENS.items()}}
        self.config = self.root / "access.json"

    def app(self, runner=None, public_url=None):
        self.config.write_text(json.dumps(self.policy))
        return create_app(self.root, self.config, runner or self.runner, public_url=public_url)

    async def runner(self, task_id, question):
        directory = self.root / "output/tasks" / task_id
        directory.mkdir(parents=True)
        state = {"task_id": task_id, "status": "complete", "versions": {"private": "hidden"},
            "phases": {"analyze": {"status": "complete", "result": {
                "request": {"intent": "metric"}, "answer": {"status": "complete"},
                "messages": [{"private": "hidden"}], "usage": {"requests": 1, "total_tokens": 20}}},
                "validate": {"status": "complete", "result": {"status": "passed"}},
                "report": {"status": "complete"}}}
        write_json(directory / "state.json", state)
        (directory / "report").mkdir()
        (directory / "report/report.pdf").write_bytes(b"%PDF-test")
        return state

    def login(self, client, user="alice"):
        response = client.post("/experience/api/login", json={"invite": TOKENS[user]}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def submit(self, client, key="request-0000000001"):
        return client.post("/experience/api/tasks", json={"question": "MSFT FY2024 revenue", "request_id": key}, headers=HEADERS)

    def wait(self, client, task_id):
        for _ in range(100):
            state = client.get("/experience/api/tasks/" + task_id).json()
            if state["status"] != "running":
                return state
            time.sleep(.01)
        self.fail("Task did not finish")

    def test_authentication_and_old_routes_are_closed(self):
        with TestClient(self.app()) as client:
            for route in ("session", "tasks", "tasks/unknown", "tasks/unknown/report/pdf"):
                self.assertEqual(client.get("/experience/api/" + route).status_code, 401)
            self.assertEqual(self.submit(client).status_code, 401)
            for route in ("/api/questions", "/api/uploads", "/api/tasks", "/openapi.json", "/config.ini"):
                self.assertEqual(client.get(route).status_code, 404)
            self.assertIn('data-live="true"', client.get("/experience.html").text)

    def test_origin_cookie_logout_and_expiry(self):
        with TestClient(self.app()) as client:
            self.assertEqual(client.post("/experience/api/login", json={"invite": TOKENS["alice"]}).status_code, 403)
            self.assertEqual(client.post("/experience/api/login", json={"invite": TOKENS["alice"]}, headers={**HEADERS, "Origin": "https://other.invalid"}).status_code, 403)
            response = self.login(client)
            self.assertIn("HttpOnly", response.headers["set-cookie"])
            self.assertIn("SameSite=strict", response.headers["set-cookie"])
            self.assertEqual(client.get("/experience/api/session").headers["cache-control"], "no-store")
            client.post("/experience/api/logout", headers=HEADERS)
            self.assertEqual(client.get("/experience/api/session").status_code, 401)
            self.login(client)
            with client.app.state.access.connect() as db:
                db.execute("UPDATE sessions SET expires=0")
            self.assertEqual(client.get("/experience/api/session").status_code, 401)

    def test_task_output_ownership_idempotency_and_report(self):
        with TestClient(self.app()) as client:
            self.login(client)
            first = self.submit(client)
            self.assertEqual(first.status_code, 202)
            task_id = first.json()["task_id"]
            state = self.wait(client, task_id)
            self.assertEqual(state["status"], "complete")
            self.assertEqual(state["validation"]["status"], "passed")
            self.assertNotIn("hidden", json.dumps(state))
            self.assertTrue(self.submit(client).json()["reused"])
            self.assertEqual(client.get("/experience/api/session").json()["used"], 1)
            conflict = client.post("/experience/api/tasks", json={"question": "different", "request_id": "request-0000000001"}, headers=HEADERS)
            self.assertEqual(conflict.status_code, 409)
            report = client.get(f"/experience/api/tasks/{task_id}/report/pdf")
            self.assertTrue(report.content.startswith(b"%PDF"))
            self.assertIn("attachment", report.headers["content-disposition"])
            self.login(client, "bob")
            self.assertEqual(client.get("/experience/api/tasks").json(), [])
            self.assertEqual(client.get(f"/experience/api/tasks/{task_id}").status_code, 404)
            self.assertEqual(client.get(f"/experience/api/tasks/{task_id}/report/pdf").status_code, 404)

    def test_daily_and_global_quotas(self):
        self.policy["daily_tasks"] = 2
        with TestClient(self.app()) as client:
            self.login(client)
            for i in range(2):
                response = self.submit(client, "request-000000000" + str(i))
                self.wait(client, response.json()["task_id"])
            self.assertEqual(self.submit(client, "request-0000000003").status_code, 429)
            self.login(client, "bob")
            self.assertEqual(self.submit(client).status_code, 429)
            self.assertEqual(client.get("/experience/api/session").json()["used"], 0)

    def test_pause_and_failed_runs_consume_reservation(self):
        async def broken(task_id, question):
            raise RuntimeError("Private provider detail")
        with TestClient(self.app(broken)) as client:
            self.login(client)
            task_id = self.submit(client).json()["task_id"]
            state = self.wait(client, task_id)
            self.assertEqual(state["error_type"], "RuntimeError")
            self.assertNotIn("Private", json.dumps(state))
            self.assertEqual(client.get("/experience/api/session").json()["used"], 1)
        self.policy["enabled"] = False
        with TestClient(self.app()) as client:
            self.login(client)
            self.assertEqual(self.submit(client, "request-0000000002").status_code, 503)

    def test_atomic_concurrency_reservation(self):
        access = AccessStore(self.root, self.policy)
        def reserve(i):
            try:
                access.reserve("alice", AnalysisRequest(question="test", request_id="request-000000000" + str(i)))
                return 202
            except Exception as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(reserve, range(4)))
        self.assertEqual(codes.count(202), 1)
        self.assertEqual(codes.count(429), 3)

    def test_restart_marks_interrupted_without_recharging(self):
        app = self.app()
        row, _ = app.state.access.reserve("alice", AnalysisRequest(question="test", request_id="request-0000000001"))
        with TestClient(app) as client:
            self.login(client)
            self.assertEqual(client.get("/experience/api/tasks/" + row["id"]).json()["status"], "interrupted")
            self.assertEqual(client.get("/experience/api/session").json()["used"], 1)

    def test_login_rate_limit(self):
        with TestClient(self.app()) as client:
            for _ in range(10):
                self.assertEqual(client.post("/experience/api/login", json={"invite": "x" * 43}, headers=HEADERS).status_code, 401)
            self.assertEqual(client.post("/experience/api/login", json={"invite": TOKENS["alice"]}, headers=HEADERS).status_code, 429)

    def test_public_https_cookie_and_origin(self):
        app = self.app(public_url="https://app.lunelu.com")
        with TestClient(app, base_url="https://app.lunelu.com") as client:
            response = client.post("/experience/api/login", json={"invite": TOKENS["alice"]},
                                   headers={**HEADERS, "Origin": "https://app.lunelu.com"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("; Secure", response.headers["set-cookie"])
            self.assertEqual(client.get("/experience/api/session").status_code, 200)
            self.assertEqual(client.get("/experience/api/health").json()["public_origin"], "https://app.lunelu.com")
            self.assertEqual(client.post("/experience/api/logout", headers={**HEADERS,
                "Origin": "https://other.invalid"}).status_code, 403)
            client.post("/experience/api/logout", headers=HEADERS)
            self.assertEqual(client.get("/experience/api/session").status_code, 401)

    def test_forwarded_https_requires_a_trusted_connector(self):
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        app = ProxyHeadersMiddleware(self.app(public_url="https://app.lunelu.com"),
                                     trusted_hosts=["127.0.0.1", "::1"])
        headers = {**HEADERS, "Origin": "https://app.lunelu.com", "X-Forwarded-Proto": "https"}
        with TestClient(app, base_url="http://app.lunelu.com", client=("127.0.0.1", 40000)) as client:
            response = client.post("/experience/api/login", json={"invite": TOKENS["alice"]}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertIn("; Secure", response.headers["set-cookie"])
        with TestClient(app, base_url="http://app.lunelu.com", client=("198.51.100.2", 40000)) as client:
            self.assertEqual(client.post("/experience/api/login", json={"invite": TOKENS["alice"]},
                                         headers=headers).status_code, 403)

    def test_local_http_and_public_host_allowlist(self):
        app = self.app(public_url="https://app.lunelu.com")
        with TestClient(app, base_url="http://127.0.0.1:8085") as client:
            response = self.login(client)
            self.assertNotIn("; Secure", response.headers["set-cookie"])
            self.assertEqual(client.get("/experience/api/session").status_code, 200)
            self.assertEqual(client.get("/experience/api/health", headers={"Host": "other.invalid"}).status_code, 400)

    def test_invalid_public_origin_is_rejected(self):
        for origin in ("http://app.lunelu.com", "https://app.lunelu.com/path", "https://app.lunelu.com:443",
                       "https://user:secret@app.lunelu.com", "https://app.lunelu.com?query=1"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                self.app(public_url=origin)

    def test_execution_and_business_statuses_are_distinct(self):
        result = public_result({"task_id": "a" * 32, "status": "complete", "phases": {
            "analyze": {"status": "complete", "result": {"answer": {"status": "insufficient_data"}}}}})
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["business_status"], "insufficient_data")

    def test_public_replay_preserves_quotes_and_excludes_private_fields(self):
        data = build_public_data(ROOT)
        replay = data["replays"][0]
        self.assertEqual(replay["validation"]["status"], "passed")
        self.assertIn("NVDA", replay["question"])
        self.assertEqual(replay["usage"]["total_tokens"], 2402)
        self.assertTrue(any(e["tool"] == "search_reports" and "output" in e for e in replay["events"]))
        text = json.dumps(data)
        for forbidden in ("E:\\\\Desktop", "messages", "source_path", "api_key"):
            self.assertNotIn(forbidden, text)
        raw = (ROOT / replay["source"]).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), replay["sha256"])
        self.assertTrue(all(row["period_start"] and row["period_end"] and row["evidence"]["locator"] for row in data["observations"]))


if __name__ == "__main__":
    unittest.main()
