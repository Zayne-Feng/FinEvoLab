"""Translate supported FMP requests and normalize Stable response fields."""

import json
import hashlib
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
import requests


ROUTES = {
    "income-statement": "income-statement", "balance-sheet-statement": "balance-sheet-statement",
    "cash-flow-statement": "cash-flow-statement", "ratios": "ratios", "key-metrics": "key-metrics",
    "profile": "profile", "quote": "quote", "quote-short": "quote-short",
    "enterprise-value": "enterprise-values", "market-capitalization": "market-capitalization",
    "historical-price-full": "historical-price-eod/full", "stock_news": "news/stock",
    "price-target": "price-target-news", "upgrades-downgrades": "grades",
}
ALIASES = {
    "fiscalYear": "calendarYear", "epsDiluted": "epsdiluted",
    "evToEBITDA": "enterpriseValueOverEBITDA", "priceToEarningsRatio": "priceEarningsRatio",
    "debtToEquityRatio": "debtEquityRatio", "marketCap": "mktCap",
    "averageVolume": "volAvg", "lastDividend": "lastDiv", "exchange": "exchangeShortName",
    "debtToAssetsRatio": "debtRatio", "interestCoverageRatio": "interestCoverage",
}
_NETWORK_REQUESTS = 0
_BUDGET_LOCK = threading.Lock()


class FmpRequestError(requests.exceptions.RequestException):
    """Expose endpoint and status without credentials or provider response bodies."""


def cache_path(target, query):
    root = Path(os.getenv("FINEVOLAB_FMP_CACHE_DIR", str(Path(__file__).resolve().parents[4] / "output/cache/fmp")))
    public = {name: str(value) for name, value in query.items() if name.lower() != "apikey"}
    identity = json.dumps({"url": target, "params": public}, sort_keys=True, separators=(",", ":"))
    return root / (hashlib.sha256(identity.encode()).hexdigest() + ".json"), public


def save_cache(target, query, payload, status_code=200, source=None, observed_at=None):
    path, public = cache_path(target, query)
    data = json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":"))
    record = {"url": target, "params": public, "saved_at": time.time() if observed_at is None else observed_at, "status_code": status_code,
              "payload": payload, "payload_sha256": hashlib.sha256(data.encode()).hexdigest(), "source": source}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(record, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def load_cache(target, query, offline):
    path, public = cache_path(target, query)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        payload = json.dumps(record["payload"], allow_nan=False, sort_keys=True, separators=(",", ":"))
        if (record["url"] != target or record["params"] != public
                or hashlib.sha256(payload.encode()).hexdigest() != record["payload_sha256"]):
            raise ValueError
        if record["status_code"] not in {200, 401, 402, 403}:
            raise ValueError
        age = time.time() - float(record["saved_at"])
        if not offline and not 0 <= age <= float(os.getenv("FINEVOLAB_FMP_CACHE_TTL", "900")):
            return None
        return record
    except (OSError, KeyError, TypeError, ValueError):
        raise FmpRequestError("FMP cache is invalid; remove or replace the affected cache entry") from None


def translate_request(url: str, params=None, version=None) -> tuple[str, dict, str, int | None]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "financialmodelingprep.com":
        raise FmpRequestError("FMP requests require the official HTTPS host")
    query = dict(parse_qsl(parsed.query))
    query.update(params or {})
    mode = version or os.getenv("FINEVOLAB_FMP_API_VERSION", "stable")
    if mode not in {"stable", "legacy"}:
        raise FmpRequestError("FMP API version must be stable or legacy")
    parts = parsed.path.strip("/").split("/")
    if parts[0] == "stable":
        endpoint = "/".join(parts[1:])
        if endpoint not in ROUTES.values():
            raise FmpRequestError("Unsupported FMP Stable endpoint")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")), query, endpoint, None
    if len(parts) not in {3, 4} or parts[:2] not in (["api", "v3"], ["api", "v4"]):
        raise FmpRequestError("Unsupported FMP request path")
    original = parts[2]
    if original not in ROUTES:
        raise FmpRequestError("Unsupported FMP endpoint")
    if mode == "legacy":
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")), query, original, None
    if len(parts) == 4:
        if not re.fullmatch(r"[A-Za-z0-9.^-]{1,20}", parts[3]):
            raise FmpRequestError("Invalid FMP symbol")
        query["symbol"] = parts[3]
    if "tickers" in query:
        query["symbols"] = query.pop("tickers")
    if not query.get("symbol") and not query.get("symbols"):
        raise FmpRequestError("FMP request requires a symbol")
    if query.get("period") == "quarterly":
        query["period"] = "quarter"
    timeseries = query.pop("timeseries", None)
    if timeseries is not None:
        try:
            timeseries = int(timeseries)
            if not 1 <= timeseries <= 5000:
                raise ValueError
        except (TypeError, ValueError):
            raise FmpRequestError("FMP historical length must be between 1 and 5000") from None
    endpoint = ROUTES[original]
    return "https://financialmodelingprep.com/stable/" + endpoint, query, original, timeseries


def normalize_payload(payload, endpoint, symbol=None, timeseries=None):
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise FmpRequestError("FMP returned an invalid data payload")
    normalized = []
    for value in payload:
        row = dict(value)
        for source, target in ALIASES.items():
            if source in row and target not in row:
                row[target] = row[source]
        if "averageVolume" in row and "avgVolume" not in row:
            row["avgVolume"] = row["averageVolume"]
        if endpoint in {"upgrades-downgrades", "grades"} and "date" in row and "publishedDate" not in row:
            row["publishedDate"] = row["date"]
        normalized.append(row)
    if endpoint == "historical-price-full":
        normalized.sort(key=lambda row: row.get("date", ""), reverse=True)
        return {"symbol": symbol, "historical": normalized[:timeseries] if timeseries else normalized}
    return normalized


def get(url: str, params=None, timeout=20):
    global _NETWORK_REQUESTS
    target, query, endpoint, timeseries = translate_request(url, params)
    public_query = {name: value for name, value in query.items() if name.lower() != "apikey"}
    safe_url = target + ("?" + urlencode(public_query) if public_query else "")
    offline = os.getenv("FINEVOLAB_FMP_OFFLINE", "0") == "1"
    cached = load_cache(target, query, offline)
    if cached is not None:
        response = requests.Response()
        response.status_code = cached["status_code"]
        response._content = json.dumps(cached["payload"]).encode("utf-8")
        response.fmp_cache_hit = True
    else:
        if offline:
            raise FmpRequestError(f"FMP {endpoint} is unavailable in the offline cache")
        with _BUDGET_LOCK:
            limit = int(os.getenv("FINEVOLAB_FMP_MAX_REQUESTS", "25"))
            if _NETWORK_REQUESTS >= limit:
                raise FmpRequestError("FMP process request budget exhausted")
            _NETWORK_REQUESTS += 1
        try:
            response = requests.get(target, params=query, timeout=timeout, allow_redirects=False)
        except requests.exceptions.RequestException as error:
            raise FmpRequestError(f"FMP {endpoint} transport failed ({type(error).__name__})") from None
        response.fmp_cache_hit = False
    response.url = safe_url
    if response.request is not None:
        response.request.url = safe_url
    if not 200 <= response.status_code < 300:
        if cached is None and response.status_code in {401, 402, 403}:
            save_cache(target, query, None, response.status_code)
        raise FmpRequestError(f"FMP {endpoint} returned HTTP {response.status_code}", response=response)
    try:
        payload = response.json()
    except ValueError:
        raise FmpRequestError(f"FMP {endpoint} returned invalid JSON") from None
    raw_payload = payload
    if "/stable/" in target:
        payload = normalize_payload(payload, endpoint, query.get("symbol"), timeseries)
    elif not isinstance(payload, (list, dict)):
        raise FmpRequestError(f"FMP {endpoint} returned an invalid data payload")
    if cached is None:
        save_cache(target, query, raw_payload)
    response._content = json.dumps(payload, allow_nan=False).encode("utf-8")
    return response


def fiscal_years(frame: pd.DataFrame) -> pd.Series:
    """Prefer disclosed fiscal-year labels over the calendar year of the closing date."""
    for name in ("fiscalYear", "calendarYear"):
        if name in frame:
            years = pd.to_numeric(frame[name], errors="raise")
            if years.isna().any() or (years % 1 != 0).any() or not years.between(1900, 2200).all():
                raise ValueError("FMP fiscal years must be integers between 1900 and 2200")
            return years.astype(int)
    return frame["date"].dt.year
