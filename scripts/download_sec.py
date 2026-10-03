"""Snapshot public SEC company facts without authentication."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import requests

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    companies = json.loads((ROOT / "configs/sec_companies.json").read_text())
    manifest = ROOT / "data/sec_sources.json"
    previous = json.loads(manifest.read_text()) if manifest.exists() else []
    expected = {item["path"]: item["sha256"] for item in previous}
    sources = []
    for item in companies:
        path = ROOT / "data/raw" / (item["company"].lower() + "_companyfacts.json")
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{item['cik']}.json"
        if not path.exists():
            response = requests.get(url, timeout=(10, 60),
                                    headers={"User-Agent": "FinEvoLab/0.1 public-filings-research"})
            response.raise_for_status()
            document = response.json()
            if int(document["cik"]) != int(item["cik"]):
                raise ValueError("SEC entity does not match the requested CIK")
            path.write_bytes(response.content)
            time.sleep(0.25)
        raw = path.read_bytes()
        document = json.loads(raw)
        relative = path.relative_to(ROOT).as_posix()
        if relative in expected and hashlib.sha256(raw).hexdigest() != expected[relative]:
            raise ValueError("SEC snapshot differs from the pinned manifest")
        if int(document["cik"]) != int(item["cik"]):
            raise ValueError("SEC entity does not match the requested CIK")
        sources.append({**item, "url": url, "path": path.relative_to(ROOT).as_posix(),
                        "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                        "entity_name": document["entityName"],
                        "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                        "fiscal_years": [2022, 2023, 2024]})
        print(f"Verified SEC snapshot: {item['company']} ({len(raw)} bytes)")
    manifest.write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
