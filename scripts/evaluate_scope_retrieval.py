"""Compare fixed-budget retrieval against independently source-verified quotes."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.retrieval import explanation_passages, load_index
from finevolab.runtime import write_json
from finevolab.scope_evaluation import task_store


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def main(args):
    gold_path = ROOT / "project_notes/evaluations" / args.evidence / "gold.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    if args.split:
        gold = [case for case in gold if case["split"] == args.split]
    documents = json.loads((ROOT / "data/evaluation/scope_v3/documents.json").read_text(encoding="utf-8"))
    directory = ROOT / "project_notes/experiments" / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    config = {"gold_sha256": sha256(gold_path), "split": args.split, "top_k": 5, "max_chars": 10000,
        "candidate_pool": "legacy 20+20; current additional two focused 20-candidate queries",
        "metric": "fraction of independently verified focused facts with the complete exact gold quote in top five",
        "not_exhaustive_question_evidence_recall": True, "deterministic_retrieval_repeats": 1}
    write_json(directory / "manifest.json", manifest(ROOT, args.run_id, config))
    stores, rows = {}, []
    for profile in ("legacy", "contextual"):
        load_index(ROOT, mode="hybrid", profile=profile).search("revenue", company="MSFT", year=2024, mode="hybrid")
    for case in gold:
        if sha256(ROOT / case["source_path"]) != case["source_sha256"]:
            raise ValueError("Gold evidence source changed")
        upload = case["company"] in {"NVDA", "TSLA", "ADBE"}
        selected = []
        if upload:
            document = next(d for d in documents if d["company"] == case["company"] and d["report_year"] == case["report_year"])
            if document["id"] not in stores:
                stores[document["id"]] = task_store(ROOT, {"upload_ids": [document["id"]]}, documents)
            selected = stores[document["id"]].uploads
        topic = "risk" if case["focus"] == "risk" else "revenue" if case["focus"] == "revenue" else "profit"
        arms = [("raw", "baseline", "contextual"), ("current", "improved", "contextual")]
        if not upload:
            arms.insert(0, ("legacy", "improved", "legacy"))
        for arm, variant, profile in arms:
            started = time.monotonic()
            passages = explanation_passages(ROOT, case["question"], case["company"], case["report_year"], topic,
                variant=variant, profile=profile, uploads=selected, metric_focus=case["focus"])
            quote = normalize(case["quote"])
            hit = any(p["source_sha256"] == case["source_sha256"] and quote in normalize(p.get("body_text", p["text"])) for p in passages)
            rows.append({"case_id": case["id"], "profile": case["profile"], "split": case["split"],
                "arm": arm, "upload": upload, "hit": hit, "elapsed_seconds": time.monotonic() - started,
                "returned": len(passages), "context_chars": sum(len(p["text"]) for p in passages),
                "chunk_ids": [p["id"] for p in passages]})
    groups = {}
    for arm in ("legacy", "raw", "current"):
        for scope in ("common", "upload", "all"):
            subset = [r for r in rows if r["arm"] == arm and (scope == "all" or r["upload"] == (scope == "upload"))]
            if subset:
                groups[f"{arm}:{scope}"] = {"facts": len(subset), "hits_at_5": sum(r["hit"] for r in subset),
                    "evidence_recall_at_5": sum(r["hit"] for r in subset) / len(subset),
                    "mean_context_chars": sum(r["context_chars"] for r in subset) / len(subset),
                    "mean_seconds": sum(r["elapsed_seconds"] for r in subset) / len(subset)}
    write_json(directory / "tasks.json", rows)
    write_json(directory / "summary.json", {"status": "completed", "groups": groups,
        "source_verification": "two isolated same-model source annotation contexts; exact raw substring program verification",
        "inference_unit": "source profile; related facts are clustered", "gold_sha256": sha256(gold_path)})
    print(json.dumps(groups))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--split", choices=["development", "validation", "test"])
    main(parser.parse_args())
