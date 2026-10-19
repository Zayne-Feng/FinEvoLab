"""Run paired shared-capability comparisons with pinned upstream report components."""

import argparse
import asyncio
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.config import ModelConfig
from finevolab.experiments import manifest, sha256
from finevolab.grounded import analyze_grounded
from finevolab.runtime import fingerprint, write_json
from finevolab.tools import FinancialStore
from finevolab.upstream_baseline import analyze_upstream


def normalize_current(output):
    calculation = output["answer"]["calculations"][0]
    values = sorted(calculation["inputs"], key=lambda row: row["fiscal_year"])
    return {"revenue_previous": values[0]["value"], "revenue_current": values[1]["value"],
            "growth_percent": calculation["value"]}


def scoped_store(store, case):
    scoped = FinancialStore(store.path)
    scoped.metrics = [store.get_metric(case["company"], metric, year)
        for year in (case["fiscal_year"] - 1, case["fiscal_year"])
        for metric in ("revenue", "operating_income", "net_income")]
    scoped.evidence = {metric.evidence_id: store.evidence[metric.evidence_id] for metric in scoped.metrics}
    scoped.version = fingerprint({"metrics": [m.model_dump(mode="json") for m in scoped.metrics]})
    scoped.source_signature = fingerprint({"source": store.source_signature, "scope": scoped.version})
    return scoped


def score(values, store, case):
    current = store.get_metric(case["company"], "revenue", case["fiscal_year"]).value
    previous = store.get_metric(case["company"], "revenue", case["fiscal_year"] - 1).value
    expected = (current / previous - 1) * 100
    return {"current_amount": Decimal(values["revenue_current"]) == current,
        "previous_amount": Decimal(values["revenue_previous"]) == previous,
        "displayed_growth": Decimal(values["growth_percent"]).quantize(Decimal(".1"), rounding=ROUND_HALF_UP)
            == expected.quantize(Decimal(".1"), rounding=ROUND_HALF_UP)}


async def run(args):
    dataset = ROOT / "data/evaluation/followup_v1/common.json"
    frozen = json.loads((dataset.parent / "manifest.json").read_text(encoding="utf-8"))
    if sha256(dataset) != frozen["files"][dataset.name]:
        raise ValueError("The shared task split changed")
    cases = json.loads(dataset.read_text(encoding="utf-8"))["tasks"]
    if args.limit:
        cases = cases[:args.limit]
    directory = ROOT / "project_notes/experiments" / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    settings, store = ModelConfig.load(), FinancialStore(ROOT / "data/processed/reports.json")
    candidate = json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8"))
    configuration = {"dataset_sha256": sha256(dataset), "repeats": args.repeats, "limit": args.limit,
        "model": settings.model, "base_url": settings.base_url, "thinking": settings.extra_body, "temperature": 0, "max_tokens": 1600,
        "candidate_sha256": sha256(ROOT / "prompts/benchmark.json"), "runner_sha256": sha256(Path(__file__)),
        "data_version": store.version, "upstream_commit": "2717499b8e30f242640af08c4ad9afd1113c2d45",
        "source_hashes": {path.relative_to(ROOT).as_posix(): sha256(path) for path in
            (ROOT / "finevolab").glob("*.py")}, "primary": "shared report amounts and one-decimal annual revenue change"}
    path = directory / "manifest.json"
    if path.exists() and json.loads(path.read_text(encoding="utf-8"))["config"] != configuration:
        raise ValueError("The experiment changed; use a new run ID")
    if not path.exists():
        write_json(path, manifest(ROOT, args.run_id, configuration))
    path = directory / "tasks.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    done = {(row["case_id"], row["repeat"], row["arm"]) for row in rows}
    for repeat in range(args.repeats):
        for index, case in enumerate(cases):
            selected = scoped_store(store, case)
            arms = ["upstream", "current"] if (repeat + index) % 2 else ["current", "upstream"]
            for arm in arms:
                key = (case["id"], repeat, arm)
                if key in done:
                    continue
                started = time.monotonic()
                row = {"case_id": case["id"], "company": case["company"], "fiscal_year": case["fiscal_year"],
                       "repeat": repeat, "arm": arm, "data_scope": selected.version,
                       "source_signature": selected.source_signature,
                       "observable_metrics": [m.model_dump(mode="json") for m in selected.metrics]}
                try:
                    output = await asyncio.wait_for(analyze_upstream(ROOT, settings, selected, case["company"], case["fiscal_year"])
                        if arm == "upstream" else analyze_grounded(settings, selected, case["question"], candidate), timeout=180)
                    values = output["normalized"] if arm == "upstream" else normalize_current(output)
                    checks = score(values, store, case)
                    row.update(output=output, usage=output["usage"], usage_complete=True,
                        checks=checks, score=int(all(checks.values())), execution_status="completed")
                except Exception as error:
                    row.update(score=0, execution_status="failed", usage_complete=False,
                        error_type=type(error).__name__, error=str(error)[:500])
                row["elapsed_seconds"] = round(time.monotonic() - started, 4)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows.append(row)
                done.add(key)
                summary = {arm: {"attempted": sum(r["arm"] == arm for r in rows),
                    "successes": sum(r["score"] for r in rows if r["arm"] == arm)} for arm in ("upstream", "current")}
                write_json(directory / "summary.json", {"groups": summary, "planned": len(cases) * args.repeats * 2})
                print(json.dumps({"case": case["id"], "arm": arm, "score": row["score"], "error": row.get("error_type")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--limit", type=int)
    asyncio.run(run(parser.parse_args()))
