"""Aggregate canonical usage ledgers without counting summaries or injected responses twice."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.costs import estimate_cost, normalize_usage
from finevolab.runtime import write_json


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Ledger:
    def __init__(self, root, pricing):
        self.root, self.pricing = root, pricing
        self.entries, self.sources, self.identities, self.unknown = [], {}, set(), {}

    def source(self, path):
        path = Path(path)
        name = path.relative_to(self.root).as_posix()
        self.sources[name] = sha(path)
        return name

    def add(self, group, path, identity, usage, model="deepseek-flash"):
        name = self.source(path)
        key = name + "#" + str(identity)
        if key in self.identities:
            raise ValueError("A canonical usage record was included twice")
        self.identities.add(key)
        if not usage:
            return
        counts = normalize_usage(usage)
        rates = self.pricing["models"].get(model)
        self.entries.append({"group": group, "source": name, "identity": str(identity), "model": model,
            "usage": counts, "estimated_cost": estimate_cost(counts, rates) if rates else None})

    def totals(self, group):
        selected = [row for row in self.entries if row["group"] == group]
        return {key: sum(row["usage"][key] for row in selected)
                for key in ("requests", "input_tokens", "output_tokens", "total_tokens")}


def collect(ledger):
    root = ledger.root
    path = root / "project_notes/evaluations/deepseek_costs.json"
    formal = read(path)
    for index, row in enumerate(formal["runs"]):
        ledger.add("formal_v2", path, row["run_id"], {
            "requests": row["known_requests"], "input_tokens": row["known_input_tokens"],
            "output_tokens": row["known_output_tokens"],
            "total_tokens": row["known_input_tokens"] + row["known_output_tokens"]})
    ledger.unknown["formal_v2"] = {"records": formal["unknown_usage_evaluations"],
                                 "partial_observed_tokens": formal["partial_observed_tokens"]}
    path = root / "project_notes/evaluations/closure_usage.json"
    closure = read(path)
    for index, row in enumerate(closure["entries"]):
        ledger.add("closure", path, index, row)
    if ledger.totals("closure") != closure["known_usage"]:
        raise ValueError("Closure usage differs from its canonical total")
    ledger.unknown["closure"] = {"records": len(closure["incomplete_tasks"]),
                               "partial_observed_tokens": closure["partial_observed_tokens"]}
    experiments = root / "project_notes/experiments"
    for directory in sorted(experiments.glob("component-*")):
        if not (directory / "plans.jsonl").exists():
            continue
        for path in (directory / "plans.jsonl", directory / "tasks.jsonl"):
            for index, row in enumerate(rows(path)):
                ledger.add("components", path, index, row.get("usage"))
    directory = experiments / "component-harness"
    path = directory / "repair.jsonl"
    for index, row in enumerate(rows(path)):
        for number, request in enumerate(row["real_requests"]):
            ledger.add("components", path, f"{index}/{number}", request.get("usage"))
    path = directory / "checkpoint.jsonl"
    for index, row in enumerate(rows(path)):
        ledger.add("components", path, index, row["usage"])
    for pattern in ("project_notes/experiments/component-focus-repair*/requests.jsonl",
                    "project_notes/evaluations/component-*/requests.jsonl"):
        for path in sorted(root.glob(pattern)):
            for index, row in enumerate(rows(path)):
                ledger.add("components", path, index, row.get("usage", row.get("repair_usage")))
    path = root / "project_notes/evaluations/component-analysis/usage.json"
    component = read(path)
    ledger.source(path)
    if (ledger.totals("components")["requests"] != component["all_total_requests"]
            or ledger.totals("components")["total_tokens"] != component["all_total_tokens"]):
        raise ValueError("Component usage differs from its canonical total")
    ledger.unknown["components"] = {"records": component["review_unknown_usage_requests"],
                                  "partial_observed_tokens": 0}
    path = root / "project_notes/run_logs/scope_usage.json"
    scope = read(path)
    ledger.source(path)
    for name, digest in scope["input_sha256"].items():
        path = root / name
        if sha(path) != digest:
            raise ValueError("A scope usage input changed")
        if path.name in {"tasks.jsonl", "requests.jsonl"}:
            usages = [row.get("usage") for row in rows(path)]
        elif path.name == "repair.jsonl":
            usages = [request.get("usage") for row in rows(path) for request in row["actual_requests"]]
        elif path.name == "scope_sdk_smoke.json":
            record = read(path)
            usages = record["minimal_usage"] + [record["output"]["usage"]]
        elif path.name == "scope_delivery_v1.json":
            record = read(path)
            usages = [record["web"]["usage"]] + [usage for row in record["recovery"] for usage in row["usage"]]
        else:
            ledger.source(path)
            usages = []
        for index, usage in enumerate(usages):
            ledger.add("scope", path, index, usage)
    if (ledger.totals("scope")["requests"] != scope["recorded_responses"]
            or ledger.totals("scope")["total_tokens"] != scope["known_tokens"]):
        raise ValueError("Scope usage differs from its canonical total")
    ledger.unknown["scope"] = {"records": scope["unknown_usage_records"], "partial_observed_tokens": None}
    task_ids = set()
    for path in sorted((root / "project_notes/run_logs").glob("dashboard*.json")):
        record = read(path)
        candidates = record.get("model_runs", []) + record.get("live", {}).get("model_runs", [])
        for row in candidates:
            if row["task_id"] in task_ids:
                continue
            task_ids.add(row["task_id"])
            state_path = root / "output/tasks" / row["task_id"] / "state.json"
            usage = row["usage"]
            if state_path.exists():
                state = read(state_path)
                if normalize_usage(state["phases"]["analyze"]["result"]["usage"]) != normalize_usage(usage):
                    raise ValueError("A dashboard usage summary differs from its saved task")
            ledger.add("dashboard", path, row["task_id"], usage)
    unknown_probes = []
    for name in ("model_verification", "financial_analysis"):
        path = root / "project_notes/run_logs" / f"{name}.json"
        ledger.source(path)
        for row in read(path):
            result = row.get("result", {})
            usage = result.get("usage") or (result if "total_tokens" in result else None)
            if usage:
                ledger.add("provider_checks", path, row["run_id"], usage, row["model"])
            else:
                unknown_probes.append({"source": path.relative_to(root).as_posix(), "run_id": row["run_id"]})
    ledger.unknown["provider_checks"] = {"records": len(unknown_probes), "unrecorded_usage": unknown_probes}
    for name in ("workflow_smoke", "general_workflow", "web_question_smoke"):
        path = root / "project_notes/run_logs" / f"{name}.json"
        record = read(path)
        records = record if isinstance(record, list) else [record, *record.get("previous_checks", [])]
        for row in records:
            ledger.add("workflow_checks", path, row["task_id"], row["usage"], row["versions"]["model"])
    path = root / "project_notes/run_logs/final_delivery.json"
    delivery = read(path)
    ledger.add("final_delivery", path, "web", delivery["web"]["usage"])
    for index, row in enumerate(delivery["recovery"]):
        for number, usage in enumerate(row["usage"]):
            ledger.add("final_delivery", path, f"{index}/{number}", usage)
    return ledger


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "project_notes/evaluations/project_costs.json")
    args = parser.parse_args()
    pricing_path = ROOT / "configs/provider_pricing.json"
    ledger = collect(Ledger(ROOT, read(pricing_path)))
    groups = {}
    for name in sorted({row["group"] for row in ledger.entries}):
        selected = [row for row in ledger.entries if row["group"] == name]
        priced = [row for row in selected if row["estimated_cost"] is not None]
        groups[name] = {**ledger.totals(name),
            "estimated_usd_lower": str(sum(Decimal(row["estimated_cost"]["lower"]) for row in priced)),
            "estimated_usd_upper": str(sum(Decimal(row["estimated_cost"]["upper"]) for row in priced)),
            "unpriced_records": len(selected) - len(priced)}
    result = {"run_id": "project-cost-settlement", "created_at": datetime.now(timezone.utc).isoformat(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "worktree_status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).splitlines(),
        "calculator_sha256": {name: sha(ROOT / name) for name in
            ("finevolab/costs.py", "scripts/settle_project_costs.py")},
        "status": "estimated_known_usage", "currency": ledger.pricing["currency"],
        "pricing": ledger.pricing, "pricing_sha256": sha(pricing_path), "groups": groups,
        "known_estimated_cost_lower": str(sum(Decimal(row["estimated_usd_lower"]) for row in groups.values())),
        "known_estimated_cost_upper": str(sum(Decimal(row["estimated_usd_upper"]) for row in groups.values())),
        "unknown_usage": ledger.unknown, "source_sha256": ledger.sources, "entries": ledger.entries,
        "invoice_cost": None, "invoice_status": "not_supplied",
        "coverage": "Canonical formal-v2, closure, component, scope, dashboard, provider/workflow checks and final delivery",
        "excluded": ["Historical records outside the enumerated canonical sources",
            "Unrecorded or unknown provider usage", "Synthetic injected responses", "Repeated summaries and checkpoints"],
        "bounds_scope": "Known recorded usage only; unknown usage prevents a total-project billing upper bound"}
    write_json(args.output, result)
    print(json.dumps({"status": result["status"], "groups": groups,
        "lower": result["known_estimated_cost_lower"], "upper": result["known_estimated_cost_upper"]}))


if __name__ == "__main__":
    main()
