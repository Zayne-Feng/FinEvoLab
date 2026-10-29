"""Compare exact retrieval backends and shared Redis caching on frozen evidence."""

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from finevolab.retrieval import explanation_passages, load_index
from finevolab.retrieval_settings import RetrievalSettings, retrieval_observations
from finevolab.retrieval_cache import cache_client
from finevolab.scope_evaluation import task_store
from finevolab.runtime import fingerprint
from service_experiment_utils import initialize, write, digest


def configuration(arm, namespace):
    settings = RetrievalSettings(vector_backend="milvus" if arm in {"C", "D"} else "numpy")
    settings.redis.enabled = arm in {"B", "D"}
    settings.redis.prefix = "finevolab:bench:" + namespace + ":" + arm
    path = ROOT / "output/retrieval_services" / ("benchmark_" + arm + ".json")
    write(path, settings.model_dump(mode="json"))
    os.environ["FINEVOLAB_RETRIEVAL_SERVICES_CONFIG"] = str(path)
    return settings


def retrieve(case, documents, uploads):
    selected = [uploads[d["id"]] for d in documents if d["company"] == case["company"]
                and d["report_year"] == case["report_year"]] if case["company"] in {"NVDA", "TSLA", "ADBE"} else []
    topic = "risk" if case["focus"] == "risk" else "revenue" if case["focus"] == "revenue" else "profit"
    return explanation_passages(ROOT, case["question"], case["company"], case["report_year"], topic,
        profile="contextual", uploads=selected, metric_focus=case["focus"])


def main(args):
    if args.worker:
        configuration("B", args.output.name)
        events = []
        with retrieval_observations(events):
            result = explanation_passages(ROOT, "Why did Microsoft revenue grow in FY2024?", "MSFT", 2024,
                "revenue", profile="contextual", metric_focus="revenue")
        print(json.dumps({"ids": [r["id"] for r in result], "events": events}))
        return
    gold_path = ROOT / "project_notes/evaluations/scope-evidence-v1/gold.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    documents = json.loads((ROOT / "data/evaluation/scope_v3/documents.json").read_text(encoding="utf-8"))
    initialize(args.output, {"questions": len(gold), "repeats": args.repeats, "arms": ["A", "B", "C", "D"],
        "top_k": 5, "max_chars": 10000, "reranker_enabled": False, "gold_sha256": digest(gold_path),
        "order_seed": 17, "service": "Milvus Lite gRPC; FLAT/IP", "silent_vector_fallback": False},
        ["scripts/benchmark_retrieval_services.py", "data/evaluation/scope_v3/documents.json",
         "project_notes/evaluations/scope-evidence-v1/gold.json", "data/indexes/contextual/structured.json",
         "data/indexes/contextual/structured.metadata.json"])
    if (args.output / "summary.json").exists():
        print(json.dumps({"status": "already_completed"}))
        return
    uploads = {}
    for document in documents:
        store = task_store(ROOT, {"upload_ids": [document["id"]]}, documents)
        uploads[document["id"]] = store.uploads[0]
    configuration("A", args.output.name)
    load_index(ROOT, mode="hybrid", profile="contextual").search("revenue", "MSFT", 2024, mode="hybrid")
    rows, outputs = [], {}
    for arm in ("B", "D"):
        settings = configuration(arm, args.output.name)
        client = cache_client(settings.redis)
        keys = list(client.scan_iter(match=settings.redis.prefix + ":*"))
        if keys: client.delete(*keys)
    for repeat in range(args.repeats):
        order = list(range(len(gold)))
        random.Random(17 + repeat).shuffle(order)
        for i in order:
            case = gold[i]
            arms = ["A", "B", "C", "D"]
            random.Random(case["id"] + str(repeat)).shuffle(arms)
            for arm in arms:
                configuration(arm, args.output.name)
                events = []
                started = time.perf_counter()
                with retrieval_observations(events): result = retrieve(case, documents, uploads)
                elapsed = time.perf_counter() - started
                ids = [item["id"] for item in result]
                outputs[(repeat, case["id"], arm)] = result
                quote = " ".join(case["quote"].split())
                hit = any(item["source_sha256"] == case["source_sha256"] and
                          quote in " ".join(item.get("body_text", item["text"]).split()) for item in result)
                rows.append({"case_id": case["id"], "arm": arm, "repeat": repeat, "elapsed_seconds": elapsed,
                    "hit": hit, "ids": ids, "result_sha256": fingerprint({"rows": result}), "events": events,
                    "vector_scope": "uploaded_bm25" if case["company"] in {"NVDA", "TSLA", "ADBE"} else "fixed"})
            if (order.index(i)+1) % 10 == 0:
                print(json.dumps({"repeat":repeat+1,"questions_completed":order.index(i)+1}),flush=True)
            reference = outputs[(repeat, case["id"], "A")]
            for arm in ("B", "C", "D"):
                candidate = outputs[(repeat, case["id"], arm)]
                if candidate != reference:
                    raise ValueError("Passage results differ from the exact baseline: " + case["id"] + ":" + arm)
        write(args.output / "records.json", rows)
        print(json.dumps({"repeat_completed": repeat+1, "questions": len(gold)}), flush=True)
    shared = []
    for _ in range(2):
        response = subprocess.check_output([sys.executable, str(Path(__file__)), "--worker", "--output", str(args.output)],
                                          cwd=ROOT, text=True, encoding="utf-8")
        shared.append(json.loads(response.strip().splitlines()[-1]))
    if shared[0]["ids"] != shared[1]["ids"] or not any(e.get("status") == "hit" for e in shared[1]["events"]):
        raise ValueError("Separate retrieval processes did not share the Redis entry")
    groups = {}
    for arm in ("A", "B", "C", "D"):
        for phase in ("first_pass", "warm"):
            subset = [r for r in rows if r["arm"] == arm and (r["repeat"] == 0) == (phase == "first_pass")]
            values = [r["elapsed_seconds"] for r in subset]
            groups[arm+":"+phase] = {"evaluations": len(subset), "evidence_hits": sum(r["hit"] for r in subset),
                "mean_seconds": statistics.mean(values), "p50_seconds": float(np.percentile(values,50)),
                "p95_seconds": float(np.percentile(values,95)), "redis_hits": sum(any(e.get("event")=="retrieval_cache" and
                e.get("status")=="hit" for e in r["events"]) for r in subset),
                "milvus_requests": sum(sum(e.get("actual_backend")=="milvus" for e in r["events"]) for r in subset)}
    settings = configuration("B", args.output.name)
    redis_info = cache_client(settings.redis).info("memory")
    write(args.output / "summary.json", {"status": "completed", "groups": groups, "queries": len(gold),
        "fixed_queries": sum(r["company"] not in {"NVDA","TSLA","ADBE"} for r in gold),
        "paired_backend_comparisons": len(gold)*args.repeats*3, "exact_passage_equivalence": True,
        "cross_process_cache": shared, "redis_used_memory_bytes": redis_info["used_memory"], "model_requests": 0,
        "scope": "retrieval of final evidence contexts, including unchanged selected-document BM25 tasks"})
    print(json.dumps({"status": "completed", "groups": groups}),flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--worker", action="store_true")
    main(parser.parse_args())
