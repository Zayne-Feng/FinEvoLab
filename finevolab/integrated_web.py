"""Attach financial analysis routes to the upstream web application."""

import contextlib
import io

with contextlib.redirect_stdout(io.StringIO()):
    from finrobot_equity.web_app.main import app

from finevolab.web import router

app.include_router(router)
