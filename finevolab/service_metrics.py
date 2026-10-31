"""Summarize completed request latencies with nearest-rank percentiles."""

from collections import Counter
import math


def summarize(records: list[dict], elapsed_seconds: float) -> dict:
    if not records or elapsed_seconds <= 0:
        raise ValueError("Measurements and a positive observation duration are required")
    latency = sorted(float(row["elapsed_seconds"]) for row in records)
    if any(not math.isfinite(value) or value < 0 for value in latency):
        raise ValueError("Latencies must be finite and nonnegative")
    counts = Counter(row["status"] for row in records)
    def percentile(fraction):
        return latency[max(0, math.ceil(len(latency) * fraction) - 1)]
    return {"requests": len(records), "successes": counts["complete"],
            "failures": len(records) - counts["complete"], "timeouts": counts["timeout"],
            "failure_rate": 1 - counts["complete"] / len(records),
            "p50_seconds": percentile(0.50), "p95_seconds": percentile(0.95),
            "max_seconds": latency[-1], "mean_seconds": sum(latency) / len(latency),
            "throughput_requests_per_second": len(records) / elapsed_seconds,
            "wall_seconds": elapsed_seconds, "status_counts": dict(counts),
            "percentile_method": "nearest_rank", "latency_scope": "request_dispatch_to_completion"}
