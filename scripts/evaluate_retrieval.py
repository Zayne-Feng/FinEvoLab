"""Run local retrieval smoke checks on public financial statements."""

from uuid import uuid4
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.embeddings import PassageEncoder, load_vectors
from finevolab.retrieval import ReportIndex
from finevolab.tools import FinancialStore


def main() -> None:
    config = json.loads((ROOT / "configs/retrieval.json").read_text())
    encoder = PassageEncoder(config, ROOT / "data/models")
    store = FinancialStore(ROOT / "data/processed/reports.json")
    cases = [("revenue", "consolidated income statement total revenue year ended June", "revenue"),
             ("net_income", "income statement net income earnings per share", "net_income"),
             ("revenue_zh", "微软2024财年合并利润表总收入", "revenue")]
    records = []
    for strategy in ("fixed", "structured"):
        path = ROOT / f"data/indexes/{strategy}.json"
        chunks = json.loads(path.read_text())
        index = ReportIndex(chunks, load_vectors(path, chunks, config), encoder)
        for mode in ("bm25", "vector", "hybrid"):
            for name, query, metric in cases:
                expected = [f"{store.get_metric('MSFT', metric, year).value / Decimal(1000000):,.0f}"
                            for year in (2023, 2024)]
                label = "total revenue" if metric == "revenue" else "net income"
                gold = {item["id"] for item in chunks if item["company"] == "MSFT"
                        and item["report_year"] == 2024 and label in item["text"].lower()
                        and all(value in item["text"] for value in expected)}
                if not gold:
                    raise ValueError("No source evidence matches the expected annual values")
                started = time.monotonic()
                results = index.search(query, company="MSFT", year=2024, mode=mode)
                records.append({"case": name, "strategy": strategy, "mode": mode,
                                "evidence_recall_at_5": float(bool(gold & {r["id"] for r in results})),
                                "gold_evidence_groups": [sorted(gold)], "retrieved_ids": [r["id"] for r in results],
                                "elapsed_seconds": round(time.monotonic() - started, 4)})
    report = {"run_id": uuid4().hex[:12] + "-retrieval-smoke",
              "purpose": "development_smoke_only", "config": config,
              "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "records": records}
    target = ROOT / "project_notes/evaluations/retrieval_smoke.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    report["gold_method"] = "source_alternatives_supporting_both_annual_values"
    report["data_version"] = store.version
    if target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
        report["history"] = previous.get("history", []) + [{k: v for k, v in previous.items() if k != "history"}]
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(records), "mean_recall_at_5": sum(r["evidence_recall_at_5"] for r in records)/len(records)}))


if __name__ == "__main__":
    main()
