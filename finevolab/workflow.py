"""Run version-bound financial analysis and report phases."""

import asyncio
import hashlib
import json
import time
from pathlib import Path

from finevolab.agent import analyze_revenue
from finevolab.config import ModelConfig
from finevolab.reporting import build_report
from finevolab.runtime import BudgetExceeded, PhaseRunner, RunBudget, fingerprint, read_json, write_json, observe
from dataclasses import asdict, replace
from finevolab.tools import FinancialStore
from finevolab.storage import save_task


ROOT = Path(__file__).resolve().parents[1]


async def run_question(task_id: str, question: str, settings: ModelConfig, candidate: dict,
                       root: Path = ROOT, events: list | None = None, telemetry: dict | None = None,
                       render: bool = True, timeout_seconds: int = 240, upload_ids: list[str] | None = None,
                       budget: RunBudget | None = None, corpus_profile: str = "contextual",
                       planner_backend: str = "api", retrieval_variant: str = "improved", review_mode: str | None = None) -> dict:
    from finevolab.evaluation import analyze_task
    from finevolab.reporting import build_analysis_report
    from finevolab.validation import validate_answer

    if not question.strip() or len(question) > 12000:
        raise ValueError("A question between 1 and 12000 characters is required")
    budget = budget or RunBudget(max_context_chars=60000)
    telemetry = telemetry if telemetry is not None else {}
    telemetry["checkpoint_root"] = str(root / "output/tasks" / task_id / "subtasks")
    if planner_backend not in {"api", "lora_bf16", "lora_nf4"}:
        raise ValueError("Unknown planner backend")
    online_planner = None
    if planner_backend != "api":
        from finevolab.planning import PlannerClient
        online_planner = PlannerClient()
    upload_ids = sorted(set(upload_ids or []))
    store = (FinancialStore.from_uploads(root / "data/processed/reports.json", upload_ids, root / "data/uploads")
             if upload_ids else FinancialStore(root / "data/processed/reports.json"))
    from finevolab.retrieval_settings import load_settings
    from finevolab.reviewing import ReviewSettings, review_mode as configured_review_mode
    review_mode = ReviewSettings(mode=review_mode or configured_review_mode(root)).mode
    from finevolab.methods import load_analysis_settings
    versions = {"analysis_extensions": load_analysis_settings(root).model_dump(),
        "analysis_methods": hashlib.sha256((ROOT / "configs/analysis_methods.json").read_bytes()).hexdigest(),
        "retrieval_services": load_settings(root).model_dump(mode="json"), "review_mode": review_mode,
        "workflow": "question", "question": question, "company": "MULTI", "fiscal_year": None,
        "model": settings.model, "base_url": settings.base_url, "data_version": store.version,
        "candidate": fingerprint(candidate), "render": render, "timeout_seconds": timeout_seconds,
        "upload_ids": upload_ids, "source_signature": store.source_signature, "budget": asdict(budget),
        "corpus_profile": corpus_profile, "retrieval_variant": retrieval_variant, "runtime_backend": "agents_sdk" if online_planner is None else "lora_planner_with_sdk_narrative",
        "planner_backend": planner_backend,
        "planner_identity": online_planner.identity(planner_backend) if online_planner else None,
        "company_aliases": hashlib.sha256((ROOT / "configs/company_aliases.json").read_bytes()).hexdigest(),
        "processed": hashlib.sha256((root / "data/processed/reports.json").read_bytes()).hexdigest(),
        "sources": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (ROOT / "finevolab").glob("*.py")},
        "dependencies": hashlib.sha256((ROOT / "requirements/development.lock.txt").read_bytes()).hexdigest(),
        "template": hashlib.sha256((ROOT / "finevolab/templates/analysis.html").read_bytes()).hexdigest(),
        "passage_sources": hashlib.sha256((ROOT / "data/filing_sources.json").read_bytes()).hexdigest(),
        "passage_index": hashlib.sha256((ROOT / "data/indexes" /
            ("contextual" if corpus_profile == "contextual" else "") / "structured.json").read_bytes()).hexdigest(),
        "reranker_config": hashlib.sha256((ROOT / "configs/reranker.json").read_bytes()).hexdigest(),
        "retrieval_config": hashlib.sha256((ROOT / "configs/retrieval.json").read_bytes()).hexdigest(),
        "vector_metadata": hashlib.sha256((ROOT / "data/indexes" /
            ("contextual" if corpus_profile == "contextual" else "") / "structured.metadata.json").read_bytes()).hexdigest(),
    }
    async def prepare(state):
        key = fingerprint({"data": store.version, "processed": versions["processed"], "question": question,
                           "source": store.source_signature, "uploads": upload_ids})
        path = root / "output/cache" / (key + ".json")
        hit = path.exists()
        if not hit:
            write_json(path, {"question": question, "data_version": store.version, "upload_ids": upload_ids})
        return {"input": json.loads(path.read_text(encoding="utf-8")), "cache_hit": hit}
    async def analyze(state):
        if online_planner is None:
            result = await analyze_task(settings, store, question, candidate, events, telemetry=telemetry,
                                        root=root, budget=budget, corpus_profile=corpus_profile, retrieval_variant=retrieval_variant, review_mode=review_mode)
        else:
            from finevolab.grounded import analyze_grounded
            from finevolab.planning import PlannerServiceError
            observed = telemetry if telemetry is not None else {}
            if budget.max_model_calls < 1 or budget.max_total_tokens < 1:
                raise BudgetExceeded("No planning budget remains")
            started = time.monotonic()
            documents = [{key: item[key] for key in ("company", "report_year", "id")} for item in store.uploads]
            try:
                with observe(events if events is not None else [], "model", "RequestPlan",
                             model=versions["planner_identity"]["model"], backend=planner_backend) as operation:
                    plan, prediction = await online_planner.plan(planner_backend, question, documents, candidate,
                                                                  budget.timeout_seconds)
                    operation["tokens"] = prediction["usage"]["total_tokens"]
            except PlannerServiceError as error:
                if error.usage is not None:
                    observed["usage"] = error.usage
                else:
                    observed["unknown_usage"] = True
                raise
            planning_seconds = time.monotonic() - started
            plan_usage = prediction["usage"]
            observed["usage"] = plan_usage.copy()
            if plan_usage["total_tokens"] >= budget.max_total_tokens or planning_seconds >= budget.timeout_seconds:
                raise BudgetExceeded("Planning exhausted the task budget")
            remaining = replace(budget, max_model_calls=budget.max_model_calls - 1,
                max_total_tokens=budget.max_total_tokens - plan_usage["total_tokens"],
                timeout_seconds=budget.timeout_seconds - planning_seconds)
            try:
                result = await analyze_grounded(settings, store, question, candidate, events, remaining,
                    observed, root=root, request_plan=plan, corpus_profile=corpus_profile, retrieval_variant=retrieval_variant, review_mode=review_mode)
            finally:
                narrative_usage = observed.get("usage", {})
                observed["usage"] = {key: narrative_usage.get(key, 0) + value for key, value in plan_usage.items()}
                observed["planner"] = prediction["identity"]
            result["model_usage"] = [{"role": "planner", "model": prediction["identity"]["model"], "usage": plan_usage},
                {"role": "narrative", "model": settings.model, "usage": result["usage"].copy()}]
            result["usage"] = observed["usage"].copy()
            result["runtime_backend"] = "lora_planner_with_sdk_narrative"
            result["planner"] = {**prediction["identity"], "elapsed_seconds": planning_seconds,
                                 "peak_allocated_bytes": prediction.get("peak_allocated_bytes"),
                                 "service_request_id": prediction.get("request_id")}
            result["elapsed_seconds"] += planning_seconds
        if result.get("answer_type") not in {"explanation", "hybrid"}:
            result["evidence"] = [store.evidence[key] for key in result["answer"]["evidence_ids"] if key in store.evidence]
        return result
    async def validate(state):
        return validate_answer(store, state["phases"]["analyze"]["result"])
    async def report(state):
        if not render:
            return {"status": "not_requested"}
        result = state["phases"]["analyze"]["result"]
        if result.get("tool_calls", 0) >= budget.max_tool_calls:
            raise BudgetExceeded("Tool call budget exceeded before report generation")
        result["tool_calls"] = result.get("tool_calls", 0) + 1
        result.setdefault("events", []).append({"event": "tool_execution", "tool": "build_report",
                                               "call": result["tool_calls"], "cache_hit": False})
        with observe(result["events"], "tool", "build_report", call=result["tool_calls"]):
            return await asyncio.to_thread(build_analysis_report, store, question,
                                           result, root / "output/tasks" / task_id / "report")
    runner = PhaseRunner(root / "output/tasks", task_id, versions)
    try:
        return await runner.run({"prepare": prepare, "analyze": analyze, "validate": validate, "report": report},
                                timeout_seconds=min(timeout_seconds, budget.timeout_seconds))
    finally:
        if runner.path.exists():
            save_task(read_json(runner.path))


async def run_task(task_id: str, company: str, year: int, mode: str = "offline") -> dict:
    if mode not in {"offline", "agent"}:
        raise ValueError("Unsupported analysis mode")
    store = FinancialStore(ROOT / "data/processed/reports.json")
    settings = ModelConfig.load() if mode == "agent" else None
    versions = {
        "company": company, "fiscal_year": year, "mode": mode, "data_version": store.version,
        "company_aliases": hashlib.sha256((ROOT / "configs/company_aliases.json").read_bytes()).hexdigest(),
        "processed_sha256": hashlib.sha256((ROOT / "data/processed/reports.json").read_bytes()).hexdigest(),
        "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "finevolab").glob("*.py")},
        "templates": hashlib.sha256((ROOT / "finevolab/templates/report.html").read_bytes()).hexdigest(),
        "model": settings.model if settings else None, "base_url": settings.base_url if settings else None,
        "dependencies": hashlib.sha256((ROOT / "requirements/development.lock.txt").read_bytes()).hexdigest(),
        "prompt": hashlib.sha256((ROOT / "prompts/default.json").read_bytes()).hexdigest(),
    }
    cache_key = fingerprint({"company": company, "year": year, "data_version": store.version,
                             "processed": versions["processed_sha256"], "tool": versions["sources"]["tools.py"]})

    async def prepare(state):
        cached = ROOT / "output/cache" / (cache_key + ".json")
        if cached.exists():
            return {"calculation": json.loads(cached.read_text()), "cache_hit": True}
        result = store.growth(company, "revenue", year).model_dump(mode="json")
        write_json(cached, result)
        return {"calculation": result, "cache_hit": False}

    async def analyze(state):
        if settings:
            return await analyze_revenue(settings, store, company, year)
        calculation = state["phases"]["prepare"]["result"]["calculation"]
        return {"mode": "deterministic", "calculation": calculation,
                "evidence": [store.evidence[key] for key in calculation["evidence_ids"]],
                "usage": {"requests": 0, "total_tokens": 0}}

    async def report(state):
        return await asyncio.to_thread(build_report, store, company, year, ROOT / "output/tasks" / task_id / "report")

    runner = PhaseRunner(ROOT / "output/tasks", task_id, versions)
    try:
        return await runner.run({"prepare": prepare, "analyze": analyze, "report": report})
    finally:
        if runner.path.exists():
            save_task(read_json(runner.path))
