"""Normalize configured local report snapshots."""

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.ingestion import extract_report
from finevolab.sec_ingestion import extract_sec


def main() -> None:
    sources = json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8"))
    reports = [extract_report(source, ROOT) for source in sources]
    if (ROOT / "data/sec_sources.json").exists():
        sec_sources = json.loads((ROOT / "data/sec_sources.json").read_text())
        reports.extend(extract_sec(source, ROOT) for source in sec_sources)
    data = {
        "version": hashlib.sha256(json.dumps({
            "sources": [{k: v for k, v in report["source"].items() if k != "retrieved_at"} for report in reports],
            "parsers": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                        for name in ("finevolab/ingestion.py", "finevolab/sec_ingestion.py", "scripts/prepare_reports.py")},
        }, sort_keys=True).encode()).hexdigest(),
        "selection_rule": "latest_report_year_in_snapshot",
        "reports": reports,
    }
    output = ROOT / "data/processed/reports.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reports": len(reports), "metrics": sum(len(r["metrics"]) for r in reports),
                      "data_version": data["version"]}, indent=2))


if __name__ == "__main__":
    main()
