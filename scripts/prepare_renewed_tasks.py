"""Freeze financial requests and field-level planning contracts."""

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
    target = ROOT / "data/evaluation/renewed_v1"
    if target.exists():
        raise ValueError("The dataset is already frozen")
    store = FinancialStore(ROOT / "data/processed/reports.json")
    specifications = {
        "development": [
            ("MSFT", 2024, "revenue", "driving"), ("MSFT", 2024, "revenue", "how_much"),
            ("MSFT", 2024, "revenue", "risks"), ("AAPL", 2023, "revenue", "negation"),
            ("AMZN", 2023, "net_income", "amount_reason"), ("META", 2024, "revenue", "amount_reason"),
            ("AAPL", 2023, "operating_income", "change_reason"), ("GOOGL", 2024, "net_income", "change_reason"),
            ("MSFT,AAPL", 2024, "revenue", "comparison"), ("AMZN,META", 2023, "revenue", "alignment"),
            ("META", 2023, "net_income", "value"), ("AMZN", 2024, "revenue", "growth"),
            ("GOOGL", 2023, "revenue", "cn_reason"), ("AAPL", 2024, "revenue", "cn_growth"),
            ("MSFT", 2023, "revenue", "quarterly"), ("META", 2024, "revenue", "summary"),
        ],
        "validation": [
            ("AMZN", 2024, "revenue", "driving"), ("META", 2023, "revenue", "how_much"),
            ("GOOGL", 2024, "revenue", "risks"), ("MSFT", 2023, "revenue", "negation"),
            ("AAPL", 2024, "net_income", "amount_reason"), ("GOOGL,AMZN", 2024, "revenue", "comparison"),
            ("AMZN", 2024, "operating_income", "value"), ("META", 2023, "revenue", "cn_reason"),
        ],
        "acceptance": [
            ("AAPL", 2024, "revenue", "driving"), ("GOOGL", 2023, "revenue", "how_much"),
            ("AMZN", 2023, "revenue", "risks"), ("META", 2024, "revenue", "negation"),
            ("MSFT", 2023, "net_income", "amount_reason"), ("GOOGL", 2024, "revenue", "amount_reason"),
            ("META", 2023, "net_income", "change_reason"), ("MSFT", 2023, "revenue", "cn_growth"),
            ("AMZN,AAPL", 2024, "revenue", "comparison"), ("MSFT,GOOGL", 2023, "revenue", "alignment"),
            ("GOOGL", 2023, "operating_income", "value"), ("AAPL", 2024, "revenue", "quarterly"),
        ],
    }
    seen, tasks = set(), {}
    for split, specs in specifications.items():
        tasks[split] = []
        for companies_text, year, metric, template in specs:
            companies = companies_text.split(",")
            name, label = " and ".join(companies), metric.replace("_", " ")
            topic = "revenue" if metric == "revenue" else "profit"
            intent, numeric, scope = "explanation", "metric", "annual_fiscal"
            questions = {
                "driving": f"What is driving {name} {label} growth in FY{year}, and why? Use its FY{year} annual report and exact quotations.",
                "how_much": f"How much did {name} {label} grow in FY{year} as an annual percentage change, and why? Use exact FY{year} report quotations for disclosed drivers.",
                "risks": f"List the risks and explain how they could affect {name} revenue. Use only its FY{year} annual report and exact quotations.",
                "negation": f"Give {name} FY{year} {label} in USD. Do not calculate growth; explain the disclosed revenue drivers using exact FY{year} report quotations.",
                "amount_reason": f"Provide {name} FY{year} annual {label} in USD with fiscal dates, and explain the change and disclosed drivers using exact FY{year} report quotations.",
                "change_reason": f"Describe why {name} {label} changed in FY{year}. Use exact FY{year} annual report quotations and state its change direction.",
                "comparison": f"Compare {name} FY{year} annual {label} in USD and give the actual fiscal dates, including whether those dates differ.",
                "alignment": f"Are {name} FY{year} fiscal date ranges strictly aligned? No amount comparison is requested.",
                "value": f"What was {name} FY{year} annual {label} in USD? Include the source and fiscal dates.",
                "growth": f"Calculate {name} FY{year} annual {label} percentage growth using the immediately preceding fiscal year, with sources.",
                "cn_reason": f"解释 {name} FY{year} 收入变化的原因，并引用 FY{year} 年报原文，不需要计算增长率。",
                "cn_growth": f"给出 {name} FY{year} 收入同比增幅，并解释原因，引用 FY{year} 年报原文。",
                "quarterly": f"Give {name} Q2 {year} quarterly revenue in USD.",
                "summary": f"Summarize {name} FY{year} annual revenue, operating income and net income in USD with fiscal dates and citations.",
            }
            if template in {"how_much", "cn_growth"}:
                intent, numeric = "hybrid", "growth"
            elif template in {"negation", "amount_reason"}:
                intent = "hybrid"
            elif template in {"comparison", "alignment", "value", "growth", "summary"}:
                intent = "metric" if template == "value" else template
            elif template == "quarterly":
                intent, scope = "unavailable", "unsupported"
            if template == "risks":
                topic = "risk"
            metrics = ["revenue", "operating_income", "net_income"] if template == "summary" else [metric]
            plan = RequestPlan(intent=intent, numeric_intent=numeric, companies=companies,
                fiscal_year=year, report_year=year if intent in {"explanation", "hybrid"} else None,
                metrics=metrics, topic=topic, scope=scope)
            operation = "hybrid_" + numeric if intent == "hybrid" else intent
            identity = hashlib.sha256(json.dumps([companies, year, metrics, template]).encode()).hexdigest()[:20]
            if identity in seen:
                raise ValueError("A semantic task cluster crosses splits")
            seen.add(identity)
            expected = {key: value for key, value in plan.model_dump().items()
                        if key not in {"numeric_intent", "report_year", "topic"}}
            if intent == "hybrid":
                expected["numeric_intent"] = numeric
            if intent in {"hybrid", "explanation"}:
                expected.update(topic=topic, report_year=year)
            reference = None if intent == "explanation" else assemble_numeric(
                plan.model_copy(update={"intent": numeric}) if intent == "hybrid" else plan, store, [])
            tasks[split].append({"id": identity, "split": split, "category": "boundary" if scope != "annual_fiscal" or intent == "alignment" else operation,
                "company": companies[0], "companies": companies, "fiscal_year": year, "report_year": year,
                "operation": operation, "metrics": metrics, "topic": topic,
                "focus": metric if intent in {"hybrid", "explanation"} else None,
                "question": questions[template], "upload_ids": [], "reference_answer": reference,
                "common_support": True, "fact_clusters": [f"{company}:{year}" for company in companies],
                "semantic_cluster": identity, "expected_plan": expected})
    target.mkdir(parents=True)
    for split, rows in tasks.items():
        write_json(target / f"{split}.json", {"data_version": store.version, "tasks": rows})
    write_json(target / "documents.json", [])
    sources = ["data/processed/reports.json", "data/filing_sources.json", "data/indexes/contextual/structured.json"]
    write_json(target / "manifest.json", {"version": "renewed-v1", "reference_commit": "5f81c0eb",
        "data_version": store.version, "files": {p.name: sha256(p) for p in target.glob("*.json")},
        "sources": {name: sha256(ROOT / name) for name in sources},
        "scope": "fixed public snapshots; disjoint company/year/intent clusters; acceptance not used for search"})
    print(json.dumps({split: len(rows) for split, rows in tasks.items()}))


if __name__ == "__main__":
    main()
