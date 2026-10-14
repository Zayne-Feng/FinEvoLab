"""Freeze shared report tasks and source-isolated upload acceptance cases."""

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
    target = ROOT / "data/evaluation/followup_v1"
    if target.exists():
        raise ValueError("The follow-up dataset is already frozen")
    store = FinancialStore(ROOT / "data/processed/reports.json")
    common = [{"id": f"{company.lower()}-{year}", "company": company, "fiscal_year": year,
        "question": f"Give {company} FY{year} and FY{year - 1} revenue in USD and the annual revenue change, rounded to one decimal percentage point."}
        for company in ("MSFT", "AAPL", "GOOGL", "AMZN", "META") for year in (2023, 2024)]
    filings = json.loads((ROOT / "data/filing_sources.json").read_text(encoding="utf-8"))
    specifications = [
        (["MSFT"], 2024, "revenue", "hybrid_metric"),
        (["AAPL"], 2024, "net_income", "hybrid_metric"),
        (["AMZN"], 2024, "operating_income", "explanation"),
        (["META"], 2024, "net_income", "explanation"),
        (["GOOGL"], 2023, "revenue", "hybrid_growth"),
        (["MSFT", "AAPL"], 2024, "revenue", "hybrid_comparison"),
        (["AMZN", "META"], 2024, "revenue", "hybrid_comparison"),
        (["GOOGL"], 2024, "revenue", "explanation"),
    ]
    cases, documents = [], {}
    for companies, year, metric, operation in specifications:
        selected = [next(row for row in filings if row["company"] == company and row["fiscal_year"] == year) for company in companies]
        ids = [row["sha256"] for row in selected]
        for row in selected:
            documents[row["sha256"]] = {"id": row["sha256"], "company": row["company"],
                "report_year": year, "path": row["path"], "sha256": row["sha256"], "url": row["url"]}
        label = metric.replace("_", " ")
        names = " and ".join(companies)
        explanation = f"Describe the FY{year} {label} change for EACH of {names} and the disclosed drivers, citing exact passages from only the selected reports."
        numeric_intent = "growth" if operation == "hybrid_growth" else "comparison" if len(companies) > 1 else "metric"
        question = explanation if operation == "explanation" else (
            f"Using the selected reports, {'calculate annual revenue growth' if numeric_intent == 'growth' else 'give ' + label} "
            f"for {names} FY{year}, preserving actual units and fiscal dates. " + explanation)
        identity = hashlib.sha256(json.dumps([companies, year, metric, operation, ids]).encode()).hexdigest()[:20]
        reference = None
        if operation != "explanation":
            reference = assemble_numeric(RequestPlan(intent=numeric_intent, companies=companies, fiscal_year=year,
                metrics=[metric]), store, [])
        cases.append({"id": identity, "split": "test", "category": "upload", "company": companies[0],
            "companies": companies, "fiscal_year": year, "report_year": year, "operation": operation,
            "metrics": [metric], "topic": "revenue" if metric == "revenue" else "profit", "focus": metric,
            "question": question, "upload_ids": ids, "reference_answer": reference, "common_support": False,
            "fact_clusters": [f"{company}:{year}" for company in companies]})
    historical = json.loads((ROOT / "data/evaluation/scope_v3/test.json").read_text(encoding="utf-8"))["tasks"]
    development = [deepcopy(row) for row in historical if row["id"] in {
        "7fe7ffde6d2e95bd178f", "af619504d81f55690b09", "263b1d9e29c2c70f6bc7"}
        or (row["company"] == "ADBE" and row["fiscal_year"] == 2023 and row.get("focus") == "revenue" and row["category"] == "upload")]
    existing_docs = json.loads((ROOT / "data/evaluation/scope_v3/documents.json").read_text(encoding="utf-8"))
    for row in existing_docs:
        if any(row["id"] in case["upload_ids"] for case in development):
            documents[row["id"]] = row
    target.mkdir(parents=True)
    write_json(target / "common.json", {"data_version": store.version, "tasks": common})
    write_json(target / "test.json", {"data_version": store.version, "tasks": cases})
    write_json(target / "development.json", {"data_version": store.version, "tasks": development})
    write_json(target / "documents.json", list(documents.values()))
    write_json(target / "manifest.json", {"version": "followup-v1", "reference_commit": "944a59ff",
        "upstream_commit": "2717499b8e30f242640af08c4ad9afd1113c2d45", "data_version": store.version,
        "files": {path.name: sha256(path) for path in target.glob("*.json")},
        "sources": {row["path"]: sha256(ROOT / row["path"]) for row in documents.values()}})
    print(json.dumps({"common": len(common), "development": len(development), "test": len(cases)}))


if __name__ == "__main__":
    main()
