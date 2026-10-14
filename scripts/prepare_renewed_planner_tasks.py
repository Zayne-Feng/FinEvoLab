"""Freeze mixed-source planner preservation tasks with independent metric references."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.runtime import write_json


def main():
    previous, target = ROOT / "data/evaluation/renewed_v2", ROOT / "data/evaluation/renewed_v3"
    if target.exists():
        raise ValueError("The planner acceptance set is already frozen")
    old = json.loads((previous / "development.json").read_text(encoding="utf-8"))
    development = old["tasks"] + json.loads((previous / "test.json").read_text(encoding="utf-8"))["tasks"]
    for case in development:
        case["split"] = "development"
    sources = json.loads((ROOT / "data/evaluation/scope_v3/documents.json").read_text(encoding="utf-8"))
    selected = {company: next(document for document in sources if document["company"] == company
                and document["report_year"] == year) for company, year in (("TSLA", 2023), ("NVDA", 2024))}
    scoped = json.loads((ROOT / "data/evaluation/scope_v3/development.json").read_text(encoding="utf-8"))["tasks"]
    reference = deepcopy(next(case["reference_answer"] for case in scoped if case["id"] == "f1ab61ed00b16b6c84a1"))
    specs = [
        ("META", 2024, "revenue", "risk", [], None,
         "Which risks in META's FY2024 annual report could reduce revenue or sales? Explain their disclosed effects with original quotations."),
        ("MSFT", 2023, "revenue", "risk", [], None,
         "请根据 MSFT FY2023 年报，说明可能影响收入的主要风险，引用原文解释这些风险与收入的关系。"),
        ("TSLA", 2023, "revenue", "risk", [selected["TSLA"]["id"]], None,
         "Which risks identified in the selected TSLA FY2023 annual report could adversely affect its revenue? Explain with original passages."),
        ("NVDA", 2024, "net_income", "hybrid_metric", [selected["NVDA"]["id"]], reference,
         "Give NVDA FY2024 annual net income in USD with actual fiscal dates, using only the selected annual report. Explain its change and the disclosed operating or tax drivers, with original quotations."),
    ]
    tasks = []
    for company, year, metric, operation, uploads, answer, question in specs:
        identity = hashlib.sha256(json.dumps([company, year, metric, operation, "renewed-v3"]).encode()).hexdigest()[:20]
        tasks.append({"id": identity, "split": "test", "category": "explanation" if operation == "risk" else operation,
            "company": company, "companies": [company], "fiscal_year": year, "report_year": year,
            "operation": "explanation" if operation == "risk" else operation,
            "topic": "risk" if operation == "risk" else "profit", "focus": metric,
            "metrics": [metric], "question": question, "upload_ids": uploads, "reference_answer": answer,
            "common_support": True, "fact_clusters": [f"{company}:{year}"]})
    target.mkdir(parents=True)
    write_json(target / "development.json", {"data_version": old["data_version"], "tasks": development})
    write_json(target / "test.json", {"data_version": old["data_version"], "tasks": tasks})
    write_json(target / "documents.json", list(selected.values()))
    frozen = json.loads((previous / "manifest.json").read_text(encoding="utf-8"))
    write_json(target / "manifest.json", {"version": "renewed-v3", "data_version": old["data_version"],
        "files": {path.name: sha256(path) for path in target.glob("*.json")},
        "sources": {**frozen["sources"], **{document["path"]: sha256(ROOT / document["path"]) for document in selected.values()}},
        "prior_acceptance": "renewed-v2 test is development; preserve its original scores",
        "reference_commit": "9437bcb9", "retrieval_reference_commit": "4d870388",
        "scope": "new company/operation combinations in this round; upload sources previously used in the project"})
    print(json.dumps({"development": len(development), "test": len(tasks), "selected_documents": len(selected)}))


if __name__ == "__main__":
    main()
