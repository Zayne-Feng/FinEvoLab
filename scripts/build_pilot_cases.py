"""Build a small source-bound revenue-growth pilot without test leakage."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.tools import FinancialStore


def main() -> None:
    store = FinancialStore(ROOT / "data/processed/reports.json")
    splits = {
        "development": [("MSFT", 2023), ("AAPL", 2024), ("ZZZZ", 2024)],
        "validation": [("GOOGL", 2024), ("AMZN", 2023)],
        "test": [("META", 2024), ("MSFT", 2024)],
    }
    output = ROOT / "data/evaluation"
    output.mkdir(parents=True, exist_ok=True)
    for split, inputs in splits.items():
        cases = []
        for company, year in inputs:
            try:
                result = store.growth(company, "revenue", year)
                expected = {"status": "complete", "value": str(result.value), "evidence_ids": result.evidence_ids}
            except ValueError:
                expected = {"status": "insufficient_data", "value": "", "evidence_ids": []}
            cases.append({"id": f"{split}-{company}-{year}", "split": split,
                          "company": company, "fiscal_year": year, "expected": expected})
        payload = {"scope": "revenue_growth_pilot", "label_method": "programmatic_from_verified_snapshot",
                   "data_version": store.version, "cases": cases}
        (output / f"pilot_{split}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"development": 3, "validation": 2, "test": 2, "scope": "pilot"}))


if __name__ == "__main__":
    main()
