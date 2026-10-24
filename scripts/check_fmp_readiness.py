"""Check FMP configuration and optionally probe the fixed upstream endpoints."""

import argparse
import configparser
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.runtime import write_json


def load_key(path: Path, environment=None) -> str | None:
    environment = os.environ if environment is None else environment
    value = environment.get("FMP_API_KEY") or environment.get("FINANCIAL_MODELING_PREP_API_KEY")
    if not value and path.exists():
        config = configparser.ConfigParser(interpolation=None)
        config.read(path, encoding="utf-8")
        value = config.get("API_KEYS", "fmp_api_key", fallback="")
    value = (value or "").strip()
    if len(value) < 16 or value.upper().startswith(("YOUR_", "PLACEHOLDER", "EXAMPLE_")):
        return None
    return value


def readiness(path: Path, probe=False, environment=None, api_version="legacy", symbol="MSFT") -> dict:
    if api_version not in {"legacy", "stable"}:
        raise ValueError("FMP API version must be legacy or stable")
    key = load_key(path, environment)
    result = {"run_id": "fmp-readiness", "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": "Financial Modeling Prep", "credential_kind": "financial_data_api_key",
        "api_version": api_version,
        "configured": bool(key), "requests_sent": 0,
        "status": "ready_for_endpoint_probe" if key else "waiting_for_fmp_key",
        "legacy_endpoint_access": "not_verified", "checks": []}
    if not key or not probe:
        return result
    logger = logging.getLogger("httpx")
    previous_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            for endpoint in ("income-statement", "balance-sheet-statement", "cash-flow-statement", "profile", "quote"):
                route = f"/api/v3/{endpoint}/{symbol}" if api_version == "legacy" else f"/stable/{endpoint}"
                check = {"endpoint": route, "symbol": symbol}
                try:
                    result["requests_sent"] += 1
                    params = {"apikey": key, "limit": 3}
                    if api_version == "stable":
                        params["symbol"] = symbol
                    response = client.get("https://financialmodelingprep.com" + check["endpoint"], params=params)
                    payload = response.json()
                    check.update(status_code=response.status_code,
                        passed=response.is_success and isinstance(payload, list) and bool(payload))
                except Exception as error:
                    check.update(passed=False, error_type=type(error).__name__)
                result["checks"].append(check)
    finally:
        logger.setLevel(previous_level)
    passed = all(check["passed"] for check in result["checks"])
    result.update(status="endpoint_probe_passed" if passed else "endpoint_probe_failed",
                  **{f"{api_version}_endpoint_access": "verified" if passed else "not_verified"})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "finrobot_equity/core/config/config.ini")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--api-version", choices=["legacy", "stable"], default="legacy")
    parser.add_argument("--output", type=Path, default=ROOT / "project_notes/run_logs/fmp_readiness.json")
    args = parser.parse_args()
    result = readiness(args.config, args.probe, api_version=args.api_version)
    write_json(args.output, result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
