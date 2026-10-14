"""Freeze revised financial tasks after retiring an observed acceptance split."""

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
    previous, target = ROOT / "data/evaluation/renewed_v1", ROOT / "data/evaluation/renewed_v2"
    if target.exists():
        raise ValueError("The revised task set is already frozen")
    store = FinancialStore(ROOT / "data/processed/reports.json")
    development = []
    for split in ("development", "validation", "acceptance"):
        for case in json.loads((previous / f"{split}.json").read_text(encoding="utf-8"))["tasks"]:
            case = deepcopy(case)
            case["split"] = "development"
            development.append(case)
    specs = [("MSFT", 2024, "net_income", "hybrid_metric"),
        ("AAPL", 2023, "net_income", "explanation"),
        ("GOOGL", 2023, "operating_income", "hybrid_metric"),
        ("AMZN", 2024, "net_income", "hybrid_growth"),
        ("META", 2024, "operating_income", "explanation"),
        ("AAPL", 2023, "revenue", "risk"),
        ("AAPL", 2024, "net_income", "hybrid_growth"),
        ("GOOGL", 2023, "revenue", "risk")]
    tasks = []
    for company, year, metric, operation in specs:
        label = metric.replace("_", " ")
        topic = "risk" if operation == "risk" else "profit"
        intent = "explanation" if operation in {"explanation", "risk"} else "hybrid"
        numeric = "growth" if operation == "hybrid_growth" else "metric"
        question = (f"Using the {company} FY{year} annual report, explain the annual {label} change "
            "and the disclosed operating or tax drivers, with original source passages.")
        if operation == "risk":
            question = (f"Which risks disclosed in {company}'s FY{year} annual report could affect its "
                        "revenue or sales? Explain the disclosed connections and cite original passages.")
        elif intent == "hybrid":
            prefix = f"Calculate annual {label} percentage growth" if numeric == "growth" else f"Give annual {label} in USD with actual fiscal dates"
            question = f"{prefix} for {company} FY{year}. " + question
        identity = hashlib.sha256(json.dumps([company, year, metric, operation, "renewed-v2"]).encode()).hexdigest()[:20]
        plan = RequestPlan(intent=intent, numeric_intent=numeric, companies=[company], fiscal_year=year,
                           report_year=year, metrics=[metric], topic=topic)
        reference = None if intent == "explanation" else assemble_numeric(
            plan.model_copy(update={"intent": numeric}), store, [])
        tasks.append({"id": identity, "split": "test", "category": "explanation" if intent == "explanation" else operation,
            "company": company, "companies": [company], "fiscal_year": year, "report_year": year,
            "operation": "explanation" if intent == "explanation" else operation, "topic": topic,
            "focus": metric, "metrics": [metric], "question": question, "upload_ids": [],
            "reference_answer": reference, "common_support": True, "fact_clusters": [f"{company}:{year}"]})
    target.mkdir(parents=True)
    write_json(target / "development.json", {"data_version": store.version, "tasks": development})
    write_json(target / "test.json", {"data_version": store.version, "tasks": tasks})
    write_json(target / "documents.json", [])
    old_manifest = json.loads((previous / "manifest.json").read_text(encoding="utf-8"))
    write_json(target / "manifest.json", {"version": "renewed-v2", "data_version": store.version,
        "files": {path.name: sha256(path) for path in target.glob("*.json")},
        "sources": old_manifest["sources"], "reference_commit": "4d870388",
        "prior_acceptance": "renewed-v1 acceptance is now development; its results remain unchanged",
        "scope": "new metric/operation combinations over known public snapshots; no new-company generalization"})
    print(json.dumps({"development": len(development), "test": len(tasks)}))


if __name__ == "__main__":
    main()
