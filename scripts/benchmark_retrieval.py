"""Compare retrieval equivalence, CPU latency, and fixed-budget reranking."""

import argparse
from collections import Counter
import gc
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
import tracemalloc
import types

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.retrieval import ReportIndex, explanation_passages, load_index
from finevolab.runtime import fingerprint, write_json
from finevolab.uploads import verified_upload


def sources(gold):
    documents = json.loads((ROOT / "data/evaluation/scope_v3/documents.json").read_text())
    uploads = {}
    for case in gold:
        if sha256(ROOT / case["source_path"]) != case["source_sha256"]:
            raise ValueError("Gold source bytes changed")
        if case["company"] in {"NVDA", "TSLA", "ADBE"}:
            key = (case["company"], case["report_year"])
            if key not in uploads:
                document = next(d for d in documents if (d["company"], d["report_year"]) == key)
                uploads[key] = verified_upload(document["id"], ROOT / "data/uploads")
    return uploads


def describe(values):
    return {"samples": len(values), "mean_seconds": statistics.mean(values),
            "p50_seconds": float(np.percentile(values, 50)),
            "p95_seconds": float(np.percentile(values, 95))}


def cache_benchmark(args, gold, uploads):
    source = subprocess.check_output(["git", "show", args.baseline + ":finevolab/retrieval.py"], cwd=ROOT, text=True, encoding="utf-8")
    module = types.ModuleType("retrieval_reference")
    exec(compile(source, "retrieval_reference.py", "exec"), module.__dict__)
    shared = load_index(ROOT, mode="hybrid", profile="contextual")
    original = {"fixed": module.ReportIndex(shared.chunks, shared.vectors, shared.encoder)}
    current = {"fixed": ReportIndex(shared.chunks, shared.vectors, shared.encoder)}
    for key, upload in uploads.items():
        original[key] = module.ReportIndex(upload["chunks"])
        current[key] = ReportIndex(upload["chunks"])
    queries = []
    for case in gold:
        key = (case["company"], case["report_year"])
        corpus = key if key in uploads else "fixed"
        for mode in (["bm25"] if key in uploads else ["bm25", "hybrid"]):
            queries.append({"case_id": case["id"], "corpus": corpus, "query": case["question"],
                "company": key[0], "year": key[1], "mode": mode})
    rows = []
    rng = random.Random(17)
    for repeat in range(args.repeats):
        order = list(range(len(queries)))
        rng.shuffle(order)
        for i in order:
            query = queries[i]
            outputs = {}
            arms = [("original", original), ("cached", current)]
            if (i + repeat) % 2:
                arms.reverse()
            for arm, indexes in arms:
                started = time.perf_counter()
                outputs[arm] = indexes[query["corpus"]].search(query["query"], query["company"], query["year"],
                    mode=query["mode"], limit=5, max_chars=10000)
                rows.append({"case_id": query["case_id"], "mode": query["mode"], "repeat": repeat,
                    "arm": arm, "elapsed_seconds": time.perf_counter() - started,
                    "result_sha256": fingerprint({"results": outputs[arm]})})
            if outputs["original"] != outputs["cached"]:
                raise ValueError("Cached retrieval differs from the original implementation")
        print(json.dumps({"completed_repeat": repeat + 1, "queries": len(queries)}), flush=True)
    groups = {}
    for arm in ("original", "cached"):
        for phase in ("first_pass", "warm"):
            subset = [r["elapsed_seconds"] for r in rows if r["arm"] == arm and (r["repeat"] == 0) == (phase == "first_pass")]
            groups[f"{arm}:{phase}"] = describe(subset)
    gc.collect()
    tracemalloc.start()
    probe = ReportIndex(shared.chunks, shared.vectors, shared.encoder)
    for query in queries:
        if query["corpus"] == "fixed":
            probe.search(query["query"], query["company"], query["year"], mode=query["mode"])
    allocated, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return rows, {"status": "passed", "queries": len(queries), "repeats": args.repeats,
        "paired_evaluations": len(queries) * args.repeats, "exact_result_equivalence": True,
        "groups": groups, "python_cache_heap_bytes": allocated, "python_cache_peak_bytes": peak,
        "scope_capacity_per_index": 32, "query_capacity_per_index": 128,
        "reference_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "warm_mean_speedup": groups["original:warm"]["mean_seconds"] / groups["cached:warm"]["mean_seconds"],
        "model_requests": 0}


def rerank_benchmark(args, gold, uploads):
    from finevolab.reranking import load_config, rerank_passages
    config = load_config(ROOT)
    started = time.perf_counter()
    rerank_passages(ROOT, "revenue growth", [{"id": "warmup", "text": "Revenue increased due to cloud services."}])
    warmup = time.perf_counter() - started
    rows = []
    for case in gold:
        selected = [uploads[(case["company"], case["report_year"])]] if (case["company"], case["report_year"]) in uploads else []
        topic = "risk" if case["focus"] == "risk" else "revenue" if case["focus"] == "revenue" else "profit"
        quote = " ".join(case["quote"].split())
        explanation_passages(ROOT, case["question"], case["company"], case["report_year"], topic,
            variant="pooled", profile="contextual", uploads=selected, metric_focus=case["focus"])
        arms = [("current", "improved"), ("pooled", "pooled"), ("cross_encoder", "reranked")]
        random.Random(case["id"]).shuffle(arms)
        for arm, variant in arms:
            started = time.perf_counter()
            passages = explanation_passages(ROOT, case["question"], case["company"], case["report_year"], topic,
                variant=variant, profile="contextual", uploads=selected, metric_focus=case["focus"])
            if len(passages) > 5 or sum(len(p["text"]) for p in passages) > 10000:
                raise ValueError("Retrieval output budget exceeded")
            hit = any(p["source_sha256"] == case["source_sha256"] and quote in " ".join(p.get("body_text", p["text"]).split()) for p in passages)
            rows.append({"case_id": case["id"], "profile": case["profile"], "split": case["split"],
                "language": case.get("language", "en"), "arm": arm, "hit": hit, "elapsed_seconds": time.perf_counter() - started,
                "chunk_ids": [p["id"] for p in passages], "context_chars": sum(len(p["text"]) for p in passages)})
        print(json.dumps({"completed_case": case["id"], "split": case["split"]}), flush=True)
    groups = {}
    for split in sorted({case["split"] for case in gold}):
        for arm in ("current", "pooled", "cross_encoder"):
            subset = [r for r in rows if r["arm"] == arm and r["split"] == split]
            groups[f"{arm}:{split}"] = {"facts": len(subset), "hits_at_5": sum(r["hit"] for r in subset),
                "evidence_recall_at_5": sum(r["hit"] for r in subset) / len(subset),
                **describe([r["elapsed_seconds"] for r in subset])}
    languages = {}
    for language in sorted({r["language"] for r in rows}):
        for arm in ("current", "pooled", "cross_encoder"):
            subset = [r for r in rows if r["language"] == language and r["arm"] == arm]
            languages[f"{arm}:{language}"] = {"facts": len(subset), "hits_at_5": sum(r["hit"] for r in subset)}
    return rows, {"status": "completed", "groups": groups, "languages": languages, "model": config.model,
        "revision": config.revision, "model_load_seconds": warmup, "model_requests": 0,
        "source_profiles": len({case["profile"] for case in gold}),
        "metric": "complete exact gold quote coverage in top five, clustered by source profile"}


def main(args):
    if args.repeats < 2:
        raise ValueError("At least two timing repetitions are required")
    gold_path = ROOT / "project_notes/evaluations/scope-evidence-v1/gold.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    if args.split:
        gold = [case for case in gold if case["split"] in args.split]
    if args.queries:
        translations = json.loads(args.queries.read_text(encoding="utf-8"))
        if translations["source_sha256"] != sha256(gold_path):
            raise ValueError("Translated queries refer to a different evidence version")
        lookup = {row["id"]: row for row in gold}
        for row in translations["queries"]:
            if row["source_case_id"] in lookup:
                original = lookup[row["source_case_id"]]
                gold.append({**original, "id": original["id"] + "-" + row["language"],
                    "question": row["question"], "language": row["language"]})
    if not gold:
        raise ValueError("No evidence cases were selected")
    config = {"mode": args.mode, "baseline": args.baseline, "split": args.split,
        "repeats": args.repeats, "queries_sha256": sha256(args.queries) if args.queries else None, "gold_sha256": sha256(gold_path), "top_k": 5, "max_chars": 10000}
    identity = manifest(ROOT, args.output.name, config)
    identity.pop("created_at", None)
    write_json(args.output / "manifest.json", identity)
    uploads = sources(gold)
    rows, summary = (cache_benchmark if args.mode == "cache" else rerank_benchmark)(args, gold, uploads)
    write_json(args.output / "records.json", rows)
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["cache", "rerank"], required=True)
    parser.add_argument("--baseline", default="8a10053a4909ee5cd0a4a3da3dde38217ad9c154")
    parser.add_argument("--split", nargs="+", choices=["development", "validation", "test"])
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
