"""Attach financial analysis routes to the upstream web application."""

import contextlib
import io

with contextlib.redirect_stdout(io.StringIO()):
    from finrobot_equity.web_app.main import app

from pathlib import Path

from finevolab.tracing import TraceMiddleware
from finevolab.web import router

app.include_router(router)

app.add_middleware(TraceMiddleware, directory=Path(__file__).resolve().parents[1] / "output/traces")
