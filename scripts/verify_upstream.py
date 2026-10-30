"""Check the local Equity V1 application without external API calls."""

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "finrobot_equity/core/src"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--base-url")
    args = parser.parse_args()
    os.environ.setdefault("FINROBOT_ADMIN_PASSWORD", secrets.token_urlsafe(24))
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("MPLBACKEND", "Agg")
    database_path = ROOT / "finrobot_equity/web_app/data/baseline_smoke.db"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    from agents import ModelSettings, RunConfig
    from fastapi.testclient import TestClient
    from sqlalchemy import inspect
    from finrobot_equity.core.src.modules.equity_agents.tagline_agent import (
        TaglineResponse,
        tagline_agent,
    )

    result = {
        "run_id": "m1-local-baseline",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "python": sys.version.split()[0],
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("openai", "openai-agents", "pydantic", "fastapi", "reportlab")
        },
        "checks": {},
    }
    result["working_diff_sha256"] = hashlib.sha256(
        subprocess.check_output(["git", "diff", "HEAD"], cwd=ROOT)
    ).hexdigest()
    result["requirements_sha256"] = hashlib.sha256(
        (ROOT / "finrobot_equity/requirements.txt").read_bytes()
    ).hexdigest()
    result["source_files_sha256"] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in (
            "scripts/verify_upstream.py",
            "tests/test_upstream_compatibility.py",
            "finrobot_equity/core/src/modules/equity_agents/agent_manager.py",
            "finrobot_equity/core/tests/test_generate_report.py",
        )
    }
    checks = result["checks"]
    with contextlib.redirect_stdout(io.StringIO()):
        from finrobot_equity.web_app.main import app
        from finrobot_equity.web_app.database.connection import engine

    checks["database_tables"] = inspect(engine).get_table_names()
    with TestClient(app) as client:
        for path in ("/login", "/static/style.css", "/output/MSFT_Equity_Research_Report.html"):
            response = client.get(path)
            checks[path] = {"status": response.status_code, "bytes": len(response.content)}
            response.raise_for_status()
    checks["structured_schema"] = (
        tagline_agent.output_type is TaglineResponse
        and "tagline" in TaglineResponse.model_json_schema()["properties"]
    )
    checks["run_config_model"] = RunConfig(model="local-schema-check").model
    try:
        settings = ModelSettings(model="local-schema-check")
    except TypeError:
        checks["upstream_model_settings_model_supported"] = False
    else:
        checks["upstream_model_settings_model_supported"] = (
            getattr(settings, "model", None) == "local-schema-check"
        )
    for name in ("LICENSE", "NOTICE", "TRADEMARK_POLICY.md"):
        checks[name] = hashlib.sha256((ROOT / "licenses/finrobot" / name).read_bytes()).hexdigest()
    if args.base_url:
        checks["live_http"] = {}
        for path in ("/login", "/static/style.css"):
            with urlopen(args.base_url.rstrip("/") + path, timeout=10) as response:
                checks["live_http"][path] = response.status
    reports = ROOT / "finrobot_equity/core/tests/test_output"
    checks["sample_pdf_files"] = [
        {"name": path.name, "bytes": path.stat().st_size,
         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(reports.glob("*.pdf"))
    ]
    serialized = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
