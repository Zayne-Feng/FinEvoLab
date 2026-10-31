"""Compare fixed plans and real model requests across financial tool backends."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark_services import identity, http_server
from finevolab.config import ModelConfig
from finevolab.grounded import RequestPlan, analyze_grounded, assemble_numeric
from finevolab.runtime import RunBudget, write_json
from finevolab.service_metrics import summarize
from finevolab.tools import FinancialStore
from finevolab.tracing import trace_scope, tracing_mode, flush_exports
from finevolab.validation import validate_answer


def case(identifier, question, intent, companies, metric="revenue", year=2024, **extra):
    return {"id": identifier, "question": question, "plan": RequestPlan(intent=intent, companies=companies,
        fiscal_year=year, metrics=metric if isinstance(metric, list) else [metric], **extra).model_dump()}


CASES = [
    case("growth", "Calculate Microsoft revenue growth for fiscal year 2024.", "growth", ["MSFT"]),
    case("comparison", "Compare Apple and Microsoft revenue for fiscal year 2024 and state fiscal-period differences.", "comparison", ["AAPL", "MSFT"]),
    case("hybrid", "Calculate Microsoft fiscal year 2024 revenue growth and explain its disclosed drivers using the FY2024 annual report.", "hybrid", ["MSFT"], numeric_intent="growth", report_year=2024),
    case("metric", "What was Apple's net income for fiscal year 2023?", "metric", ["AAPL"], "net_income", 2023),
    case("summary", "Give Microsoft's revenue, operating income and net income for fiscal year 2024.", "summary", ["MSFT"], ["revenue", "operating_income", "net_income"]),
    case("alignment", "Are Apple and Microsoft fiscal year 2024 date ranges strictly aligned?", "alignment", ["AAPL", "MSFT"]),
    case("profit_growth", "Calculate Amazon's operating income percentage growth for fiscal year 2024.", "growth", ["AMZN"], "operating_income"),
    case("revenue", "Give Alphabet's annual revenue for fiscal year 2023.", "metric", ["GOOGL"], year=2023),
    case("meta_growth", "Calculate Meta's annual revenue percentage growth for fiscal year 2024.", "growth", ["META"]),
    case("explanation", "Explain Microsoft's revenue growth drivers disclosed in its FY2024 annual report.", "explanation", ["MSFT"], report_year=2024),
    case("clarification", "Compare annual revenue without selecting companies or a fiscal year.", "clarification", [], scope="ambiguous"),
    case("invalid", "Compare MSFT with Microsoft revenue in fiscal year 2024.", "comparison", ["MSFT", "MSFT"])]


def assess(store, item, output):
    expected = RequestPlan.model_validate(item["plan"])
    actual = RequestPlan.model_validate(output["request"])
    expected_companies = sorted(store.company_id(c) for c in expected.companies)
    actual_companies = sorted(store.company_id(c) for c in actual.companies)
    plan_ok = actual.intent == expected.intent and actual_companies == expected_companies
    if expected.intent != "clarification":
        plan_ok = plan_ok and actual.fiscal_year == expected.fiscal_year and set(actual.metrics) == set(expected.metrics)
    if expected.intent == "hybrid":
        plan_ok = plan_ok and actual.numeric_intent == expected.numeric_intent
    if item["id"] == "invalid" and actual.intent == "invalid":
        plan_ok = True
    numeric_ok = True
    if expected.intent != "explanation":
        reference = assemble_numeric(expected, store, [])
        observed = output["answer"]
        def normalized(value):
            return {key: sorted(items, key=lambda x: json.dumps(x, sort_keys=True)) if isinstance(items, list) else items
                    for key, items in value.items() if key in {"status", "metrics", "calculations", "periods_aligned", "evidence_ids"}}
        numeric_ok = normalized(observed) == normalized(reference)
    narrative_ok = output.get("narrative", output.get("answer", {})).get("status") == "complete" if expected.intent in {"explanation", "hybrid"} else True
    return {"plan": plan_ok, "numeric": numeric_ok, "narrative_complete": narrative_ok,
            "source_validation": validate_answer(store, output)["status"] == "passed"}


async def run(args):
    if args.repeats < 1:
        raise ValueError("Positive repetitions are required")
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    cases = CASES[:3] if args.mode == "smoke" else [c for c in CASES if c["plan"]["intent"] not in {"hybrid", "explanation"}] if args.mode == "fixed" else CASES
    args.requests, args.concurrency, args.real_model = len(cases), [1], args.mode != "fixed"
    manifest = identity(args)
    manifest["source_hashes"]["scripts/benchmark_tool_backends.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest["config"].update(mode=args.mode, cases=cases, warmup_per_case=0, tool_backends=["direct", "mcp"], review_mode="off",
        budget=vars(RunBudget(max_context_chars=60000)), trace_mode=args.trace_mode,
        scoring_scope="planning_numeric_and_source_validation")
    if (directory / "manifest.json").exists():
        saved = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if saved["source_hashes"] != manifest["source_hashes"] or saved["config"] != manifest["config"]:
            raise ValueError("The source or experiment configuration changed")
    else:
        write_json(directory / "manifest.json", manifest)
    path = directory / "results.json"
    records = json.loads(path.read_text(encoding="utf-8"))["records"] if path.exists() else []
    completed = {(r["case_id"], r["repeat"], r["tool_backend"]) for r in records}
    store = FinancialStore(ROOT / "data/processed/reports.json")
    candidate = json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8"))
    budget = RunBudget(max_context_chars=60000)
    settings = ModelConfig.load() if args.real_model else ModelConfig("fixture", "https://fixture.invalid/", "local-fixture")
    manifest["model"] = {"name": settings.model, "base_url": settings.base_url, "thinking": settings.extra_body}
    write_json(directory / "manifest.json", manifest)
    async def execute(item, repeat, backend, client=None):
        key = (item["id"], repeat, backend)
        if key in completed:
            return
        telemetry = {}
        events = []
        started = time.perf_counter()
        row = {"case_id": item["id"], "repeat": repeat, "tool_backend": backend}
        try:
            if client is None:
                with trace_scope("fixed.business", directory / "traces") as (trace, _):
                    output = await analyze_grounded(settings, store, item["question"], candidate, events, budget,
                        telemetry, root=ROOT, request_plan=item["plan"], tool_backend=backend)
                row["trace_id"] = trace.trace_id
            else:
                response = await client.post("/api/questions", json={"question": item["question"], "tool_backend": backend})
                response.raise_for_status()
                state = response.json()
                output = state["phases"]["analyze"]["result"]
                row.update(task_id=state["task_id"], trace_id=state["trace_id"],
                    validation=state["phases"]["validate"]["result"], report=state["phases"]["report"]["status"])
            checks = assess(store, item, output)
            row.update(status="complete" if all(checks.values()) else "mismatch", checks=checks,
                output={key: output[key] for key in ("request", "answer", "answer_type", "narrative", "overall_status", "evidence", "tool_backend", "remote_tool_calls", "tool_backend_startup_seconds", "tool_calls", "usage", "repair", "events") if key in output})
        except Exception as error:
            row.update(status="timeout" if isinstance(error, TimeoutError) else "failed", error_type=type(error).__name__,
                       observed_usage=telemetry.get("usage"))
        row["elapsed_seconds"] = time.perf_counter() - started
        records.append(row)
        completed.add(key)
        write_json(path, {"run_id": args.run_id, "records": records})
        print(json.dumps({key: row[key] for key in ("case_id", "repeat", "tool_backend", "status", "elapsed_seconds")}), flush=True)
    with tracing_mode(args.trace_mode):
        if args.mode == "fixed":
            for repeat in range(args.repeats):
                for item in cases:
                    for backend in ("direct", "mcp") if repeat % 2 == 0 else ("mcp", "direct"):
                        await execute(item, repeat, backend)
        else:
            import os
            old = os.environ.get("FINEVOLAB_TRACE_MODE")
            os.environ["FINEVOLAB_TRACE_MODE"] = args.trace_mode
            try:
                async with http_server(directory) as client:
                    for repeat in range(args.repeats):
                        ordered = list(cases)
                        random.Random(17 + repeat).shuffle(ordered)
                        for item in ordered:
                            for backend in ("direct", "mcp") if repeat % 2 == 0 else ("mcp", "direct"):
                                await execute(item, repeat, backend, client)
            finally:
                if old is None:
                    os.environ.pop("FINEVOLAB_TRACE_MODE", None)
                else:
                    os.environ["FINEVOLAB_TRACE_MODE"] = old
        flush_exports()
    summaries = [{"tool_backend": backend, **summarize([r for r in records if r["tool_backend"] == backend],
                  sum(r["elapsed_seconds"] for r in records if r["tool_backend"] == backend))} for backend in ("direct", "mcp")]
    write_json(path, {"run_id": args.run_id, "records": records, "summaries": summaries,
                     "all_passed": len(records) == len(cases) * args.repeats * 2 and all(r["status"] == "complete" for r in records)})
    if any(r["status"] != "complete" for r in records):
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fixed", "smoke", "real"], default="fixed")
    parser.add_argument("--run-id", default="tool-backends-v1")
    parser.add_argument("--output", type=Path, default=ROOT / "output/tool_backends")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--trace-mode", default="file")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
