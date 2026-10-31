"""Measure financial MCP equivalence, TCP HTTP load, and phase recovery."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import random
import socket
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finevolab.mcp_client import call_financial_tool, financial_session
from finevolab.mcp_server import (FinancialToolService, MetricRequest, CalculationRequest,
                                 ComparisonRequest, SearchRequest)
from finevolab.runtime import PhaseRunner, observe, write_json
from finevolab.service_metrics import summarize
from finevolab.tracing import trace_scope, parse_traceparent

SCHEMAS = {"get_financial_metrics": MetricRequest, "calculate_metrics": CalculationRequest,
           "compare_companies": ComparisonRequest, "search_reports": SearchRequest}
CASES = [
    {"tool": "get_financial_metrics", "request": {"company": "MSFT", "metric": "revenue", "fiscal_year": 2024}},
    {"tool": "calculate_metrics", "request": {"company": "MSFT", "metric": "revenue", "fiscal_year": 2024}},
    {"tool": "calculate_metrics", "request": {"company": "MSFT", "metric": "operating_margin_change", "fiscal_year": 2024}},
    {"tool": "compare_companies", "request": {"companies": ["AAPL", "MSFT"], "metric": "revenue", "fiscal_year": 2024}},
    {"tool": "search_reports", "request": {"company": "MSFT", "query": "revenue growth", "report_year": 2024}}]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def identity(args):
    paths = sorted(set(list((ROOT / "finevolab").glob("*.py")) +
                       [Path(__file__), ROOT / "scripts/serve_mcp.py", ROOT / "scripts/call_mcp.py",
                        ROOT / "tests/test_mcp.py", ROOT / "tests/test_tracing.py",
                        ROOT / "configs/mcp.json", ROOT / "configs/retrieval.json",
                        ROOT / "configs/retrieval_services.json", ROOT / "configs/company_aliases.json",
                        ROOT / "requirements/development.lock.txt"]))
    return {"run_id": args.run_id, "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "source_hashes": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            "data_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
                            ("data/processed/reports.json", "data/indexes/structured.json")},
            "config": {"requests_per_cell": args.requests, "repeats": args.repeats,
                       "concurrency": args.concurrency, "seed": 17, "warmup_per_case": 1,
                       "trace_export": True, "real_model_smoke": args.real_model},
            "environment": {"python": platform.python_version(), "os": platform.system(),
                            "processor": platform.processor(), "cpu_count": os.cpu_count(),
                            "packages": {name: importlib.metadata.version(name) for name in ("mcp", "fastapi", "httpx", "numpy")}}}


@asynccontextmanager
async def http_server(directory):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (directory / "http_server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "finevolab.web:app", "--host", "127.0.0.1",
                                    "--port", str(port), "--no-access-log"], cwd=ROOT, stdout=log, stderr=log)
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=240) as client:
                for _ in range(120):
                    if process.poll() is not None:
                        raise RuntimeError("The local HTTP service exited before readiness")
                    try:
                        response = await client.get("/api/corpus", timeout=1)
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.25)
                else:
                    raise TimeoutError("The local HTTP service did not become ready")
                yield client
        finally:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)


async def measured_batch(call, cases, concurrency, repeat, arm):
    semaphore = asyncio.Semaphore(concurrency)
    async def measure(index, case):
        async with semaphore:
            started = time.perf_counter()
            row = {"arm": arm, "repeat": repeat, "concurrency": concurrency, "sequence_index": index, "case_index": CASES.index(case) if case in CASES else 0}
            try:
                data, expected, trace_id = await call(case)
                row.update(status="complete" if data == expected else "mismatch", output_sha256=digest(data), trace_id=trace_id)
            except Exception as error:
                row.update(status="timeout" if isinstance(error, (TimeoutError, httpx.TimeoutException)) else "failed",
                           error_type=type(error).__name__)
            row["elapsed_seconds"] = time.perf_counter() - started
            return row
    started = time.perf_counter()
    rows = await asyncio.gather(*(measure(index, case) for index, case in enumerate(cases)))
    return rows, time.perf_counter() - started


async def recovery(directory, repeats):
    records = []
    for failure in ("timeout", "connection"):
        for repeat in range(repeats):
            runner = PhaseRunner(directory / "recovery", failure + str(repeat), {"data": "fixed"})
            calls = []
            async def prepare(state):
                calls.append("prepare")
                return {"ready": True}
            async def interrupted(state):
                with observe([], "model", "injected_model_step"):
                    if failure == "timeout":
                        await asyncio.wait_for(asyncio.sleep(1), timeout=0.02)
                    else:
                        raise ConnectionError("Injected provider failure")
            started = time.perf_counter()
            try:
                await runner.run({"prepare": prepare, "analyze": interrupted}, timeout_seconds=10)
                raise AssertionError("The injected failure was not detected")
            except (TimeoutError, ConnectionError) as error:
                detected = type(error).__name__
            failed_seconds = time.perf_counter() - started
            async def repaired(state):
                with observe([], "tool", "get_financial_metrics"):
                    return {"value": 42}
            started = time.perf_counter()
            state = await runner.run({"prepare": prepare, "analyze": repaired})
            records.append({"failure": failure, "repeat": repeat, "detected": detected,
                "failure_seconds": failed_seconds, "recovery_seconds": time.perf_counter() - started,
                "status": state["status"], "prepare_calls": len(calls), "trace_id": state["trace_id"],
                "analyze_attempts": state["phases"]["analyze"]["attempts"]})
    return records


async def permissions(directory):
    checks = []
    async with financial_session(ROOT) as session:
        tools = (await session.list_tools()).tools
        checks.append({"name": "discovery", "passed": {t.name for t in tools} == set(SCHEMAS)})
        checks.append({"name": "readonly_annotations", "passed": all(t.annotations.readOnlyHint and not t.annotations.destructiveHint for t in tools)})
        checks.append({"name": "structured_output_schema", "passed": all(t.outputSchema for t in tools)})
        base = CASES[0]["request"]
        for label, change in (("string_year", {"fiscal_year": "2024"}), ("boolean_year", {"fiscal_year": True}),
                              ("invalid_year", {"fiscal_year": 0}), ("unknown_metric", {"metric": "price"}),
                              ("private_path", {"path": "private.ini"}), ("unknown_company", {"company": "UNKNOWN"}),
                              ("upload_scope", {"upload_ids": ["private"]})):
            result = await session.call_tool("get_financial_metrics", {"request": {**base, **change}})
            checks.append({"name": label, "passed": bool(result.isError)})
        result = await session.call_tool("build_report", {"request": base})
        checks.append({"name": "write_tool_denied", "passed": bool(result.isError)})
    policy = directory / "restricted_policy.json"
    write_json(policy, {"version": 1, "allowed_tools": ["get_financial_metrics"],
                       "allowed_companies": ["MSFT"], "retrieval_mode": "bm25"})
    async with financial_session(ROOT, policy) as session:
        tools = (await session.list_tools()).tools
        checks.append({"name": "restricted_discovery", "passed": [t.name for t in tools] == ["get_financial_metrics"]})
        result = await session.call_tool("get_financial_metrics", {"request": {**base, "company": "AAPL"}})
        checks.append({"name": "company_scope_denied", "passed": bool(result.isError)})
        result = await session.call_tool("calculate_metrics", {"request": base})
        checks.append({"name": "tool_scope_denied", "passed": bool(result.isError)})
        result = await call_financial_tool(session, "get_financial_metrics", {**base, "company": "Microsoft"})
        checks.append({"name": "authorized_alias", "passed": result["data"]["company"] == "MSFT"})
    policy.unlink()
    return checks


def trace_checks(rows, directory):
    identifiers = {row["trace_id"] for row in rows if row.get("trace_id")}
    groups = {identifier: [] for identifier in identifiers}
    for location in (directory / "traces", ROOT / "output/mcp/traces", ROOT / "output/traces"):
        for path in location.glob("*.json"):
            if path.name.split(".", 1)[0] not in identifiers:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            groups[data["trace_id"]].extend(data["spans"])
    errors = []
    for identifier, spans in groups.items():
        known = {s["span_id"] for s in spans}
        if not spans or any(s["status"] != "complete" or s.get("duration_seconds", -1) < 0 for s in spans):
            errors.append({"trace_id": identifier, "reason": "incomplete_spans"})
        if any(s["parent_span_id"] is not None and s["parent_span_id"] not in known for s in spans):
            errors.append({"trace_id": identifier, "reason": "missing_parent"})
    return {"requests": len(rows), "trace_ids": len(identifiers), "spans": sum(len(s) for s in groups.values()),
            "errors": errors, "passed": len(identifiers) == len(rows) and not errors}


async def run(args):
    if min(args.requests, args.repeats, *args.concurrency) < 1 or args.requests % len(CASES):
        raise ValueError("Use positive counts and a request count divisible by the case count")
    directory = args.output.resolve()
    if (directory / "results.json").exists():
        raise ValueError("The result directory already contains a completed run")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = identity(args)
    write_json(directory / "manifest.json", manifest)
    service = FinancialToolService()
    expected = [service.invoke(case["tool"], SCHEMAS[case["tool"]].model_validate(case["request"])) for case in CASES]
    rows, summaries, startups = [], [], []
    async def direct(case):
        with trace_scope("benchmark.direct", directory / "traces") as (trace, _):
            def invoke():
                with observe([], "tool", case["tool"]):
                    return service.invoke(case["tool"], SCHEMAS[case["tool"]].model_validate(case["request"]))
            data = await asyncio.to_thread(invoke)
            return data, expected[CASES.index(case)], trace.trace_id
    started = time.perf_counter()
    async with financial_session(ROOT) as session:
        startups.append({"component": "mcp", "seconds": time.perf_counter() - started})
        async def mcp(case):
            with trace_scope("benchmark.client", directory / "traces") as (trace, _):
                result = await call_financial_tool(session, case["tool"], case["request"])
                if result["trace_id"] != trace.trace_id:
                    raise ValueError("MCP trace context was not propagated")
                return result["data"], expected[CASES.index(case)], trace.trace_id
        for case in CASES:
            await direct(case)
            await mcp(case)
        for repeat in range(args.repeats):
            cases = CASES * (args.requests // len(CASES))
            random.Random(17 + repeat).shuffle(cases)
            for concurrency in args.concurrency:
                for arm, call in (("direct", direct), ("mcp_stdio", mcp)) if repeat % 2 == 0 else (("mcp_stdio", mcp), ("direct", direct)):
                    batch, wall = await measured_batch(call, cases, concurrency, repeat, arm)
                    rows.extend(batch)
                    summaries.append({"arm": arm, "concurrency": concurrency, "repeat": repeat, **summarize(batch, wall)})
                    print(json.dumps({"arm": arm, "concurrency": concurrency, "repeat": repeat,
                                      "requests": len(batch), "failures": sum(r["status"] != "complete" for r in batch)}), flush=True)
    started = time.perf_counter()
    async with http_server(directory) as client:
        startups.append({"component": "http", "seconds": time.perf_counter() - started})
        request = {"company": "MSFT", "metric": "operating_margin_change", "fiscal_year": 2024}
        target = service.store.derive("MSFT", request["metric"], 2024).model_dump(mode="json")
        async def http_call(case):
            response = await client.post("/api/derived-metrics", json=request)
            response.raise_for_status()
            parent = parse_traceparent(response.headers.get("traceparent"))
            if parent is None:
                raise ValueError("The HTTP response omitted trace context")
            return response.json(), target, parent[0]
        await http_call(None)
        for repeat in range(args.repeats):
            for concurrency in args.concurrency:
                batch, wall = await measured_batch(http_call, [None] * args.requests, concurrency, repeat, "http_tcp")
                rows.extend(batch)
                summaries.append({"arm": "http_tcp", "concurrency": concurrency, "repeat": repeat, **summarize(batch, wall)})
        real = []
        if args.real_model:
            questions = ["Calculate Microsoft revenue growth for fiscal year 2024.",
                         "Compare Apple and Microsoft revenue in fiscal year 2024 and state the fiscal-period differences.",
                         "Explain Microsoft's fiscal year 2024 revenue growth using the annual report and calculate the growth rate."]
            for question in questions:
                started = time.perf_counter()
                response = await client.post("/api/questions", json={"question": question})
                body = response.json()
                state = body if response.status_code == 200 else {}
                result = state.get("phases", {}).get("analyze", {}).get("result", {})
                real.append({"case_index": len(real), "http_status": response.status_code,
                    "status": state.get("status", "failed"), "elapsed_seconds": time.perf_counter() - started,
                    "trace_id": state.get("trace_id"), "task_id": state.get("task_id"), "usage": result.get("usage"),
                    "validation": state.get("phases", {}).get("validate", {}).get("result"),
                    "answer_status": result.get("answer", {}).get("status"),
                    "response_trace_id": (parse_traceparent(response.headers.get("traceparent")) or (None,))[0]})
                print(json.dumps({"arm": "real_model_smoke", "case_index": len(real)-1,
                                  "http_status": response.status_code, "status": state.get("status")}), flush=True)
    permission_results = await permissions(directory)
    faults = await recovery(directory, 5)
    graph = trace_checks(rows, directory)
    pooled = []
    for arm in ("direct", "mcp_stdio", "http_tcp"):
        for concurrency in args.concurrency:
            subset = [r for r in rows if r["arm"] == arm and r["concurrency"] == concurrency]
            wall = sum(s["wall_seconds"] for s in summaries if s["arm"] == arm and s["concurrency"] == concurrency)
            pooled.append({"arm": arm, "concurrency": concurrency, **summarize(subset, wall)})
    result = {"run_id": args.run_id, "cases": CASES, "records": rows, "summaries": summaries, "pooled": pooled,
              "startups": startups, "fault_injection": faults, "real_model_smoke": real,
              "permission_checks": permission_results, "trace_checks": graph,
              "checks": {"permissions_passed": all(row["passed"] for row in permission_results),
                         "trace_graph_complete": graph["passed"],
                         "source_unchanged": all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value for name, value in manifest["source_hashes"].items()),
                         "all_requests_equivalent": all(r["status"] == "complete" for r in rows),
                         "all_injected_failures_recovered": all(r["status"] == "complete" and r["prepare_calls"] == 1 and r["analyze_attempts"] == 2 and r["detected"] == ("TimeoutError" if r["failure"] == "timeout" else "ConnectionError") for r in faults),
                         "real_model_completed": all(r["status"] == "complete" and r["trace_id"] == r["response_trace_id"] for r in real) if real else None}}
    write_json(directory / "results.json", result)
    print(json.dumps({"run_id": args.run_id, "requests": len(rows), "checks": result["checks"]}), flush=True)
    if not all(value is not False for value in result["checks"].values()):
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="mcp-trace-v1")
    parser.add_argument("--output", type=Path, default=ROOT / "output/service_acceptance")
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 4, 8])
    parser.add_argument("--real-model", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
