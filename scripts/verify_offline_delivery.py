"""Audit uploaded disclosures and render a deterministic comparison report."""

from uuid import uuid4
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.evaluation import reference_answer
from finevolab.experiments import manifest, sha256
from finevolab.reporting import build_analysis_report
from finevolab.runtime import write_json
from finevolab.tools import FinancialStore
from finevolab.uploads import ingest_upload
from finevolab.validation import validate_answer


def main():
    run_id = uuid4().hex[:12] + "-offline-delivery"
    directory = ROOT / "project_notes/experiments" / run_id
    write_json(directory / "manifest.json", manifest(ROOT, run_id, {"model_calls": 0, "dataset": "v2",
        "scope": "uploaded_source_extraction_reference_validation_and_report_rendering"}))
    rows = []
    sources = json.loads((ROOT / "data/filing_sources.json").read_text()) + json.loads((ROOT / "data/sources.json").read_text())
    temporary_root = ROOT / "output/cache"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temporary_root, prefix="upload_audit_") as temporary:
        for source in sources:
            started = time.monotonic()
            record = {"company": source["company"], "fiscal_year": source["fiscal_year"], "source_sha256": source["sha256"]}
            try:
                raw = (ROOT / source["path"]).read_bytes()
                result = ingest_upload(raw, ".html", source["company"], source["fiscal_year"], Path(temporary))["metric_results"]
                record.update(status="extracted" if result["observations"] else "no_observations", extraction=result)
            except Exception as error:
                record.update(status="failed", error_type=type(error).__name__, message=str(error)[:200])
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            rows.append(record)
            write_json(directory / "upload_audit.json", {"reports": rows})
            print(json.dumps({key: value for key, value in record.items() if key != "extraction"}), flush=True)
    store = FinancialStore(ROOT / "data/processed/reports.json")
    checked = []
    for split in ("development", "validation", "test"):
        for case in json.loads((ROOT / f"data/evaluation/v2/{split}.json").read_text())["tasks"]:
            result = validate_answer(store, {"answer": reference_answer(case)})
            checked.append({"task_id": case["id"], "split": split, **result})
    comparison = store.compare(["MSFT", "AAPL"], "revenue", 2024)
    answer = {"status": "complete", "answer":
              "MSFT and AAPL FY2024 revenue observations retain their actual fiscal dates. Their fiscal periods differ; each amount remains in its disclosed USD unit.",
              "metrics": [item.model_dump(mode="json") for item in comparison.values], "calculations": [],
              "periods_aligned": comparison.periods_aligned, "evidence_ids": [item.evidence_id for item in comparison.values]}
    artifacts = build_analysis_report(store, "Compare MSFT and AAPL FY2024 revenue, preserving actual periods and units.",
                                      {"answer": answer}, ROOT / "output/reports/company_comparison")
    summary = {"run_id": run_id, "status": "complete", "model_calls": 0, "reports_audited": len(rows),
               "reports_with_observations": sum(row["status"] == "extracted" for row in rows),
               "upload_failures": sum(row["status"] == "failed" for row in rows),
               "snapshot_matched_observations": sum(row.get("extraction", {}).get("counts", {}).get("matched_snapshot", 0) for row in rows),
               "conflicting_observations": sum(row.get("extraction", {}).get("counts", {}).get("conflict", 0) for row in rows),
               "reference_answers_validated": len(checked), "data_version": store.version,
               "artifacts": {name: {"path": path, "sha256": sha256(Path(path))} for name, path in artifacts.items()}}
    write_json(directory / "reference_validation.json", {"checks": checked})
    write_json(directory / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
