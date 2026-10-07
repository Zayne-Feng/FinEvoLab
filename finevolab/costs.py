"""Normalize observed token usage and calculate reproducible tariff bounds."""

from decimal import Decimal


def normalize_usage(usage: dict) -> dict:
    counts = {
        "requests": usage.get("requests", 1),
        "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
        "output_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
        "total_tokens": usage.get("total_tokens"),
    }
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts.values()):
        raise ValueError("Usage requires nonnegative integer request and token counts")
    if counts["input_tokens"] + counts["output_tokens"] != counts["total_tokens"]:
        raise ValueError("Input and output tokens must sum to total tokens")
    return counts


def estimate_cost(usage: dict, rates: dict, period: str | None = None) -> dict:
    counts = normalize_usage(usage)
    periods = [rates[period]] if period else list(rates.values())
    if not periods:
        raise ValueError("At least one tariff period is required")
    values = [{key: Decimal(row[key]) for key in ("cached_input", "uncached_input", "output")}
              for row in periods]
    if any(not value.is_finite() or value < 0 for row in values for value in row.values()):
        raise ValueError("Tariff rates must be finite and nonnegative")
    if any(row["cached_input"] > row["uncached_input"] for row in values):
        raise ValueError("Cached input rates must not exceed uncached input rates")
    lower = (counts["input_tokens"] * min(row["cached_input"] for row in values)
             + counts["output_tokens"] * min(row["output"] for row in values)) / Decimal(1_000_000)
    upper = (counts["input_tokens"] * max(row["uncached_input"] for row in values)
             + counts["output_tokens"] * max(row["output"] for row in values)) / Decimal(1_000_000)
    return {"lower": str(lower), "upper": str(upper), "period": period or "any",
            "method": "observed_usage_cache_and_tariff_bounds"}
