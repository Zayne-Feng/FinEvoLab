"""Settle follow-up evaluation usage from canonical task and review ledgers."""

from decimal import Decimal
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.runtime import write_json
from settle_project_costs import Ledger, read, rows, sha


def main():
    pricing_path = ROOT / "configs/provider_pricing.json"
    ledger = Ledger(ROOT, read(pricing_path))
    unknown = []
    experiments = ROOT / "project_notes/experiments"
    for pattern in ("upload-*/tasks.jsonl", "upstream-*/tasks.jsonl",
                    "renewed-*/tasks.jsonl"):
        for path in sorted(experiments.glob(pattern)):
            for index, row in enumerate(rows(path)):
                ledger.add(path.parent.name, path, index, row.get("usage"))
                if not row.get("usage_complete", bool(row.get("usage"))):
                    unknown.append({"source": path.relative_to(ROOT).as_posix(), "record": index,
                        "observed_usage": row.get("usage")})
    for path in sorted(experiments.glob("upload-*/invalidated.json")):
        ledger.source(path)
        unknown.append({"source": path.relative_to(ROOT).as_posix(), "possible_in_flight_request": True,
            "usage": None, "reason": "The process was stopped; an in-flight response may not have been recorded"})
    for pattern in ("upload-*/requests.jsonl", "renewed-*/requests.jsonl"):
        for path in sorted((ROOT / "project_notes/evaluations").glob(pattern)):
            for index, row in enumerate(rows(path)):
                ledger.add(path.parent.name, path, index, row.get("usage"))
                if not row.get("usage"):
                    unknown.append({"source": path.relative_to(ROOT).as_posix(), "record": index})
    for directory in sorted((ROOT / "project_notes/optimization").glob("renewed-*")):
        for filename in ("evaluations.jsonl", "reflections.jsonl"):
            path = directory / filename
            if not path.exists():
                continue
            for index, row in enumerate(rows(path)):
                if filename == "reflections.jsonl" and row["status"] == "started":
                    continue
                usage = row.get("output", {}).get("usage") if filename == "evaluations.jsonl" else row.get("usage")
                ledger.add(directory.name, path, index, usage)
                if not usage or (filename == "evaluations.jsonl" and not row["output"].get("usage_complete")):
                    unknown.append({"source": path.relative_to(ROOT).as_posix(), "record": index})
    for pattern in ("followup_delivery.json", "renewed_delivery.json"):
        for path in sorted((ROOT / "project_notes/run_logs").glob(pattern)):
            delivery = read(path)
            ledger.add(path.stem, path, "web", delivery["web"]["usage"])
            for index, row in enumerate(delivery["recovery"]):
                for number, usage in enumerate(row["usage"]):
                    ledger.add(path.stem, path, f"{index}/{number}", usage)
    groups = {}
    for name in sorted({entry["group"] for entry in ledger.entries}):
        selected = [entry for entry in ledger.entries if entry["group"] == name]
        groups[name] = {**ledger.totals(name),
            "estimated_usd_lower": str(sum(Decimal(e["estimated_cost"]["lower"]) for e in selected)),
            "estimated_usd_upper": str(sum(Decimal(e["estimated_cost"]["upper"]) for e in selected))}
    result = {"status": "estimated_known_usage", "currency": "USD", "groups": groups,
        "known_lower": str(sum(Decimal(g["estimated_usd_lower"]) for g in groups.values())),
        "known_upper": str(sum(Decimal(g["estimated_usd_upper"]) for g in groups.values())),
        "unknown_usage": unknown, "entries": ledger.entries, "source_sha256": ledger.sources,
        "pricing_sha256": sha(pricing_path), "pricing": ledger.pricing, "invoice_cost": None,
        "historical_costs": "project_notes/evaluations/project_costs.json",
        "scope": "Follow-up development, preflight, comparison, review and delivery only; no historical double count"}
    write_json(ROOT / "project_notes/evaluations/followup_costs.json", result)
    print(json.dumps({key: result[key] for key in ("status", "known_lower", "known_upper", "unknown_usage")}))


if __name__ == "__main__":
    main()
