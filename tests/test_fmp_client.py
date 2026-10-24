"""Verify endpoint translation, fiscal periods, schema aliases, and credential redaction."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "finrobot_equity/core/src"))
from modules import fmp_client
from modules import market_data_api


def response(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result.request = requests.Request("GET", "https://financialmodelingprep.com/?apikey=fixture-secret").prepare()
    return result


class FmpClientTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        configuration = patch.dict(os.environ, {"FINEVOLAB_FMP_CACHE_DIR": directory.name,
            "FINEVOLAB_FMP_OFFLINE": "0", "FINEVOLAB_FMP_MAX_REQUESTS": "25"})
        configuration.start()
        self.addCleanup(configuration.stop)
        budget = patch.object(fmp_client, "_NETWORK_REQUESTS", 0)
        budget.start()
        self.addCleanup(budget.stop)
    def test_stable_translation_preserves_query_and_removes_symbol_from_path(self):
        url, params, endpoint, _ = fmp_client.translate_request(
            "https://financialmodelingprep.com/api/v3/income-statement/MSFT?apikey=fixture-secret&limit=3",
            {"period": "quarterly"}, version="stable")
        self.assertEqual(url, "https://financialmodelingprep.com/stable/income-statement")
        self.assertEqual(params, {"apikey": "fixture-secret", "limit": "3", "period": "quarter", "symbol": "MSFT"})
        self.assertEqual(endpoint, "income-statement")

    def test_legacy_mode_and_known_special_routes(self):
        url, params, _, _ = fmp_client.translate_request(
            "https://financialmodelingprep.com/api/v3/quote/MSFT?apikey=fixture-secret", version="legacy")
        self.assertTrue(url.endswith("/api/v3/quote/MSFT"))
        self.assertNotIn("symbol", params)
        for old, new in (("enterprise-value/MSFT", "enterprise-values"), ("quote-short/MSFT", "quote-short")):
            translated, _, _, _ = fmp_client.translate_request("https://financialmodelingprep.com/api/v3/" + old, version="stable")
            self.assertTrue(translated.endswith("/stable/" + new))
        translated, params, _, _ = fmp_client.translate_request(
            "https://financialmodelingprep.com/api/v3/stock_news", {"tickers": "MSFT", "limit": 2}, version="stable")
        self.assertTrue(translated.endswith("/news/stock"))
        self.assertEqual(params["symbols"], "MSFT")

    def test_unknown_paths_hosts_and_versions_do_not_send_credentials(self):
        for url in ("http://financialmodelingprep.com/api/v3/quote/MSFT", "https://example.com/api/v3/quote/MSFT",
                    "https://financialmodelingprep.com/api/v3/unknown/MSFT"):
            with self.assertRaises(fmp_client.FmpRequestError):
                fmp_client.translate_request(url)
        with self.assertRaises(fmp_client.FmpRequestError):
            fmp_client.translate_request("https://financialmodelingprep.com/api/v3/quote/MSFT", version="invalid")

    def test_aliases_preserve_zero_values_and_do_not_invent_absent_metrics(self):
        row = fmp_client.normalize_payload([{"fiscalYear": "2026", "marketCap": 0, "evToEBITDA": 0}], "key-metrics")[0]
        self.assertEqual(row["calendarYear"], "2026")
        self.assertEqual(row["mktCap"], 0)
        self.assertEqual(row["enterpriseValueOverEBITDA"], 0)
        self.assertNotIn("returnOnEquity", row)
        existing = fmp_client.normalize_payload([{"epsDiluted": 3, "epsdiluted": 2}], "income-statement")[0]
        self.assertEqual(existing["epsdiluted"], 2)

    def test_fiscal_year_is_not_inferred_from_calendar_date_when_disclosed(self):
        frame = pd.DataFrame({"date": pd.to_datetime(["2026-01-31"]), "fiscalYear": ["2025"]})
        self.assertEqual(fmp_client.fiscal_years(frame).tolist(), [2025])
        frame["fiscalYear"] = ["2025.5"]
        with self.assertRaises(ValueError):
            fmp_client.fiscal_years(frame)

    def test_historical_prices_retain_legacy_envelope_and_requested_length(self):
        rows = [{"date": "2026-10-20"}, {"date": "2026-10-22"}, {"date": "2026-10-21"}]
        result = fmp_client.normalize_payload(rows, "historical-price-full", "MSFT", 2)
        self.assertEqual(result, {"symbol": "MSFT", "historical": [rows[1], rows[2]]})

    def test_http_and_transport_errors_redact_credentials(self):
        url = "https://financialmodelingprep.com/api/v3/quote/MSFT?apikey=fixture-secret"
        for failure in (response({"error": "denied"}, 403), requests.exceptions.ConnectionError(url)):
            target, query, _, _ = fmp_client.translate_request(url)
            fmp_client.cache_path(target, query)[0].unlink(missing_ok=True)
            kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            with patch.dict(os.environ, {"FINEVOLAB_FMP_API_VERSION": "stable"}), patch.object(fmp_client.requests, "get", **kwargs):
                with self.assertRaises(fmp_client.FmpRequestError) as caught:
                    fmp_client.get(url)
            self.assertNotIn("fixture-secret", str(caught.exception))
            if caught.exception.response is not None:
                self.assertNotIn("fixture-secret", caught.exception.response.url)
                self.assertNotIn("fixture-secret", caught.exception.response.request.url)

    def test_success_response_is_sanitized_and_error_payload_is_rejected(self):
        url = "https://financialmodelingprep.com/api/v3/profile/MSFT?apikey=fixture-secret"
        with patch.object(fmp_client.requests, "get", return_value=response([{"marketCap": 12}])) as transport:
            result = fmp_client.get(url)
        self.assertEqual(result.json()[0]["mktCap"], 12)
        self.assertNotIn("fixture-secret", result.url)
        self.assertEqual(transport.call_args.kwargs["timeout"], 20)
        self.assertFalse(transport.call_args.kwargs["allow_redirects"])
        with patch.object(fmp_client, "load_cache", return_value=None), patch.object(fmp_client.requests, "get", return_value=response({"Error Message": "denied"})):
            with self.assertRaises(fmp_client.FmpRequestError):
                fmp_client.get(url)

    def test_upstream_statement_uses_the_disclosed_fiscal_year(self):
        payload = [{"date": "2026-01-31", "fiscalYear": "2025", "revenue": 0}]
        with patch.object(market_data_api, "fmp_get", return_value=response(payload)):
            result = market_data_api.get_fmp_income_statement("MSFT", "fixture-secret")
        self.assertEqual(result["year"].tolist(), [2025])
        self.assertEqual(result["revenue"].tolist(), [0])

    def test_unavailable_market_metrics_are_not_substituted_with_different_quantities(self):
        ratios = pd.DataFrame([{"priceEarningsRatio": 30, "debtEquityRatio": 2, "dividendYield": 0.02}])
        metrics = pd.DataFrame([{"peRatio": 30, "returnOnEquity": 0.25}])
        with patch.object(market_data_api, "get_fmp_current_price", return_value=100), \
             patch.object(market_data_api, "get_fmp_target_price", return_value=None) as targets, \
             patch.object(market_data_api, "get_fmp_analyst_rating", return_value=None) as ratings, \
             patch.object(market_data_api, "get_fmp_company_profile", return_value={"lastDiv": 5}), \
             patch.object(market_data_api, "get_fmp_ratios_and_key_metrics", return_value=(ratios, metrics)), \
             patch.object(market_data_api, "fmp_get", return_value=response([{}])):
            result = market_data_api.get_comprehensive_company_metrics("MSFT", "fixture-secret", include_analyst_data=False)
        targets.assert_not_called()
        ratings.assert_not_called()
        self.assertIsNone(result["fwd_pe"])
        self.assertIsNone(result["net_debt_to_equity"])
        self.assertIsNone(result["free_float"])
        self.assertEqual(result["dividend_yield"], 2)
        self.assertEqual(result["roe"], 25)
        self.assertEqual(result["sector"], "N/A")

    def test_positive_and_denied_responses_are_reused_without_network_calls(self):
        url = "https://financialmodelingprep.com/api/v3/quote/MSFT?apikey=fixture-secret"
        with patch.object(fmp_client.requests, "get", return_value=response([{"price": 100}])) as transport:
            first = fmp_client.get(url)
            second = fmp_client.get(url)
        self.assertEqual(transport.call_count, 1)
        self.assertFalse(first.fmp_cache_hit)
        self.assertTrue(second.fmp_cache_hit)
        target, query, _, _ = fmp_client.translate_request(url)
        fmp_client.save_cache(target, query, None, 402)
        with patch.object(fmp_client.requests, "get") as transport:
            with self.assertRaises(fmp_client.FmpRequestError):
                fmp_client.get(url)
        transport.assert_not_called()

    def test_offline_cache_miss_and_exhausted_budget_send_no_requests(self):
        url = "https://financialmodelingprep.com/api/v3/quote/MSFT?apikey=fixture-secret"
        for values in ({"FINEVOLAB_FMP_OFFLINE": "1"}, {"FINEVOLAB_FMP_MAX_REQUESTS": "0"}):
            with patch.dict(os.environ, values), patch.object(fmp_client.requests, "get") as transport:
                with self.assertRaises(fmp_client.FmpRequestError):
                    fmp_client.get(url)
            transport.assert_not_called()

    def test_cache_tampering_is_rejected_without_sending_a_request(self):
        url = "https://financialmodelingprep.com/api/v3/quote/MSFT?apikey=fixture-secret"
        target, query, _, _ = fmp_client.translate_request(url)
        fmp_client.save_cache(target, query, [{"price": 100}])
        path, _ = fmp_client.cache_path(target, query)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["payload"][0]["price"] = 999
        path.write_text(json.dumps(record), encoding="utf-8")
        with patch.object(fmp_client.requests, "get") as transport:
            with self.assertRaises(fmp_client.FmpRequestError):
                fmp_client.get(url)
        transport.assert_not_called()
        self.assertNotIn("fixture-secret", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
