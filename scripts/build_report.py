"""Generate source-bound HTML and PDF reports from local snapshots."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.reporting import build_report
from finevolab.tools import FinancialStore


def main() -> None:
    store = FinancialStore(ROOT / "data/processed/reports.json")
    print(json.dumps(build_report(store, "MSFT", 2024, ROOT / "output/reports/msft_2024"), indent=2))


if __name__ == "__main__":
    main()
