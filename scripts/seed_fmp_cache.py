"""Populate the FMP response cache from verified local snapshots without network access."""

import json
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "finrobot_equity/core/src"))
from modules.fmp_client import save_cache


def acquisition_time(record: dict, path: Path) -> str:
    """Recover the acquisition time from private metadata when it is omitted."""
    if record.get("created_at"):
        return record["created_at"]
    audit_path = ROOT / "project_notes/run_logs/file_cleanup_timestamp_cleanup.json"
    if audit_path.is_file():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
        value = audit.get("removed_metadata", {}).get(relative, {}).get("/created_at")
        if value:
            return value
    raise ValueError("The snapshot acquisition time is unavailable; fetch a fresh provider snapshot")


def main():
    import hashlib
    path = ROOT / "project_notes/run_logs/fmp_stable_access.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    observed_time = acquisition_time(record, path)
    count = 0
    for row in record["checks"]:
        source = ROOT / row["path"]
        if not row["passed"] or hashlib.sha256(source.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("An FMP snapshot is missing or changed")
        payload = json.loads(source.read_text(encoding="utf-8"))
        params = {"symbol": row["symbol"]}
        if row["endpoint"] not in {"profile", "quote"}:
            params.update(period="annual", limit=3)
        provenance = {"path": row["path"], "sha256": row["sha256"], "retrieved_at": observed_time}
        observed_at = datetime.fromisoformat(observed_time).timestamp()
        target = "https://financialmodelingprep.com/stable/" + row["endpoint"]
        save_cache(target, params, payload, source=provenance, observed_at=observed_at)
        count += 1
        if row["endpoint"] == "quote":
            save_cache("https://financialmodelingprep.com/stable/quote-short", params, payload, source=provenance, observed_at=observed_at)
            count += 1
        elif row["endpoint"] not in {"profile", "quote"}:
            save_cache(target, {**params, "limit": 1}, payload[:1], source=provenance, observed_at=observed_at)
            count += 1
    print(json.dumps({"status": "passed", "cache_entries": count, "network_requests": 0}))


if __name__ == "__main__":
    main()
