"""Create source-bound reference tasks and audit split overlap."""

from collections import Counter
from itertools import combinations, product
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.tools import FinancialStore


def main() -> None:
    target = ROOT / "data/evaluation"
    if (target / "manifest.json").exists():
        existing = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        if existing.get("status", "").startswith("frozen"):
            raise ValueError("Frozen evaluation data cannot be overwritten")
    store = FinancialStore(ROOT / "data/processed/reports.json")
    rng = random.Random(17)
    companies = ["MSFT", "AAPL", "GOOGL", "AMZN", "META"]
    metrics = ["revenue", "operating_income", "net_income"]
    pools = {}
    inputs = list(product(companies, (2022, 2023, 2024), metrics))
    rng.shuffle(inputs)
    pools["metric_query"] = [{"company": company, "fiscal_year": year, "metric": metric,
                              "expected": store.get_metric(company, metric, year).model_dump(mode="json")}
                             for company, year, metric in inputs[:20]]
    inputs = list(product(companies, (2023, 2024), metrics))
    rng.shuffle(inputs)
    pools["annual_growth"] = [{"company": company, "fiscal_year": year, "metric": metric,
                              "expected": store.growth(company, metric, year).model_dump(mode="json")}
                             for company, year, metric in inputs[:20]]
    inputs = [(list(pair), year, metric) for pair, year, metric
              in product(combinations(companies, 2), (2022, 2023, 2024), metrics)]
    rng.shuffle(inputs)
    pools["company_comparison"] = [{"companies": pair, "fiscal_year": year, "metric": metric,
                                   "expected": store.compare(pair, metric, year).model_dump(mode="json")}
                                  for pair, year, metric in inputs[:15]]
    pools["integrated_analysis"] = [
        {"company": company, "fiscal_year": year,
         "expected": {name: store.get_metric(company, name, year).model_dump(mode="json") for name in metrics},
         "open_text_review": "pending_rubric_calibration"}
        for company, year in product(companies, (2022, 2023, 2024))]
    edge_inputs = [
        ("unknown_company", "Get ZZZZ annual revenue in FY2024.", "insufficient_data"),
        ("missing_metric", "Get MSFT annual free_cash_flow in FY2024.", "insufficient_data"),
        ("missing_year", "Get AAPL annual revenue in FY2030.", "insufficient_data"),
        ("calendar_ambiguity", "Compare MSFT calendar-year 2024 revenue with fiscal-year 2024 revenue.", "clarification_required"),
        ("quarterly_scope", "Get AAPL Q4 revenue in FY2024 from the annual-only metric store.", "insufficient_data"),
        ("currency_conversion", "Convert MSFT FY2024 revenue into EUR without an exchange rate.", "clarification_required"),
        ("fiscal_alignment", "Compare MSFT and AAPL FY2024 revenue and state whether periods align.", "periods_not_aligned"),
        ("missing_baseline", "Calculate AAPL FY2022 annual revenue growth from the stored metrics.", "insufficient_data"),
        ("historical_disclosure", "Give MSFT FY2023 revenue as known on 2022-12-31.", "insufficient_data"),
        ("duplicate_company", "Compare MSFT with MSFT as two distinct companies in FY2024.", "invalid_parameters"),
    ]
    pools["boundary"] = [{"scenario": kind, "question": question, "expected": {"status": status},
                          "review_status": "manual_review_pending"} for kind, question, status in edge_inputs]
    allocation = {"metric_query": (8, 5, 7), "annual_growth": (8, 5, 7),
                  "company_comparison": (5, 4, 6), "integrated_analysis": (5, 4, 6), "boundary": (4, 2, 4)}
    splits = {name: [] for name in ("development", "validation", "test")}
    for category, tasks in pools.items():
        offset = 0
        for split, count in zip(splits, allocation[category]):
            for task in tasks[offset:offset + count]:
                identity = {key: value for key, value in task.items() if key not in {"expected", "question"}}
                identifier = hashlib.sha256(json.dumps({"category": category, **identity}, sort_keys=True).encode()).hexdigest()[:16]
                question = task.get("question")
                if question is None:
                    if category == "metric_query":
                        question = f"Find {task['company']} FY{task['fiscal_year']} annual {task['metric']} with currency, units, period, and source."
                    elif category == "annual_growth":
                        question = (f"Calculate {task['company']} FY{task['fiscal_year']} {task['metric']} change using "
                                    "(current / previous - 1) * 100 and cite both annual inputs.")
                    elif category == "company_comparison":
                        question = (f"Compare {', '.join(task['companies'])} FY{task['fiscal_year']} {task['metric']}; "
                                    "state the actual fiscal periods and whether they align.")
                    else:
                        question = (f"Summarize {task['company']} FY{task['fiscal_year']} revenue, operating income, "
                                    "and net income with source-backed numerical evidence.")
                splits[split].append({"id": identifier, "category": category, "split": split, **task, "question": question})
            offset += count
    identifiers = [item["id"] for tasks in splits.values() for item in tasks]
    if len(identifiers) != 80 or len(set(identifiers)) != 80:
        raise ValueError("Reference task identities are not unique")
    for split, tasks in splits.items():
        payload = {"status": "draft", "label_method": "source_checked_programmatic_reference",
                   "data_version": store.version, "tasks": tasks}
        (target / f"{split}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    overlap = {}
    for left, right in combinations(splits, 2):
        def entities(tasks):
            return {company for task in tasks for company in task.get("companies", [task.get("company")]) if company}
        overlap[left + "_vs_" + right] = {
            "companies": sorted(entities(splits[left]) & entities(splits[right])),
            "fiscal_years": sorted({task.get("fiscal_year") for task in splits[left] if "fiscal_year" in task}
                                   & {task.get("fiscal_year") for task in splits[right] if "fiscal_year" in task}),
            "shared_task_ids": sorted({task["id"] for task in splits[left]} & {task["id"] for task in splits[right]}),
            "shared_task_categories": sorted({task["category"] for task in splits[left]} & {task["category"] for task in splits[right]}),
        }
    manifest = {"status": "draft_not_formally_frozen", "data_version": store.version,
                "split_sizes": {name: len(tasks) for name, tasks in splits.items()},
                "category_counts": dict(Counter(task["category"] for tasks in splits.values() for task in tasks)),
                "overlap_audit": overlap, "exact_task_identity_duplicates": 0,
                "semantic_near_duplicate_review": "pending",
                "question_template_review": "pending", "open_text_calibration": "pending",
                "test_used_by_optimizer": False}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"split_sizes": manifest["split_sizes"], "categories": manifest["category_counts"],
                      "status": manifest["status"]}))


if __name__ == "__main__":
    main()
