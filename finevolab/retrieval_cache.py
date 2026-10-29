"""Share exact, version-bound passage rankings through Redis."""

from functools import lru_cache
import json
import math
import os

from finevolab.retrieval_settings import service_event
from finevolab.runtime import fingerprint


@lru_cache(maxsize=4)
def redis_client(url: str, timeout: float):
    import redis
    return redis.Redis.from_url(url, socket_connect_timeout=timeout, socket_timeout=timeout,
                                decode_responses=True, max_connections=8)


def cache_client(settings):
    return redis_client(os.environ.get("FINEVOLAB_REDIS_URL", settings.url), settings.timeout_seconds)


def restore(raw: str, identity: str, chunks: list[dict]) -> list[dict]:
    payload = json.loads(raw)
    if payload["identity"] != identity or fingerprint({"rows": payload["rows"]}) != payload["checksum"]:
        raise ValueError("Retrieval cache identity or checksum differs")
    lookup = {item["id"]: item for item in chunks}
    rows, seen = [], set()
    for row in payload["rows"]:
        if set(row) - {"id", "score", "rerank_score"} or row["id"] in seen:
            raise ValueError("Invalid retrieval cache row")
        if not all(math.isfinite(float(value)) for key, value in row.items() if key != "id"):
            raise ValueError("Cached retrieval scores must be finite")
        seen.add(row["id"])
        rows.append({**lookup[row["id"]], **row})
    if len(rows) > 5 or sum(len(item["text"]) for item in rows) > 10000:
        raise ValueError("Cached passages exceed the context budget")
    return rows


def cached_passages(settings, identity: str, chunks: list[dict], compute):
    key = settings.prefix + ":" + identity
    client = None
    try:
        client = cache_client(settings)
        raw = client.get(key)
        if raw is not None:
            result = restore(raw, identity, chunks)
            service_event("retrieval_cache", backend="redis", status="hit")
            return result
        service_event("retrieval_cache", backend="redis", status="miss")
    except Exception as error:
        service_event("retrieval_cache", backend="redis", status="error", error_type=type(error).__name__)
    result = compute()
    rows = [{key: item[key] for key in ("id", "score", "rerank_score") if key in item} for item in result]
    payload = {"identity": identity, "rows": rows, "checksum": fingerprint({"rows": rows})}
    if client is not None:
        try:
            client.set(key, json.dumps(payload, separators=(",", ":")), ex=settings.ttl_seconds)
        except Exception as error:
            service_event("retrieval_cache", backend="redis", status="write_error", error_type=type(error).__name__)
    return result
