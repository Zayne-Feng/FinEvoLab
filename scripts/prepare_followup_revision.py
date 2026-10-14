"""Freeze source-isolated annual-report development and acceptance tasks."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.grounded import RequestPlan, assemble_numeric
from finevolab.runtime import write_json
from finevolab.tools import FinancialStore


def main():
    source, target = ROOT / "data/evaluation/followup_v1", ROOT / "data/evaluation/followup_v2"
    if target.exists():
        raise ValueError("The follow-up revision is already frozen")
    store = FinancialStore(ROOT / "data/processed/reports.json")
    filings = json.loads((ROOT / "data/filing_sources.json").read_text(encoding="utf-8"))
    documents = {d["id"]: d for d in json.loads((source / "documents.json").read_text(encoding="utf-8"))}
    specifications = [(["MSFT"], 2023, "operating_income", "explanation"),
        (["AAPL"], 2023, "revenue", "hybrid_growth"), (["AMZN"], 2023, "net_income", "hybrid_metric"),
        (["META"], 2023, "operating_income", "explanation"), (["GOOGL"], 2024, "net_income", "hybrid_metric"),
        (["AMZN", "GOOGL"], 2024, "revenue", "hybrid_comparison"),
        (["AAPL", "META"], 2023, "revenue", "hybrid_comparison"), (["MSFT"], 2023, "revenue", "explanation")]
    cases = []
    for companies, year, metric, operation in specifications:
        selected = [next(d for d in filings if d["company"] == company and d["fiscal_year"] == year) for company in companies]
        ids = [d["sha256"] for d in selected]
        for d in selected:
            documents[d["sha256"]] = {"id": d["sha256"], "company": d["company"], "report_year": year,
                "path": d["path"], "sha256": d["sha256"], "url": d["url"]}
        names, label = " and ".join(companies), metric.replace("_", " ")
        discussion = f"Explain the FY{year} {label} change and disclosed drivers for each of {names}, with source passages from the selected reports."
        intent = "comparison" if len(companies) > 1 else "growth" if operation == "hybrid_growth" else "metric"
        prefix = f"Compare annual revenue amounts for {names} FY{year} and retain their actual fiscal periods. " if len(companies) > 1 else (
            f"Calculate FY{year} annual revenue growth for {names}. " if intent == "growth" else
            f"Give the FY{year} {label} amount for {names}, with units and fiscal dates. ")
        reference = None if operation == "explanation" else assemble_numeric(RequestPlan(intent=intent,
            companies=companies, fiscal_year=year, metrics=[metric]), store, [])
        if reference:
            for m in reference["metrics"] + [m for calc in reference["calculations"] for m in calc["inputs"]]:
                m["report_year"] = year
        identity = hashlib.sha256(json.dumps([companies, year, metric, operation, "followup-v2"]).encode()).hexdigest()[:20]
        cases.append({"id": identity, "split": "test", "category": "upload", "company": companies[0],
            "companies": companies, "fiscal_year": year, "report_year": year, "operation": operation,
            "metrics": [metric], "topic": "revenue" if metric == "revenue" else "profit", "focus": metric,
            "question": discussion if operation == "explanation" else prefix + discussion, "upload_ids": ids,
            "reference_answer": reference, "common_support": False,
            "fact_clusters": [f"{company}:{year}" for company in companies]})
    development = deepcopy(json.loads((source / "development.json").read_text(encoding="utf-8"))["tasks"] +
        json.loads((source / "test.json").read_text(encoding="utf-8"))["tasks"])
    for case in development:
        case["split"] = "development"
    target.mkdir(parents=True)
    write_json(target / "development.json", {"data_version": store.version, "tasks": development})
    write_json(target / "test.json", {"data_version": store.version, "tasks": cases})
    write_json(target / "documents.json", list(documents.values()))
    write_json(target / "manifest.json", {"version": "followup-v2", "data_version": store.version,
        "files": {p.name: sha256(p) for p in target.glob("*.json")},
        "sources": {d["path"]: sha256(ROOT / d["path"]) for d in documents.values()},
        "reference_policy": "Canonical financial values and fiscal dates; report_year fixed to selected disclosure",
        "earlier_test": "followup-v1 test is development after invalidated cache isolation; not independent acceptance"})
    print(json.dumps({"development": len(development), "test": len(cases)}))


if __name__ == "__main__":
    main()
