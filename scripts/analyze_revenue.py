"""Calculate annual revenue growth from local financial evidence."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.tools import FinancialStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", default="MSFT")
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args()
    store = FinancialStore(ROOT / "data/processed/reports.json")
    result = store.growth(args.company, "revenue", args.year)
    print(json.dumps({"data_version": store.version, "result": result.model_dump(mode="json"),
                      "evidence": [store.evidence[key] for key in result.evidence_ids]}, indent=2))


if __name__ == "__main__":
    main()
