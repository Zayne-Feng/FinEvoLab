"""Replay identical passage outputs and repair request-coverage failures once."""

import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.closure_evaluation import assess
from finevolab.config import ModelConfig
from finevolab.experiments import manifest, sha256
from finevolab.grounded import EXPLANATION_PROMPT, ExplanationDraft, validate_explanation
from finevolab.provider import async_client
from finevolab.runtime import write_json


async def run(args):
    source = ROOT / "project_notes/experiments" / args.source
    records = [json.loads(line) for line in (source / "tasks.jsonl").read_text().splitlines()]
    if any(row["output"]["repair"]["count"] for row in records):
        raise ValueError("The source must have an unused one-repair budget")
    cases = {case["id"]: case for case in json.loads((ROOT / "data/evaluation/closure/acceptance.json").read_text())["tasks"]}
    directory = ROOT / "project_notes/experiments" / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    settings = ModelConfig.load()
    config = {"source": args.source, "rows_sha256": sha256(source / "tasks.jsonl"),
        "model": settings.model, "thinking": settings.extra_body, "temperature": 0, "max_tokens": 1600,
        "intervention": "one_request_coverage_repair_same_answer_same_retrieved_passages_no_new_search",
        "original_generation_instruction_unchanged": True, "max_repairs": 1,
        "core": {path.relative_to(ROOT).as_posix(): sha256(path) for path in (ROOT / "finevolab").glob("*.py")}}
    target = directory / "manifest.json"
    if target.exists() and json.loads(target.read_text())["config"] != config:
        raise ValueError("Repair inputs or implementation changed")
    if not target.exists():
        write_json(target, manifest(ROOT, args.run_id, config))
    path = directory / "tasks.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    done = {(row["case_id"], row["repeat"], row["arm"]) for row in rows}
    async with async_client(settings) as client:
        for original in records:
            if (original["case_id"], original["repeat"], original["arm"]) in done:
                continue
            row = {key: original[key] for key in ("case_id", "repeat", "arm", "category", "plan")}
            output = copy.deepcopy(original["output"])
            row.update(before_score=original["score"], repaired=False, repair_usage=None,
                       original_output_sha256=hashlib.sha256(json.dumps(original["output"], sort_keys=True).encode()).hexdigest())
            try:
                validate_explanation(ROOT, output)
            except ValueError as error:
                row["before_contract_error"] = str(error)
                evidence = [item for event in output["events"] if event.get("tool") == "search_reports" for item in event["output"]]
                question = cases[row["case_id"]]["question"]
                draft = {key: output["answer"][key] for key in ("status", "claims")}
                messages = [{"role": "system", "content": EXPLANATION_PROMPT + "\nReturn a JSON data object. Schema: " + json.dumps(ExplanationDraft.model_json_schema())},
                    {"role": "user", "content": json.dumps({"question": question, "passages": [{"id": item["id"], "text": item["text"]} for item in evidence]}, ensure_ascii=False)},
                    {"role": "assistant", "content": json.dumps(draft)},
                    {"role": "user", "content": "Correct only the invalid output using the supplied source data. Error: " + str(error)[:1200]}]
                started = time.monotonic()
                response = await client.chat.completions.create(model=settings.model, temperature=0,
                    max_tokens=1600, extra_body=settings.extra_body, response_format={"type": "json_object"}, messages=messages)
                raw = response.choices[0].message.content or ""
                row.update(repaired=True, repair_response=raw, repair_seconds=time.monotonic() - started,
                    repair_usage=response.usage.model_dump() if response.usage else None)
                output["repair"] = {"attempted": True, "count": 1, "errors": [{"schema": "ExplanationDraft", "error": str(error)}]}
                output["usage"]["requests"] += 1
                if response.usage:
                    for key, field in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"), ("total_tokens", "total_tokens")):
                        output["usage"][key] += getattr(response.usage, field)
                else:
                    output["usage_complete"] = False
                with (directory / "requests.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({key: row[key] for key in ("case_id", "repeat", "arm", "repair_response", "repair_usage")}) + "\n")
                try:
                    parsed = ExplanationDraft.model_validate_json(raw)
                    used = {claim.chunk_id for claim in parsed.claims}
                    output["answer"] = parsed.model_dump()
                    output["answer"]["answer"] = "\n".join(claim.statement for claim in parsed.claims) or "The supplied passages do not support the request."
                    output["evidence"] = [item for item in evidence if item["id"] in used]
                    validate_explanation(ROOT, output)
                except ValueError as failure:
                    row["repair_error"] = str(failure)[:500]
            try:
                result = assess(ROOT, cases[row["case_id"]], output)
            except ValueError as error:
                result = {"score": 0, "checks": {}, "validation_error": str(error)[:500]}
            row.update(output=output, **result)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.append(row)
            print(json.dumps({"completed": len(rows), "repaired": row["repaired"], "score": row["score"]}), flush=True)
    write_json(directory / "summary.json", {"rows": len(rows), "original_answers_are_replayed": True,
        "real_repair_requests": sum(row["repaired"] for row in rows),
        "known_repair_tokens": sum((row["repair_usage"] or {}).get("total_tokens", 0) for row in rows),
        "failed_repairs": sum("repair_error" in row for row in rows),
        "groups": {arm: {"attempts": sum(row["arm"] == arm for row in rows),
                         "mechanical_successes": sum(row["score"] for row in rows if row["arm"] == arm),
                         "repairs": sum(row["repaired"] for row in rows if row["arm"] == arm)} for arm in sorted({row["arm"] for row in rows})}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="component-rag-v2")
    parser.add_argument("--run-id", default="component-focus-repair")
    asyncio.run(run(parser.parse_args()))
