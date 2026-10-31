"""Compare financial request latency with controlled trace collection and export."""

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

from benchmark_services import CASES, SCHEMAS, identity, measured_batch
from finevolab.mcp_client import financial_session, call_financial_tool
from finevolab.mcp_server import FinancialToolService
from finevolab.runtime import observe, write_json
from finevolab.service_metrics import summarize
from finevolab.tracing import trace_scope, tracing_mode, MODES, flush_exports


async def run(args):
    if args.requests < 1 or args.requests % len(CASES) or args.repeats < 1 or min(args.concurrency) < 1:
        raise ValueError("Positive counts and a request count divisible by the case count are required")
    if not set(args.modes) <= MODES:
        raise ValueError("Unknown trace mode")
    directory = args.output.resolve()
    if (directory / "results.json").exists():
        raise ValueError("Completed measurements require a new output directory")
    directory.mkdir(parents=True, exist_ok=True)
    args.real_model = False
    manifest = identity(args)
    manifest["source_hashes"]["scripts/benchmark_trace_modes.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest["config"].pop("trace_export", None)
    manifest["config"].update(modes=args.modes, response_latency_includes_synchronous_export=True,
                               batch_flush_measured_separately=True)
    write_json(directory / "manifest.json", manifest)
    store = FinancialToolService()
    expected = [store.invoke(case["tool"], SCHEMAS[case["tool"]].model_validate(case["request"])) for case in CASES]
    rows, summaries, startup, drains = [], [], [], []
    for repeat in range(args.repeats):
        modes = list(args.modes)
        random.Random(17 + repeat).shuffle(modes)
        for mode in modes:
            with tracing_mode(mode):
                started = time.perf_counter()
                async with financial_session(ROOT) as session:
                    startup.append({"mode": mode, "repeat": repeat, "seconds": time.perf_counter() - started})
                    async def direct(case):
                        with trace_scope("load.direct", directory / "traces") as (trace, _):
                            def compute():
                                with observe([], "tool", case["tool"]):
                                    return store.invoke(case["tool"], SCHEMAS[case["tool"]].model_validate(case["request"]))
                            value = await asyncio.to_thread(compute)
                            return value, expected[CASES.index(case)], trace.trace_id
                    async def mcp(case):
                        with trace_scope("load.client", directory / "traces") as (trace, _):
                            result = await call_financial_tool(session, case["tool"], case["request"])
                            if result["trace_mode"] != mode or result["trace_id"] != trace.trace_id:
                                raise ValueError("The server trace configuration or correlation differs")
                            return result["data"], expected[CASES.index(case)], trace.trace_id
                    for case in CASES:
                        await direct(case)
                        await mcp(case)
                    cases = CASES * (args.requests // len(CASES))
                    random.Random(29 + repeat).shuffle(cases)
                    for concurrency in args.concurrency:
                        arms = [("direct", direct), ("mcp", mcp)]
                        if repeat % 2:
                            arms.reverse()
                        for arm, call in arms:
                            batch, wall = await measured_batch(call, cases, concurrency, repeat, arm)
                            for row in batch:
                                row["trace_mode"] = mode
                            rows.extend(batch)
                            drained = time.perf_counter()
                            stats = await asyncio.to_thread(flush_exports)
                            server = await call_financial_tool(session, CASES[0]["tool"], CASES[0]["request"], flush_trace=True)
                            drain_seconds = time.perf_counter() - drained
                            drains.append({"mode": mode, "arm": arm, "concurrency": concurrency, "repeat": repeat,
                                           "seconds": drain_seconds, "client": stats, "server": server["export_stats"]})
                            summaries.append({"arm": arm, "trace_mode": mode, "repeat": repeat,
                                              "concurrency": concurrency, "drain_seconds": drain_seconds,
                                              "durable_throughput_requests_per_second": len(batch) / (wall + drain_seconds),
                                              **summarize(batch, wall)})
                            write_json(directory / "progress.json", {"records": rows, "summaries": summaries})
                            print(json.dumps({"arm": arm, "trace_mode": mode, "repeat": repeat,
                                "concurrency": concurrency, "failures": sum(r["status"] != "complete" for r in batch)}), flush=True)
    pooled = []
    for mode in args.modes:
        for arm in ("direct", "mcp"):
            for concurrency in args.concurrency:
                group = [r for r in rows if (r["trace_mode"], r["arm"], r["concurrency"]) == (mode, arm, concurrency)]
                wall = sum(s["wall_seconds"] for s in summaries if (s["trace_mode"], s["arm"], s["concurrency"]) == (mode, arm, concurrency))
                pooled.append({"trace_mode": mode, "arm": arm, "concurrency": concurrency, **summarize(group, wall)})
    result = {"run_id": args.run_id, "records": rows, "summaries": summaries, "pooled": pooled,
              "startup": startup, "drains": drains,
              "all_exports_drained": all(item["client"]["pending"] == item["server"]["pending"] == 0 and item["client"]["failed"] == item["server"]["failed"] == 0 for item in drains),
              "all_equivalent": all(r["status"] == "complete" for r in rows)}
    write_json(directory / "results.json", result)
    (directory / "progress.json").unlink(missing_ok=True)
    if not result["all_equivalent"]:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "output/trace_controls")
    parser.add_argument("--run-id", default="trace-controls-v1")
    parser.add_argument("--modes", nargs="+", default=["off", "memory", "file"])
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 4, 8])
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
