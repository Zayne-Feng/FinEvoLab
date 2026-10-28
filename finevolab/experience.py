"""Serve invite-authenticated, owner-scoped financial analysis."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from filelock import FileLock
from pydantic import BaseModel, Field

from finevolab.config import ModelConfig
from finevolab.experience_data import public_result
from finevolab.runtime import RunBudget, read_json
from finevolab.workflow import run_question

ROOT = Path(__file__).resolve().parents[1]
COOKIE = "finevolab_session"


class LoginRequest(BaseModel):
    invite: str = Field(min_length=20, max_length=200)


class AnalysisRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    request_id: str = Field(pattern=r"^[a-zA-Z0-9-]{16,64}$")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AccessStore:
    def __init__(self, directory: Path, policy: dict):
        directory.mkdir(parents=True, exist_ok=True)
        self.path, self.policy = directory / "access.sqlite3", policy
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, owner TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS attempts (client TEXT, at REAL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, owner TEXT, request_id TEXT, question TEXT, day TEXT,
                    status TEXT, error_type TEXT, UNIQUE(owner, request_id));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def owner(self, request: Request) -> str:
        token = request.cookies.get(COOKIE, "")
        with self.connect() as db:
            row = db.execute("SELECT owner FROM sessions WHERE token=? AND expires>?",
                             (digest(token), time.time())).fetchone()
        if row is None or row["owner"] not in self.policy["users"]:
            raise HTTPException(401, "Authentication required")
        return row["owner"]

    def login(self, invite: str, client: str) -> tuple[str, str]:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE at<?", (now - 600,))
            db.execute("DELETE FROM sessions WHERE expires<?", (now,))
            count = db.execute("SELECT COUNT(*) FROM attempts WHERE client=?", (client,)).fetchone()[0]
            if count >= 10:
                raise HTTPException(429, "Login attempts exceeded")
            db.execute("INSERT INTO attempts VALUES (?,?)", (client, now))
        owner = next((name for name, item in self.policy["users"].items()
                      if hmac.compare_digest(digest(invite), item["invite_sha256"])), None)
        if owner is None:
            raise HTTPException(401, "Invalid invitation")
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (digest(token), owner, now + 12 * 3600))
        return owner, token

    def reserve(self, owner: str, request: AnalysisRequest) -> tuple[dict, bool]:
        day = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE owner=? AND request_id=?", (owner, request.request_id)).fetchone()
            if row:
                if row["question"] != request.question:
                    raise HTTPException(409, "Request ID belongs to different input")
                return dict(row), False
            if not self.policy.get("enabled", True):
                raise HTTPException(503, "New analysis is paused")
            own = db.execute("SELECT COUNT(*) FROM tasks WHERE owner=? AND day=?", (owner, day)).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM tasks WHERE day=?", (day,)).fetchone()[0]
            if own >= self.policy["users"][owner]["daily_tasks"] or total >= self.policy["daily_tasks"]:
                raise HTTPException(429, "Daily task quota exceeded")
            running = db.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0]
            if running >= self.policy["concurrent_tasks"]:
                raise HTTPException(429, "Analysis capacity is busy")
            task_id = uuid4().hex
            db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,'running',NULL)",
                       (task_id, owner, request.request_id, request.question, day))
            return dict(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()), True

    def task(self, owner: str, task_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=? AND owner=?", (task_id, owner)).fetchone()
        if row is None:
            raise HTTPException(404, "Task not found")
        return dict(row)

    def finish(self, task_id: str, status: str, error_type=None):
        with self.connect() as db:
            db.execute("UPDATE tasks SET status=?, error_type=? WHERE id=?", (status, error_type, task_id))


def create_app(root: Path = ROOT, access_path: Path | None = None, runner=None) -> FastAPI:
    root = root.resolve()
    access_path = access_path or Path(os.environ.get("FINEVOLAB_ACCESS_CONFIG", str(root / "output/experience/access.json")))
    policy = json.loads(access_path.read_text(encoding="utf-8"))
    if not policy.get("users") or policy["daily_tasks"] < 1 or policy["concurrent_tasks"] < 1:
        raise ValueError("An invitation policy and positive quotas are required")
    for owner, user in policy["users"].items():
        if not owner or len(user["invite_sha256"]) != 64 or user["daily_tasks"] < 1:
            raise ValueError("Invalid invitation policy")
    budget = RunBudget(**policy["budget"])
    if min(budget.max_model_calls, budget.max_tool_calls, budget.max_total_tokens, budget.timeout_seconds) < 1:
        raise ValueError("Positive task budgets are required")
    access = AccessStore(root / "output/experience", policy)
    jobs = set()
    instance_lock = FileLock(str(root / "output/experience/server.lock"))

    @asynccontextmanager
    async def lifespan(app):
        with instance_lock.acquire(timeout=0):
            with access.connect() as db:
                db.execute("UPDATE tasks SET status='interrupted', error_type='ProcessRestart' WHERE status='running'")
            yield
            for task in jobs:
                task.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)

    app = FastAPI(title="FinEvoLab Experience", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.access = access

    @app.middleware("http")
    async def browser_boundary(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if request.headers.get("x-finevolab") != "1" or (origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc)):
                return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/experience/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/experience/api/login")
    async def login(body: LoginRequest, request: Request):
        owner, token = access.login(body.invite, request.client.host if request.client else "unknown")
        response = JSONResponse({"user": owner})
        response.set_cookie(COOKIE, token, httponly=True, secure=policy.get("secure_cookie", False),
                            samesite="strict", max_age=12 * 3600, path="/experience/api")
        return response

    @app.post("/experience/api/logout")
    async def logout(request: Request):
        with access.connect() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (digest(request.cookies.get(COOKIE, "")),))
        response = JSONResponse({"status": "signed_out"})
        response.delete_cookie(COOKIE, path="/experience/api")
        return response

    @app.get("/experience/api/session")
    async def session(request: Request):
        owner = access.owner(request)
        day = datetime.now(timezone.utc).date().isoformat()
        with access.connect() as db:
            used = db.execute("SELECT COUNT(*) FROM tasks WHERE owner=? AND day=?", (owner, day)).fetchone()[0]
        return {"user": owner, "used": used, "daily_tasks": policy["users"][owner]["daily_tasks"],
                "quota_timezone": "UTC", "budget": policy["budget"], "enabled": policy.get("enabled", True)}

    async def execute(row):
        try:
            if runner:
                state = await runner(row["id"], row["question"])
            else:
                settings = ModelConfig.load()
                candidate = read_json(root / "prompts/benchmark.json")
                state = await run_question(row["id"], row["question"], settings, candidate,
                                           root=root, budget=budget, timeout_seconds=budget.timeout_seconds)
            access.finish(row["id"], state["status"])
        except asyncio.CancelledError:
            access.finish(row["id"], "interrupted", "ProcessShutdown")
            raise
        except Exception as error:
            access.finish(row["id"], "failed", type(error).__name__)

    @app.post("/experience/api/tasks", status_code=202)
    async def create_task(body: AnalysisRequest, request: Request):
        owner = access.owner(request)
        if not body.question.strip():
            raise HTTPException(422, "A nonempty question is required")
        row, created = access.reserve(owner, body)
        if created:
            job = asyncio.create_task(execute(row))
            jobs.add(job)
            job.add_done_callback(jobs.discard)
        return {"task_id": row["id"], "status": row["status"], "reused": not created}

    @app.get("/experience/api/tasks")
    async def history(request: Request):
        owner = access.owner(request)
        with access.connect() as db:
            rows = db.execute("SELECT id AS task_id, question, status FROM tasks WHERE owner=? ORDER BY rowid DESC LIMIT 30", (owner,))
            return [dict(row) for row in rows]

    @app.get("/experience/api/tasks/{task_id}")
    async def task(task_id: str, request: Request):
        row = access.task(access.owner(request), task_id)
        path = root / "output/tasks" / row["id"] / "state.json"
        result = public_result(read_json(path)) if path.is_file() else {"task_id": task_id, "phases": {}}
        result.update(status=row["status"], question=row["question"], error_type=row["error_type"])
        result["reports"] = [kind for kind in ("html", "pdf")
            if row["status"] == "complete" and (path.parent / "report" / ("report." + kind)).is_file()]
        return result

    @app.get("/experience/api/tasks/{task_id}/report/{kind}")
    async def report(task_id: str, kind: str, request: Request):
        row = access.task(access.owner(request), task_id)
        if kind not in {"html", "pdf"} or row["status"] != "complete":
            raise HTTPException(404, "Report not found")
        path = root / "output/tasks" / row["id"] / "report" / ("report." + kind)
        if not path.is_file():
            raise HTTPException(404, "Report not found")
        return FileResponse(path, filename="finevolab-report." + kind,
                            media_type="application/pdf" if kind == "pdf" else "text/html")

    site = root / "output/showcase"

    @app.get("/experience.html", response_class=HTMLResponse)
    async def experience_page():
        return (site / "experience.html").read_text(encoding="utf-8").replace('data-live="false"', 'data-live="true"')

    @app.get("/{name:path}")
    async def static_file(name: str):
        allowed = {"index.html", "styles.css", "app.js", "data.js", "favicon.svg", "experience.js", "experience.css"}
        name = name or "index.html"
        if name not in allowed:
            raise HTTPException(404, "Page not found")
        return FileResponse(site / name)

    return app
