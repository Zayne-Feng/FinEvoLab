"""Freeze annual net-income evidence cases and selected-source references."""

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
    target = ROOT / "data/evaluation/followup_v3"
    if target.exists():
        raise ValueError("The table acceptance dataset is already frozen")
    previous = ROOT / "data/evaluation/followup_v2"
    development = deepcopy(next(c for c in json.loads((previous / "test.json").read_text(encoding="utf-8"))["tasks"]
        if c["company"] == "AMZN" and c["fiscal_year"] == 2023))
    development["split"] = "development"
    documents = {d["id"]: d for d in json.loads((previous / "documents.json").read_text(encoding="utf-8"))}
    filings = json.loads((ROOT / "data/filing_sources.json").read_text(encoding="utf-8"))
    store = FinancialStore(ROOT / "data/processed/reports.json")
    cases = []
    for company, year in (("AMZN", 2024), ("MSFT", 2023), ("GOOGL", 2023)):
        filing = next(d for d in filings if d["company"] == company and d["fiscal_year"] == year)
        identifier = filing["sha256"]
        documents[identifier] = {"id": identifier, "company": company, "report_year": year,
            "path": filing["path"], "sha256": identifier, "url": filing["url"]}
        reference = assemble_numeric(RequestPlan(intent="metric", companies=[company], fiscal_year=year,
            metrics=["net_income"]), store, [])
        reference["metrics"][0]["report_year"] = year
        cases.append({"id": hashlib.sha256(f"{company}:{year}:net-income:followup-v3".encode()).hexdigest()[:20],
            "split": "test", "category": "upload", "company": company, "companies": [company],
            "fiscal_year": year, "report_year": year, "operation": "hybrid_metric", "metrics": ["net_income"],
            "topic": "profit", "focus": "net_income", "upload_ids": [identifier], "common_support": True,
            "reference_answer": reference, "fact_clusters": [f"{company}:{year}"],
            "question": f"Using only the selected {company} FY{year} annual report, give the annual net income amount "
                "with currency and fiscal dates. Explain whether net income increased or decreased from the preceding "
                "fiscal year and identify one related disclosed profit or tax factor, with exact source passages."})
    target.mkdir(parents=True)
    write_json(target / "development.json", {"data_version": store.version, "tasks": [development]})
    write_json(target / "test.json", {"data_version": store.version, "tasks": cases})
    write_json(target / "documents.json", list(documents.values()))
    write_json(target / "manifest.json", {"version": "followup-v3", "data_version": store.version,
        "files": {p.name: sha256(p) for p in target.glob("*.json")},
        "sources": {d["path"]: sha256(ROOT / d["path"]) for d in documents.values()},
        "comparison": "260a7da7 versus parenthetical-label and annual-period table ranking; three new probes, three repeats",
        "independence": "New target combinations within existing companies and financial snapshots"})
    print(json.dumps({"development": 1, "test": 3}))


if __name__ == "__main__":
    main()
