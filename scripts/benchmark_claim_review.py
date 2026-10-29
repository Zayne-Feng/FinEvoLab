"""Measure source-support decisions on paired supported and corrupted predictions."""

import argparse
import asyncio
import json
from pathlib import Path
import random
import sys

from pydantic import BaseModel, ConfigDict
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from finevolab.config import ModelConfig
from finevolab.provider import async_client
from finevolab.reviewing import ClaimReview, REVIEW_INSTRUCTION, SELF_CHECK_INSTRUCTION
from finevolab.sdk_runtime import generate_json
from finevolab.validation import validate_answer
from service_experiment_utils import digest,initialize,write


class SampleAssessment(ClaimReview):
    sample_id:str


class BatchAssessment(BaseModel):
    model_config=ConfigDict(extra="forbid")
    ratings:list[SampleAssessment]


async def main(args):
    dataset_path=ROOT/"data/evaluation/service_agents/claim_controls.json"
    data=json.loads(dataset_path.read_text(encoding="utf-8"))
    settings=ModelConfig.load()
    initialize(args.output,{"dataset_sha256":digest(dataset_path),"model":settings.model,"thinking":settings.extra_body,
        "samples":len(data["samples"]),"prediction_pairs":data["prediction_pairs"],"source_request_ids":data["source_request_ids"],"batch_size":4,"temperature":0,"max_tokens":3200,
        "arms":["source_validation","self_check","reviewer"],"order_seed":29},
        ["scripts/benchmark_claim_review.py",dataset_path.relative_to(ROOT).as_posix()])
    samples={row["id"]:row for row in data["samples"]}
    rows_path=args.output/"records.json"
    rows=json.loads(rows_path.read_text(encoding="utf-8")) if rows_path.exists() else []
    completed={(r["id"],r["arm"]) for r in rows}
    from finevolab.scope_evaluation import task_store
    documents=json.loads((ROOT/"data/evaluation/scope_v3/documents.json").read_text(encoding="utf-8"))
    stores={tuple(sample["output"].get("upload_ids",[])):task_store(ROOT,
        {"upload_ids":sample["output"].get("upload_ids",[])},documents) for sample in samples.values()}
    for identifier,sample in samples.items():
        if (identifier,"source_validation") in completed:continue
        try:
            identifiers=sample["output"].get("upload_ids",[])
            scoped_store=stores[tuple(identifiers)]
            validate_answer(scoped_store,sample["output"])
            accepted=True
        except (ValueError,KeyError,TypeError):accepted=False
        rows.append({"id":identifier,"arm":"source_validation","accepted":accepted,
                     "expected_supported":sample["expected_supported"],"source_case_id":sample["source_case_id"]})
    requests_path=args.output/"usage.json"
    usage=json.loads(requests_path.read_text(encoding="utf-8")) if requests_path.exists() else []
    async with async_client(settings) as client:
        for arm,instruction in (("self_check",SELF_CHECK_INSTRUCTION),("reviewer",REVIEW_INSTRUCTION)):
            identifiers=list(samples)
            random.Random(29).shuffle(identifiers)
            identifiers=[key for key in identifiers if (key,arm) not in completed]
            for offset in range(0,len(identifiers),4):
                batch={}
                for key in identifiers[offset:offset+4]:
                    output=samples[key]["output"]
                    narrative=output.get("narrative",output["answer"])
                    batch[key]={"question":output["question"],"request":output["request"],"claims":narrative["claims"],
                        "passages":[p for p in output["evidence"] if p["id"].startswith("passage:")],
                        "numeric_answer":output["answer"] if output["answer_type"]=="hybrid" else None}
                messages=[{"role":"system","content":instruction+" Evaluate each sample independently. Return JSON matching: "+json.dumps(BatchAssessment.model_json_schema())},
                          {"role":"user","content":json.dumps(batch,ensure_ascii=False)}]
                def record(response):
                    usage.append({"arm":arm,"input_tokens":response.usage.input_tokens,
                                  "output_tokens":response.usage.output_tokens,"total_tokens":response.usage.total_tokens})
                    write(requests_path,usage)
                raw=await generate_json(client,settings,messages,record,max_tokens=3200,
                    agent_name="EvidenceReviewer" if arm=="reviewer" else "FinancialAnalyst")
                parsed=BatchAssessment.model_validate_json(raw)
                if len(parsed.ratings)!=len(batch) or {r.sample_id for r in parsed.ratings}!=set(batch):
                    raise ValueError("Review batch omitted or duplicated sample identities")
                for rating in parsed.ratings:
                    if sorted(r.index for r in rating.claims)!=list(range(len(batch[rating.sample_id]["claims"]))):
                        raise ValueError("Review batch omitted or duplicated claim identities")
                    sample=samples[rating.sample_id]
                    accepted=rating.complete and bool(rating.claims) and all(c.verdict=="supported" for c in rating.claims)
                    rows.append({"id":rating.sample_id,"arm":arm,"accepted":accepted,
                        "expected_supported":sample["expected_supported"],"source_case_id":sample["source_case_id"],
                        "review":rating.model_dump(exclude={"sample_id"})})
                write(rows_path,rows)
                print(json.dumps({"arm":arm,"completed":sum(r["arm"]==arm for r in rows)}),flush=True)
    groups={}
    for arm in ("source_validation","self_check","reviewer"):
        subset=[r for r in rows if r["arm"]==arm]
        groups[arm]={"samples":len(subset),"correct":sum(r["accepted"]==r["expected_supported"] for r in subset),
            "false_accepts":sum(r["accepted"] and not r["expected_supported"] for r in subset),
            "false_rejects":sum(not r["accepted"] and r["expected_supported"] for r in subset),
            "model_requests":sum(r["arm"]==arm for r in usage),"tokens":sum(r["total_tokens"] for r in usage if r["arm"]==arm)}
    write(args.output/"summary.json",{"status":"completed","groups":groups,"prediction_pairs":data["prediction_pairs"],"source_request_ids":data["source_request_ids"],
        "scope":"support-detection controls on paired archived answers; not end-to-end business accuracy"})
    print(json.dumps({"status":"completed","groups":groups}),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    asyncio.run(main(parser.parse_args()))
