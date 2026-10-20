"""Compare bounded SDK repair on identical malformed planning responses."""

import argparse
import asyncio
import copy
import json
from pathlib import Path
import sys
from unittest.mock import patch

from openai.types.chat import ChatCompletion

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.config import ModelConfig
from finevolab.experiments import manifest, sha256
from finevolab.grounded import analyze_grounded
from finevolab.provider import async_client
from finevolab.runtime import write_json
from finevolab.scope_evaluation import assess_scope
from finevolab.tools import FinancialStore
from run_scope_experiments import append


class InjectedClient:
    def __init__(self, client, raw, requests):
        self.client, self.raw, self.requests = client, raw, requests
        self.base_url, self.chat, self.completions = client.base_url, self, self
        self.first = True

    async def __aenter__(self):
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.client.__aexit__(*args)

    async def create(self, **kwargs):
        if self.first:
            self.first = False
            return ChatCompletion(id="injected-plan", created=0, model=kwargs["model"], object="chat.completion",
                choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": self.raw}}],
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        response = await self.client.chat.completions.create(**kwargs)
        self.requests.append({"content": response.choices[0].message.content,
            "usage": response.usage.model_dump() if response.usage else None})
        return response


async def run(args):
    source = ROOT / "project_notes/experiments/scope-development-v3/tasks.jsonl"
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    cases = {c["id"]: c for c in json.loads((ROOT / "data/evaluation/scope_v3/development.json").read_text(encoding="utf-8"))["tasks"]}
    seeds = [next(r for r in records if r["category"] == category and r["score"])
             for category in ("metric_query", "annual_growth", "company_comparison")]
    settings = ModelConfig.load()
    store = FinancialStore(ROOT / "data/processed/reports.json")
    candidate = json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8"))
    directory = ROOT / "project_notes/experiments" / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "manifest.json", manifest(ROOT, args.run_id, {
        "source_sha256": sha256(source), "model": settings.model, "thinking": settings.extra_body,
        "faults": ["malformed_json", "extra_field", "invalid_enum"], "seed_cases": [s["case_id"] for s in seeds],
        "method": "identical injected first responses; repair toggled; actual provider returns recorded separately",
        "injected_responses_are_provider_calls": False}))
    path = directory / "repair.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    done = {(r["case_id"], r["fault"], r["enabled"]) for r in rows}
    for seed in seeds:
        for fault in ("malformed_json", "extra_field", "invalid_enum"):
            plan = copy.deepcopy(seed["output"]["request"])
            raw = "{" if fault == "malformed_json" else json.dumps(
                {**plan, "unexpected": True} if fault == "extra_field" else {**plan, "intent": "unknown_intent"})
            for enabled in (False, True):
                key = (seed["case_id"], fault, enabled)
                if key in done:
                    continue
                requests, telemetry = [], {}
                client = InjectedClient(async_client(settings), raw, requests)
                row = {"case_id": seed["case_id"], "fault": fault, "enabled": enabled, "injected_response": raw,
                       "injected_response_count": 1}
                try:
                    with patch("finevolab.grounded.async_client", return_value=client):
                        output = await analyze_grounded(settings, store, cases[seed["case_id"]]["question"],
                            candidate, allow_repair=enabled, telemetry=telemetry)
                    row.update(output=output, **assess_scope(ROOT, store, cases[seed["case_id"]], output))
                except Exception as error:
                    row.update(score=0, error_type=type(error).__name__, error=str(error)[:1000])
                row.update(actual_requests=requests, telemetry=telemetry)
                append(path, row)
                rows.append(row)
                done.add(key)
                print(json.dumps({"completed": len(rows), "enabled": enabled, "score": row["score"],
                                  "actual_requests": len(requests)}), flush=True)
    groups = {str(enabled): {"trials": sum(r["enabled"] == enabled for r in rows),
        "successes": sum(r["score"] for r in rows if r["enabled"] == enabled),
        "actual_requests": sum(len(r["actual_requests"]) for r in rows if r["enabled"] == enabled),
        "known_tokens": sum((req["usage"] or {}).get("total_tokens", 0) for r in rows if r["enabled"] == enabled for req in r["actual_requests"])}
        for enabled in (False, True)}
    write_json(directory / "summary.json", {"status": "completed", "groups": groups,
        "synthetic_response_usage_excluded": True, "paired_fault_conditions": 9})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="scope-sdk-repair")
    asyncio.run(run(parser.parse_args()))
