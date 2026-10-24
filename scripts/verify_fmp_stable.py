"""Audit Stable snapshots, historical metrics, forecast horizons, and upstream PDF artifacts."""

import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys

import pandas as pd
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json
from finevolab.tools import FinancialStore


def check_statement_quality(snapshots):
    checks = []
    for snapshot in snapshots:
        endpoint = snapshot["url"].split("/stable/")[1].split("?")[0]
        if endpoint not in {"income-statement", "balance-sheet-statement", "cash-flow-statement"}:
            continue
        for row in json.loads((ROOT / snapshot["path"]).read_text(encoding="utf-8")):
            def amount(name):
                return Decimal(str(row[name]))
            if endpoint == "income-statement":
                equalities = [("gross_profit", amount("grossProfit"), amount("revenue") - amount("costOfRevenue"))]
            elif endpoint == "balance-sheet-statement":
                equalities = [("balance_equation", amount("totalAssets"), amount("totalLiabilities") + amount("totalEquity"))]
            else:
                equalities = [
                    ("cash_balance", amount("cashAtEndOfPeriod"), amount("cashAtBeginningOfPeriod") + amount("netChangeInCash")),
                    ("cash_movements", amount("netChangeInCash"), amount("operatingCashFlow")
                     + amount("netCashProvidedByInvestingActivities") + amount("netCashProvidedByFinancingActivities")
                     + amount("effectOfForexChangesOnCash")),
                    ("free_cash_flow", amount("freeCashFlow"), amount("operatingCashFlow") + amount("capitalExpenditure")),
                ]
            for name, observed, expected in equalities:
                difference = observed - expected
                checks.append({"company": row["symbol"], "year": int(row["fiscalYear"]), "check": name,
                    "difference": str(difference), "passed": difference.is_finite() and abs(difference) <= Decimal(1)})
    return checks


def main(args):
    access_path = ROOT / "project_notes/run_logs/fmp_stable_access.json"
    access = json.loads(access_path.read_text(encoding="utf-8"))
    snapshots, comparisons = [], []
    store = FinancialStore(ROOT / "data/processed/reports.json")
    for check in access["checks"]:
        if not check["passed"] or check["status_code"] != 200:
            raise ValueError("A required Stable endpoint did not pass")
        path = ROOT / check["path"]
        if sha256(path) != check["sha256"]:
            raise ValueError("A Stable snapshot changed")
        snapshots.append({"path": check["path"], "sha256": check["sha256"],
            "url": f"https://financialmodelingprep.com/stable/{check['endpoint']}?symbol={check['symbol']}",
            "records": check["records"], "retrieved_at": access["created_at"]})
        if check["endpoint"] != "income-statement":
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            year = int(row["fiscalYear"])
            for metric, field in (("revenue", "revenue"), ("operating_income", "operatingIncome"), ("net_income", "netIncome")):
                matches = [value for value in store.metrics if value.company == check["symbol"] and value.name == metric and value.fiscal_year == year]
                if not matches:
                    continue
                original = store.get_metric(check["symbol"], metric, year)
                if row["date"] != original.period_end or row["reportedCurrency"] != original.currency:
                    raise ValueError("FMP and the pinned source use different periods or currencies")
                observed = Decimal(str(row[field]))
                if observed != original.value:
                    raise ValueError("FMP and the pinned source differ on a checked amount")
                comparisons.append({"company": check["symbol"], "year": year, "metric": metric,
                    "amount": str(observed), "unit": original.unit, "period_end": row["date"],
                    "evidence_id": original.evidence_id, "status": "matched"})
    quality = check_statement_quality(snapshots)
    if any(not row["passed"] for row in quality):
        issues = [row for row in quality if not row["passed"]]
        print(json.dumps({"status": "source_quality_failed", "issues": issues}))
        raise ValueError("A saved FMP statement failed accounting consistency checks")
    output = args.output_root
    analysis = output / "analysis"
    summary = json.loads((analysis / "analysis_summary.json").read_text(encoding="utf-8"))
    frame = pd.read_csv(analysis / "financial_metrics_and_forecasts.csv")
    latest = int(summary["latest_year"].removesuffix("A"))
    expected_forecasts = [f"{latest + offset}E" for offset in (1, 2, 3)]
    if summary["forecast_years"] != expected_forecasts or any(year not in frame for year in expected_forecasts):
        raise ValueError("Forecast periods do not follow the latest disclosed fiscal year")
    income = pd.read_csv(analysis / "income_statement_raw_data.csv")
    for row in income.to_dict("records"):
        year = int(row["fiscalYear"])
        for name, field in (("Revenue", "revenue"), ("EBITDA", "ebitda"), ("EPS", "eps")):
            observed = Decimal(str(frame.loc[frame["metrics"] == name, f"{year}A"].iloc[0]))
            if abs(observed - Decimal(str(row[field]))) > Decimal("0.000001"):
                raise ValueError("An upstream historical metric differs from its FMP amount")
    if not (output / "report_verified/MSFT_cash_flow_chart.png").exists():
        raise ValueError("The Stable cash flow chart was not generated")
    pdf = output / "report_verified/Professional_Equity_Report_MSFT.pdf"
    if not pdf.read_bytes().startswith(b"%PDF"):
        raise ValueError("The report is not a PDF document")
    reader = PdfReader(pdf)
    page_text = [page.extract_text() or "" for page in reader.pages]
    if not page_text or any(not text.strip() for text in page_text):
        raise ValueError("The report contains an empty text page")
    text = "\n".join(page_text)
    if "Not assessed" not in text or "assumption-based" not in text:
        raise ValueError("The numeric report does not identify its scope and forecasts")
    if any(heading in text for heading in ("Competitive Landscape", "Risk Factors", "Company Overview")):
        raise ValueError("The numeric report contains an empty qualitative section")
    paths = sorted(path for path in analysis.iterdir() if path.suffix in {".csv", ".json"})
    paths.extend(sorted((output / "report_verified").glob("*.png")))
    paths.append(pdf)
    snapshot_manifest = ROOT / "data/raw/fmp/fmp-stable-access/manifest.json"
    write_json(snapshot_manifest, {"version": "fmp-stable", "provider": "Financial Modeling Prep",
        "api_version": "stable", "snapshots": snapshots})
    record = {**manifest(ROOT, args.run_id, {"api_version": "stable", "symbol": "MSFT", "years": 3,
        "model_calls": 0, "text_sections": "not_requested", "forecast_rates": [0.05, 0.06, 0.04]}),
        "status": "passed_numeric_report", "endpoint_checks": len(snapshots),
        "snapshot_manifest": {"path": snapshot_manifest.relative_to(ROOT).as_posix(), "sha256": sha256(snapshot_manifest)},
        "pinned_metric_checks": comparisons, "historical_report_metric_checks": len(income) * 3,
        "statement_quality_checks": quality,
        "actual_years": summary["available_years"], "forecast_years": expected_forecasts,
        "pdf": {"path": pdf.relative_to(ROOT).as_posix(), "sha256": sha256(pdf), "pages": len(page_text),
                "page_characters": [len(text) for text in page_text], "visual_review": "pending"},
        "artifacts": {path.relative_to(ROOT).as_posix(): sha256(path) for path in paths},
        "upstream_sources": {path.relative_to(ROOT).as_posix(): sha256(path) for path in
            [ROOT / "finrobot_equity/core/src/generate_financial_analysis.py",
             ROOT / "finrobot_equity/core/src/generate_pdf_report.py",
             ROOT / "finrobot_equity/core/src/modules/fmp_client.py",
             ROOT / "finrobot_equity/core/src/modules/market_data_api.py",
             ROOT / "finrobot_equity/core/src/modules/professional_pdf_report.py"]},
        "identity": "Stable-compatible upstream numeric report; not pristine Legacy reproduction",
        "optional_news": "HTTP_402_unavailable", "qualitative_model_sections": "not_generated"}
    write_json(ROOT / "project_notes/run_logs" / f"{args.run_id}.json", record)
    print(json.dumps({"status": record["status"], "endpoint_checks": len(snapshots),
        "pinned_metric_checks": len(comparisons), "historical_report_metric_checks": len(income) * 3,
        "statement_quality_checks": len(quality),
        "pdf_pages": len(page_text), "forecast_years": expected_forecasts}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=ROOT / "output/reports/fmp_stable-offline")
    parser.add_argument("--run-id", default="fmp_stable_report")
    main(parser.parse_args())
