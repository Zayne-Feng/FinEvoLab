"""Freeze source-bound task splits and source anchors without model calls."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import fingerprint, write_json
from finevolab.tools import FinancialStore


def main():
    directory = ROOT / "data/evaluation/closure"
    if (directory / "manifest.json").exists():
        raise ValueError("The task collection is already frozen")
    store = FinancialStore(ROOT / "data/processed/reports.json")
    groups = {
        "development": [
            ("metric_query", ["META"], 2023, "net_income"),
            ("metric_query", ["AMZN"], 2022, "revenue"),
            ("annual_growth", ["GOOGL"], 2024, "operating_income"),
            ("annual_growth", ["AAPL"], 2023, "revenue"),
            ("company_comparison", ["MSFT", "AAPL", "META"], 2023, "revenue"),
            ("company_comparison", ["AMZN", "GOOGL", "META"], 2024, "net_income"),
            ("company_comparison", ["MSFT", "AMZN"], 2022, "operating_income"),
            ("integrated_analysis", ["META"], 2024, "summary"),
            ("fiscal_alignment", ["MSFT", "AAPL"], 2022, "revenue"),
            ("missing", ["GOOGL"], 2019, "revenue"),
            ("self_comparison", ["Amazon", "AMZN"], 2024, "revenue"),
            ("calendar", ["MSFT"], 2023, "revenue"),
        ],
        "acceptance": [
            ("metric_query", ["GOOGL"], 2022, "operating_income"),
            ("metric_query", ["AAPL"], 2024, "net_income"),
            ("annual_growth", ["META"], 2023, "net_income"),
            ("annual_growth", ["MSFT"], 2024, "operating_income"),
            ("company_comparison", ["MSFT", "AAPL", "GOOGL"], 2024, "operating_income"),
            ("company_comparison", ["AMZN", "META", "AAPL"], 2023, "revenue"),
            ("company_comparison", ["GOOGL", "MSFT"], 2022, "net_income"),
            ("integrated_analysis", ["AMZN"], 2023, "summary"),
            ("fiscal_alignment", ["MSFT", "META"], 2023, "operating_income"),
            ("missing", ["META"], 2018, "revenue"),
            ("self_comparison", ["Google", "GOOGL"], 2023, "net_income"),
            ("calendar", ["AMZN"], 2024, "revenue"),
        ],
        "gepa_acceptance": [
            ("company_comparison", list(companies), year, metric)
            for companies, year, metric in [
                (("MSFT", "AAPL", "AMZN"), 2022, "net_income"),
                (("MSFT", "META", "GOOGL"), 2023, "operating_income"),
                (("AMZN", "META", "GOOGL"), 2022, "revenue"),
                (("AAPL", "META", "GOOGL"), 2024, "net_income"),
                (("MSFT", "AAPL", "META"), 2024, "operating_income"),
                (("MSFT", "AMZN", "META"), 2023, "net_income"),
            ]
        ] + [("fiscal_alignment", ["MSFT", company], year, "net_income")
             for company, year in [("AAPL", 2024), ("AMZN", 2022), ("GOOGL", 2023)]]
          + [("annual_growth", [company], 2024, "net_income") for company in ("AAPL", "AMZN", "GOOGL")],
    }
    texts = {
        "development": [("MSFT", 2024, "revenue"), ("MSFT", 2023, "profit"),
                        ("AAPL", 2024, "revenue"), ("AAPL", 2022, "risk"),
                        ("GOOGL", 2023, "revenue"), ("AMZN", 2024, "profit"),
                        ("META", 2022, "revenue"), ("GOOGL", 2022, "risk")],
        "acceptance": [("MSFT", 2022, "revenue"), ("AAPL", 2023, "profit"),
                       ("GOOGL", 2024, "revenue"), ("AMZN", 2023, "risk"),
                       ("META", 2024, "revenue"), ("AMZN", 2022, "profit"),
                       ("META", 2023, "risk"), ("MSFT", 2024, "risk")],
    }
    topics = {"revenue": "revenue changes and their disclosed business drivers",
              "profit": "operating income changes and their disclosed cost or business drivers",
              "risk": "specific operating risks and their possible effects"}
    all_keys = []
    for split, definitions in groups.items():
        tasks = []
        for category, companies, year, metric in definitions:
            label = metric.replace("_", " ")
            case = {"split": split, "category": category, "companies": companies,
                    "company": companies[0], "fiscal_year": year, "metric": metric}
            if category == "metric_query":
                question = f"Give {companies[0]}'s FY{year} {label}, including the original unit and fiscal dates."
                expected = store.get_metric(companies[0], metric, year).model_dump(mode="json")
            elif category == "annual_growth":
                question = f"Compute the FY{year} annual change in {label} for {companies[0]}, using the prior fiscal year."
                expected = store.growth(companies[0], metric, year).model_dump(mode="json")
            elif category in {"company_comparison", "fiscal_alignment"}:
                if category == "company_comparison":
                    question = f"Compare FY{year} {label} for {', '.join(companies)}. Show the amounts and explain any fiscal-date differences."
                else:
                    question = f"Are the FY{year} reporting periods for {', '.join(companies)} strictly aligned? Use {label} observations as evidence."
                expected = store.compare(companies, metric, year).model_dump(mode="json")
                if category == "fiscal_alignment":
                    case.update(category="boundary", scenario="fiscal_alignment")
                    expected["status"] = "periods_not_aligned"
            elif category == "integrated_analysis":
                question = f"Provide {companies[0]}'s FY{year} income summary with revenue, operating income and net income."
                expected = {name: store.get_metric(companies[0], name, year).model_dump(mode="json")
                            for name in ("revenue", "operating_income", "net_income")}
            else:
                case.update(category="boundary", scenario=category)
                if category == "missing":
                    question = f"What was {companies[0]}'s FY{year} annual {label}? Do not estimate absent observations."
                    expected = {"status": "insufficient_data"}
                elif category == "self_comparison":
                    question = f"Compare {companies[0]} against {companies[1]} on FY{year} {label}."
                    expected = {"status": "invalid_parameters"}
                else:
                    question = f"Give {companies[0]}'s {year} {label}; I have not decided whether to use calendar year or fiscal year."
                    expected = {"status": "clarification_required"}
            case.update(question=question, expected=expected)
            key = fingerprint({"category": category, "companies": sorted(store.company_id(c) for c in companies),
                               "year": year, "metric": metric})
            case.update(id=key[:20], semantic_key=key)
            all_keys.append(key)
            tasks.append(case)
        for company, year, topic in texts.get(split, []):
            key = fingerprint({"company": company, "year": year, "topic": topic})
            tasks.append({"id": key[:20], "semantic_key": key, "split": split, "category": "explanation",
                          "company": company, "fiscal_year": year, "topic": topic,
                          "question": f"Using {company}'s FY{year} annual report, explain {topics[topic]}. "
                                      "Give source passages for each main statement; do not invent causes or forecasts.",
                          "expected": {"company": company, "report_year": year, "topic": topic}})
            all_keys.append(key)
        write_json(directory / f"{split}.json", {"data_version": store.version, "tasks": tasks})
    if len(set(all_keys)) != len(all_keys):
        raise ValueError("Task semantics overlap across splits")
    write_json(directory / "manifest.json", {
        "status": "frozen_closure_v1", "data_version": store.version,
        "split_sha256": {name: sha256(directory / f"{name}.json") for name in groups},
        "source_audit": "canonical_SEC_metrics_and_source_bound_text_evaluation",
        "text_gold": "source_validity_plus_blind_support_review_not_exhaustive_gold",
        "overlap_with_v2": "existing_snapshot_facts_and_some_requests_overlap_regression_only",
        "generalization_scope": "within_snapshot_request_combinations", "semantic_duplicates": 0,
    })
    write_json(ROOT / "project_notes/evaluations/closure_task_audit.json",
               manifest(ROOT, "closure-task-freeze", {"dataset": sha256(directory / "manifest.json"),
                        "counts": {name: len(json.loads((directory / f"{name}.json").read_text())["tasks"]) for name in groups},
                        "acceptance_opened_for_development": False}))
    print("Task splits frozen")


if __name__ == "__main__":
    main()
