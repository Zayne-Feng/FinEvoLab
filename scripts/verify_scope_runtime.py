"""Verify SDK JSON generation and combined analysis from a selected SEC document."""

import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.config import ModelConfig
from finevolab.experiments import manifest
from finevolab.grounded import analyze_grounded
from finevolab.provider import async_client
from finevolab.runtime import write_json
from finevolab.sdk_runtime import generate_json
from finevolab.tools import FinancialStore
from finevolab.uploads import ingest_upload
from finevolab.validation import validate_answer


async def main():
    settings = ModelConfig.load()
    observations = []
    async with async_client(settings) as client:
        raw = await generate_json(client, settings, [{"role": "system", "content": "Return JSON with ok=true."},
            {"role": "user", "content": "Verify the SDK JSON transport."}],
            lambda response: observations.append({key: getattr(response.usage, key) for key in
                                                 ("requests", "input_tokens", "output_tokens", "total_tokens")}))
    if json.loads(raw).get("ok") is not True:
        raise ValueError("SDK JSON verification failed")
    source = next(item for item in json.loads((ROOT / "data/acceptance_sources.json").read_text())
                  if item["company"] == "NVDA" and item.get("fiscal_year") == 2024)
    document = ingest_upload((ROOT / source["path"]).read_bytes(), ".html", "NVDA", 2024, ROOT / "data/uploads")
    store = FinancialStore.from_uploads(ROOT / "data/processed/reports.json", [document["id"]], ROOT / "data/uploads")
    question = "Using only the selected NVIDIA FY2024 report, calculate revenue growth over the prior fiscal year and explain the disclosed business drivers, citing the report."
    telemetry = {}
    output = await analyze_grounded(settings, store, question, {"system": "Use only the selected source and preserve fiscal periods."}, telemetry=telemetry)
    validation = validate_answer(store, output)
    if output["answer_type"] != "hybrid" or output["overall_status"] != "complete":
        raise ValueError("The combined analysis did not complete")
    path = ROOT / "project_notes/run_logs/scope_sdk_smoke.json"
    write_json(path, {**manifest(ROOT, "scope-sdk-smoke-v1", {"model": settings.model, "thinking": settings.extra_body,
        "document_id": document["id"], "question": question}), "minimal_usage": observations,
        "output": output, "validation": validation, "status": "passed"})
    print(json.dumps({"status": "passed", "answer_type": output["answer_type"],
        "overall_status": output["overall_status"], "usage": output["usage"],
        "tool_calls": output["tool_calls"], "growth": output["answer"]["calculations"][0]["value"]}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
