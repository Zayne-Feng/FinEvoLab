"""Blindly review fixed-plan financial answers with isolated scoring contexts."""

import argparse
import asyncio
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, ConfigDict
from finevolab.config import ModelConfig
from finevolab.provider import async_client
from finevolab.runtime import fingerprint, read_json, write_json


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    supported: bool
    complete: bool
    reason: str


class Decisions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Decision]


def samples_for(records, inputs):
    lookup = {case["id"]: case for case in inputs["model_cases"]}
    samples, identities = [], {}
    for record in records:
        identity = {key: record[key] for key in ("case_id", "repeat", "arm")}
        identifier = fingerprint(identity)[:20]
        identities[identifier] = identity
        if "result" not in record:continue
        result = record["result"]
        samples.append({"id": identifier, "question": lookup[record["case_id"]]["question"],
            "answer": result["answer"], "evidence": [{key: item[key] for key in ("id", "text")} for item in result["evidence"]]})
    evidence = inputs["model_cases"][0]["passages"][0]
    quote = evidence.get("body_text", evidence["text"])[:180]
    controls = [{"id": fingerprint({"control": "positive"})[:20], "question": "Is the cited quotation present verbatim in the supplied passage?",
        "answer": {"status": "complete", "claims": [{"statement": "The cited quotation is present verbatim in the supplied passage.", "quote": quote}]},
        "evidence": [{"id": "control-source", "text": evidence["text"]}], "expected": True},
        {"id": fingerprint({"control": "negative"})[:20], "question": "What caused the disclosed revenue change?",
        "answer": {"status": "complete", "claims": [{"statement": "The report attributes revenue growth to the discovery of a new planet.", "quote": quote}]},
        "evidence": [{"id": "control-source", "text": evidence["text"]}], "expected": False}]
    return samples, identities, controls


async def execute(folder, samples, identities, controls):
    settings = ModelConfig.load()
    path = folder / "blind_reviews.json"
    results = read_json(path) if path.exists() else []
    done = {(item["reviewer"], item["batch"]) for item in results}
    instruction = ("Assess each anonymized answer against its question and supplied report evidence. "
        "supported is true only if all claims, directions and causal assertions follow from that evidence. "
        "complete is true only if the answer addresses all requested companies and metrics. "
        "A source quote alone does not support unrelated causes. Do not infer missing facts. "
        "Return JSON matching this schema: " + json.dumps(Decisions.model_json_schema()))
    async with async_client(settings) as client:
        for reviewer, seed in (("A", 17), ("B", 29)):
            ordered = samples.copy();random.Random(seed).shuffle(ordered)
            for batch, offset in enumerate(range(0, len(ordered), 4)):
                if (reviewer, batch) in done:continue
                selected = ordered[offset:offset + 4]
                blinded = [{key: value for key, value in item.items() if key != "expected"} for item in [*selected, *controls]]
                random.Random(seed + batch).shuffle(blinded)
                response = await asyncio.wait_for(client.chat.completions.create(model=settings.model,
                    messages=[{"role": "system", "content": instruction}, {"role": "user", "content": json.dumps(blinded, ensure_ascii=False)}],
                    response_format={"type": "json_object"}, temperature=0, max_tokens=2200, extra_body=settings.extra_body), 180)
                parsed = Decisions.model_validate_json(response.choices[0].message.content or "")
                expected_ids = {item["id"] for item in blinded}
                if {item.id for item in parsed.items} != expected_ids or len(parsed.items) != len(expected_ids):
                    raise ValueError("Blind review identifiers differ from the supplied batch")
                mapping = {item.id: item for item in parsed.items}
                calibrated = all(mapping[item["id"]].supported == item["expected"] and
                                 mapping[item["id"]].complete == item["expected"] for item in controls)
                usage = response.usage.model_dump() if response.usage else None
                result = {"reviewer": reviewer, "batch": batch, "model": settings.model,
                          "context_isolation": "separate_request_same_model", "controls_passed": calibrated,
                          "decisions": parsed.model_dump()["items"], "usage": usage}
                results.append(result);write_json(path, results)
                print(json.dumps({"reviewer": reviewer, "batch": batch, "controls_passed": calibrated}), flush=True)
    by_id = {}
    for batch in results:
        for item in batch["decisions"]:
            if item["id"] in identities:by_id.setdefault(item["id"], {})[batch["reviewer"]] = item
    calibrated = all(batch["controls_passed"] for batch in results)
    summary = {"model": settings.model, "reviewer_contexts": 2, "controls_passed": calibrated,
               "review_requests": len(results), "review_input_tokens": sum((r["usage"] or {}).get("prompt_tokens", 0) for r in results),
               "review_output_tokens": sum((r["usage"] or {}).get("completion_tokens", 0) for r in results), "arms": {}}
    for identifier, identity in identities.items():
        arm = summary["arms"].setdefault(identity["arm"], {"tasks": 0, "both_supported_and_complete": 0})
        arm["tasks"] += 1
        judgments = by_id.get(identifier, {})
        arm["both_supported_and_complete"] += int(calibrated and len(judgments) == 2 and
            all(item["supported"] and item["complete"] for item in judgments.values()))
    write_json(folder / "blind_summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    inputs = read_json(ROOT / "data/evaluation/analysis_extensions/cases.json")
    records = read_json(args.run / "model_records.json")
    samples, identities, controls = samples_for(records, inputs)
    write_json(args.run / "blind_samples.json", {"samples": samples, "identities": identities, "controls": controls})
    if args.execute:print(json.dumps(asyncio.run(execute(args.run, samples, identities, controls)), indent=2))
    else:print(json.dumps({"samples": len(samples), "external_calls": 0}))


if __name__ == "__main__":main()
