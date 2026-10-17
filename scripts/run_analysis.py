"""Execute a source-checked annual revenue analysis."""

from uuid import uuid4
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.agent import analyze_revenue
from finevolab.config import ModelConfig
from finevolab.tools import FinancialStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", default="MSFT")
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args()
    settings = ModelConfig.load(ROOT / "finrobot_equity/core/config/config.ini")
    record = {
        "run_id": uuid4().hex[:12] + "-analysis",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "model": settings.model, "base_url": settings.base_url,
        "sources_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in (ROOT / "finevolab").glob("*.py")},
    }
    started = time.monotonic()
    events = []
    try:
        store = FinancialStore(ROOT / "data/processed/reports.json")
        record["result"] = asyncio.run(analyze_revenue(settings, store, args.company, args.year, events))
        record["status"] = "passed"
    except Exception as error:
        record.update(status="failed", error_type=type(error).__name__,
                      status_code=getattr(error, "status_code", None))
        if type(error).__name__ == "ModelBehaviorError":
            record["schema_error"] = str(error).replace(settings.api_key, "[REDACTED]")[:1600]
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    record["tool_events"] = events
    output = ROOT / "project_notes/run_logs/financial_analysis.json"
    records = json.loads(output.read_text(encoding="utf-8")) if output.exists() else []
    records.append(record)
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, ensure_ascii=True, indent=2))
    if record["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
