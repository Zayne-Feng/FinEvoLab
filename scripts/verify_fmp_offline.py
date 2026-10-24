"""Execute the upstream numeric report pipeline with all HTTP transport disabled."""

import os
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "finrobot_equity/core/src"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CORE))
from finevolab.experiments import manifest
from modules import fmp_client
from scripts.seed_fmp_cache import main as seed_cache


def invoke(name, arguments):
    with patch.object(sys, "argv", [name, *arguments]):
        try:
            runpy.run_path(str(CORE / name), run_name="__main__")
        except SystemExit as error:
            if error.code not in (None, 0):
                raise ValueError("An upstream report stage failed") from None


def main():
    output = ROOT / "output/reports/fmp_stable-offline"
    common = ["--company-ticker", "MSFT", "--company-name", "Microsoft Corporation",
              "--config-file", str(ROOT / "finrobot_equity/core/config/config.ini")]
    configuration = {"FINEVOLAB_FMP_API_VERSION": "stable", "FINEVOLAB_FMP_OFFLINE": "1",
                     "FINEVOLAB_FMP_MAX_REQUESTS": "0"}
    with patch.dict(os.environ, configuration):
        seed_cache()
        with patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP transport is disabled")) as transport:
            invoke("generate_financial_analysis.py", [*common, "--years-limit", "3", "--skip-news",
                "--skip-retail-sentiment", "--output-dir", str(output / "analysis")])
            invoke("generate_pdf_report.py", [*common, "--numeric-only", "--analysis-dir", str(output / "analysis"),
                "--output-dir", str(output / "report_verified")])
        if transport.call_count or fmp_client._NETWORK_REQUESTS:
            raise ValueError("Offline verification attempted a network request")
    record = {
        **manifest(ROOT, "fmp-stable-offline", configuration), "status": "passed",
        "http_transport_calls": 0, "fmp_network_requests": 0, "model_calls": 0,
        "analysis": str((output / "analysis").relative_to(ROOT)),
        "pdf": str((output / "report_verified/Professional_Equity_Report_MSFT.pdf").relative_to(ROOT)),
        "optional_news": "skipped", "optional_analyst_data": "skipped", "retail_sentiment": "skipped"}
    path = ROOT / "project_notes/run_logs/fmp_stable_offline.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
