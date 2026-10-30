"""Run paired context, recovery, concurrency, source, and scenario evaluations."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal, localcontext
import difflib
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
from threading import Barrier, Lock
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel
from finevolab.config import ModelConfig
from finevolab.context_encoding import encode_context, decode_context, GUIDANCE
from finevolab.grounded import RequestPlan, analyze_grounded
from finevolab.methods import AnalysisSettings
from finevolab.provenance import file_digest
from finevolab.reconciliation import SourceObservation, reconcile
from finevolab.retrieval import explanation_passages
from finevolab.runtime import RunBudget, ToolExecutor, fingerprint, read_json, write_json
from finevolab.scenarios import ScenarioRequest, calculate_scenarios, validate_scenarios
from finevolab.subtasks import SubtaskJournal
from finevolab.tools import FinancialStore
from finevolab.validation import validate_answer


class Step(BaseModel):
    company: str
    value: str


def freeze_inputs(path, store):
    if path.exists():
        return read_json(path)
    controls = []
    for company in ("MSFT", "AAPL", "GOOGL"):
        metric = store.get_metric(company, "revenue", 2024).model_dump(mode="json")
        def row(identifier, **kwargs):
            return SourceObservation(metric=deepcopy(metric), source_id=identifier, filing_id=identifier, **kwargs).model_dump(mode="json")
        a, b = row("a", disclosed_on="2024-06-30"), row("b", disclosed_on="2025-06-30")
        cases = []
        def add(name, rows, expected):cases.append({"id": company + ":" + name, "observations": deepcopy(rows), "expected": expected})
        add("single", [a], "single_source")
        add("corroborated", [a, b], "corroborated")
        c = deepcopy(b);c["filing_id"] = "a";add("same_filing", [a, c], "same_filing")
        for field, value in (("period_end", "2024-12-31" if company == "MSFT" else "2024-06-30"),
                             ("currency", "EUR"), ("name", "net_income"), ("company", "OTHER"), ("fiscal_year", 2023)):
            c = deepcopy(b);c["metric"][field] = value;add(field, [a, c], "not_comparable")
        c = deepcopy(b);c["metric"]["value"] = str(Decimal(metric["value"]) / 1000000)
        c["metric"]["unit"] = "USD_millions";c["scale"] = "1000000";add("scaled", [a, c], "corroborated")
        c = deepcopy(b);c["metric"]["value"] = str(Decimal(metric["value"]) + Decimal("0.4"));c["rounding_unit"] = "1";add("rounding", [a, c], "corroborated")
        c = deepcopy(b);c["metric"]["value"] = str(Decimal(metric["value"]) + 1000);add("conflict", [a, c], "conflict")
        c["restated"] = True;add("restated", [a, c], "restated")
        c["filing_id"] = "a";add("same_filing_conflict", [a, c], "conflict")
        c = deepcopy(b);c["metric"]["unit"] = "unknown";add("unit", [a, c], "not_comparable")
        controls.extend(cases)
    live = []
    for company, topic, metrics in (("MSFT", "profit", ["revenue", "operating_income", "net_income"]),
             ("AAPL", "profit", ["revenue", "operating_income", "net_income"]),
             ("MSFT", "risk", ["revenue"]), ("AAPL", "revenue", ["revenue"])):
        question = (f"Explain how {company} revenue, operating income and net income changed in FY2024; "
                    "cite exact annual-report passages and separate disclosed drivers from inference.") if len(metrics) == 3 else (
                    f"Describe {company}'s disclosed FY2024 business risks with exact report passages." if topic == "risk" else
                    f"Explain {company}'s FY2024 revenue changes using exact annual-report passages.")
        plan = RequestPlan(intent="explanation", companies=[company], fiscal_year=2024, report_year=2024, metrics=metrics, topic=topic)
        passages = explanation_passages(ROOT, question, company, 2024, topic, "improved", "contextual", [], metrics[0] if len(metrics) == 1 else None)
        live.append({"id": company + ":" + topic, "question": question, "plan": plan.model_dump(), "passages": passages})
    inputs = {"version": "analysis_extensions_v1", "data_version": store.version, "source_signature": store.source_signature,
              "source_controls": controls, "model_cases": live, "repeats": 2, "concurrency": 8, "cold_repeats": 5,
              "revenue_assumptions_percent": [-10, 0, 10], "margin_assumptions_percentage_points": [-2, 0, 2]}
    write_json(path, inputs)
    return inputs


def offline(inputs, store, output):
    workspace = ROOT / "output/analysis_extensions" / output.name / file_digest(Path(__file__).resolve())[:12]
    controls = []
    for case in inputs["source_controls"]:
        rows = [SourceObservation.model_validate(item) for item in case["observations"]]
        baseline = "single_source" if len(rows) == 1 else "corroborated" if len({r.metric.value for r in rows}) == 1 else "conflict"
        result = reconcile(rows)
        controls.append({"id": case["id"], "expected": case["expected"], "baseline": baseline,
                         "coordinated": result["status"], "detail": result})
    assert all(row["expected"] == row["coordinated"] for row in controls)
    scenario_rows = []
    for company in sorted({m.company for m in store.metrics}):
        for margin in ("operating_margin", "net_margin"):
            request = ScenarioRequest(company=company, fiscal_year=2024, margin=margin,
                revenue_changes_percent=inputs["revenue_assumptions_percent"], margin_changes_percentage_points=inputs["margin_assumptions_percentage_points"])
            result = validate_scenarios(store, calculate_scenarios(store, request))
            profit, revenue = map(lambda item: Fraction(item.value), result.baseline.inputs)
            for point in result.points:
                expected = (profit + revenue * Fraction(point.margin_change_percentage_points) / 100) * (1 + Fraction(point.revenue_change_percent) / 100)
                with localcontext() as context:
                    context.prec = 50
                    reference = Decimal(expected.numerator) / Decimal(expected.denominator)
                    correct = abs(point.profit - reference) <= Decimal("1e-20")
                assert correct
                scenario_rows.append({"company": company, "margin": margin, **point.model_dump(mode="json"), "reference_profit": str(reference), "correct": correct})
    context = []
    for case in inputs["model_cases"]:
        metrics = [store.get_metric(case["plan"]["companies"][0], name, year).model_dump(mode="json")
                   for year in (2023, 2024) for name in case["plan"]["metrics"]]
        payload = {"question": case["question"], "observations": metrics, "passages": case["passages"]}
        encoded, stats = encode_context(payload)
        assert decode_context(encoded) == payload
        context.append({"id": case["id"], **stats, "instruction_overhead_chars": len(GUIDANCE) if stats["observation_rows"] else 0})
    recovery = []
    for repeat in range(inputs["cold_repeats"]):
        for enabled in (False, True):
            calls, failed = [], False
            journal = SubtaskJournal(workspace / "recovery" / str(repeat) / str(enabled), {"source": store.source_signature}, RunBudget()) if enabled else None
            def execute():
                nonlocal failed
                values = []
                for company in ("MSFT", "AAPL", "GOOGL"):
                    value = journal.load(company, Step) if journal else None
                    if value is None:
                        calls.append(company)
                        value = Step(company=company, value=str(store.get_metric(company, "revenue", 2024).value))
                        if journal:journal.save(company, value)
                    values.append(value.model_dump())
                    if company == "AAPL" and not failed:
                        failed = True
                        raise RuntimeError("Controlled completed-subtask interruption")
                return values
            try:execute()
            except RuntimeError:pass
            result = execute()
            recovery.append({"repeat": repeat, "enabled": enabled, "executions": len(calls), "result": result})
    concurrency = []
    for repeat in range(inputs["cold_repeats"]):
        for enabled in (False, True):
            gate, lock = Barrier(inputs["concurrency"]), Lock()
            calls = 0
            cache = workspace / "concurrency" / str(repeat) / str(enabled)
            def worker(index):
                nonlocal calls
                executor = ToolExecutor(RunBudget(), [], {"source": store.source_signature}, cache, coalesce=enabled)
                def compute():
                    nonlocal calls
                    with lock:calls += 1
                    return {"digest": hashlib.pbkdf2_hmac("sha256", b"fixed-payload", b"fixed-salt", 60000).hex()}
                gate.wait()
                return executor.call("controlled_cpu_work", {"query": "same"}, compute)
            started = time.perf_counter()
            cpu_started = time.process_time()
            with ThreadPoolExecutor(max_workers=inputs["concurrency"]) as pool:results = list(pool.map(worker, range(inputs["concurrency"])))
            assert all(item == results[0] for item in results)
            concurrency.append({"repeat": repeat, "enabled": enabled, "requests": len(results), "computations": calls,
                                "elapsed_seconds": time.perf_counter() - started, "cpu_seconds": time.process_time() - cpu_started, "result": results[0]})
    write_json(output / "offline.json", {"source_controls": controls, "scenarios": scenario_rows, "context": context,
                                        "recovery": recovery, "concurrency": concurrency})
    return {"source_controls": {"cases": len(controls), "value_only_correct": sum(r["baseline"] == r["expected"] for r in controls),
                                "coordinated_correct": len(controls)},
            "scenario_reference_checks": {"correct": len(scenario_rows), "cases": len(scenario_rows)},
            "context": context, "recovery": [{"enabled": flag, "executions": sum(r["executions"] for r in recovery if r["enabled"] == flag)} for flag in (False, True)],
            "concurrency": [{"enabled": flag, "requests": sum(r["requests"] for r in concurrency if r["enabled"] == flag),
                "computations": sum(r["computations"] for r in concurrency if r["enabled"] == flag),
                "median_seconds": statistics.median(r["elapsed_seconds"] for r in concurrency if r["enabled"] == flag),
                "cpu_seconds": sum(r["cpu_seconds"] for r in concurrency if r["enabled"] == flag)} for flag in (False, True)]}


async def live(inputs, store, output):
    settings = ModelConfig.load()
    arms = {"baseline": (False, False), "encoding": (True, False), "methods": (False, True), "combined": (True, True)}
    path = output / "model_records.json"
    records = read_json(path) if path.exists() else []
    done = {(r["case_id"], r["repeat"], r["arm"]) for r in records}
    schedule = [(case, repeat, arm) for repeat in range(inputs["repeats"]) for case in inputs["model_cases"] for arm in arms]
    random.Random(17).shuffle(schedule)
    for case, repeat, arm in schedule:
        key = (case["id"], repeat, arm)
        if key in done:continue
        encoding, methods = arms[arm]
        flags = AnalysisSettings(context_encoding=encoding, method_selection=methods, subtask_recovery=False)
        started = time.perf_counter()
        telemetry = {}
        record = {"case_id": case["id"], "repeat": repeat, "arm": arm, "model": settings.model, "temperature": 0, "max_output_tokens": 1600}
        try:
            with patch("finevolab.retrieval.explanation_passages", return_value=case["passages"]):
                result = await analyze_grounded(settings, store, case["question"], {"system": "Use source-bound financial analysis."},
                    telemetry=telemetry, budget=RunBudget(max_context_chars=60000), corpus_profile="contextual",
                    request_plan=RequestPlan.model_validate(case["plan"]), allow_repair=False, extensions=flags)
            validation = validate_answer(store, result)
            record.update(mechanical_passed=result["answer"]["status"] == "complete", validation=validation,
                          usage=result["usage"], result=result)
        except Exception as error:
            record.update(mechanical_passed=False, error_type=type(error).__name__, error=str(error)[:500],
                          usage=telemetry.get("usage", {}), messages=telemetry.get("messages", []))
        record["elapsed_seconds"] = time.perf_counter() - started
        records.append(record);write_json(path, records)
        print(json.dumps({"completed": len(records), "planned": len(schedule), "case": case["id"], "arm": arm,
                          "passed": record["mechanical_passed"]}), flush=True)
    return {arm: {"tasks": sum(r["arm"] == arm for r in records),
        "mechanical_passed": sum(r["mechanical_passed"] for r in records if r["arm"] == arm),
        "model_requests": sum(r["usage"].get("requests", 0) for r in records if r["arm"] == arm),
        "input_tokens": sum(r["usage"].get("input_tokens", 0) for r in records if r["arm"] == arm),
        "output_tokens": sum(r["usage"].get("output_tokens", 0) for r in records if r["arm"] == arm),
        "mean_seconds": statistics.mean(r["elapsed_seconds"] for r in records if r["arm"] == arm)} for arm in arms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args();args.output.mkdir(parents=True, exist_ok=True)
    store = FinancialStore(ROOT / "data/processed/reports.json")
    inputs_path = ROOT / "data/evaluation/analysis_extensions/cases.json"
    inputs = freeze_inputs(inputs_path, store)
    sources = [*sorted((ROOT / "finevolab").glob("*.py")), Path(__file__).resolve(), ROOT / "configs/analysis_extensions.json", ROOT / "configs/analysis_methods.json",
               ROOT / "scripts/review_analysis_extensions.py", ROOT / "scripts/calculate_derived.py",
               ROOT / "tests/test_analysis_extensions.py", ROOT / "tests/test_derived.py", ROOT / "finevolab/templates/analysis.html"]
    import subprocess
    manifest = {"run_id": args.output.name, "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_hashes": {p.relative_to(ROOT).as_posix(): file_digest(p) for p in sources}, "input_sha256": file_digest(inputs_path),
        "data_version": store.version, "source_signature": store.source_signature, "configuration": read_json(ROOT / "configs/analysis_extensions.json"),
        "model_parameters": {"temperature": 0, "max_output_tokens": 1600, "repair": False, "fixed_request_plans": True},
        "runtime": "CPU", "model": ModelConfig.load().model if args.live else None}
    identity = {k: v for k, v in manifest.items() if k != "model"}
    path = args.output / "manifest.json"
    if path.exists():
        previous = read_json(path)
        if {k: v for k, v in previous.items() if k not in {"model", "source_patch_sha256"}} != identity:raise ValueError("The run identity changed; use another output directory")
    patch_parts = []
    for source in [*sources, inputs_path]:
        name = source.relative_to(ROOT).as_posix()
        base = subprocess.run(["git", "show", manifest["code_commit"] + ":" + name], cwd=ROOT, capture_output=True)
        before = base.stdout.decode("utf-8").replace("\r\n", "\n") if base.returncode == 0 else ""
        after = source.read_text(encoding="utf-8")
        if before != after:
            header = "diff --git a/" + name + " b/" + name + "\n" + ("new file mode 100644\n" if base.returncode else "")
            def physical_lines(text):
                parts = text.split("\n")
                return [line + "\n" for line in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
            diff = difflib.unified_diff(physical_lines(before), physical_lines(after),
                fromfile="a/" + name if base.returncode == 0 else "/dev/null", tofile="b/" + name)
            patch_parts.append(header + "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff))
    patch_content = "".join(patch_parts)
    manifest["source_patch_sha256"] = hashlib.sha256(patch_content.encode()).hexdigest()
    if path.exists():
        previous = read_json(path)
        if previous.get("source_patch_sha256") not in {None, manifest["source_patch_sha256"]}:
            raise ValueError("The archived source patch changed")
    (args.output / "source.patch").write_text(patch_content, encoding="utf-8", newline="\n")
    write_json(path, manifest)
    summary = read_json(args.output / "summary.json") if (args.output / "summary.json").exists() else {}
    if "offline" not in summary:summary["offline"] = offline(inputs, store, args.output)
    if args.live:summary["model_ablation"] = asyncio.run(live(inputs, store, args.output))
    summary["metric_scope"] = "Paired engineering controls and source/schema acceptance; model cases use fixed plans and evidence."
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":main()
