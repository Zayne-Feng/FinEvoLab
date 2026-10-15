"""Compare chunk strategies and retrieval modes on frozen financial tasks."""

from uuid import uuid4
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.embeddings import PassageEncoder, load_vectors
from finevolab.evaluation import reference_answer
from finevolab.experiments import manifest, sha256
from finevolab.retrieval import ReportIndex
from finevolab.runtime import write_json


LABELS = {"revenue": ("revenue", "net sales", "total sales"),
          "operating_income": ("operating income", "income from operations", "operating loss"),
          "net_income": ("net income", "net earnings", "net loss")}


def gold_groups(case, chunks):
    reference = reference_answer(case)
    observations = reference["metrics"] + [item for calc in reference["calculations"] for item in calc["inputs"]]
    groups = []
    for metric in observations:
        value = abs(Decimal(metric["value"])) / Decimal(1_000_000)
        if value != value.to_integral_value():
            raise ValueError("The retrieval label profile requires integer millions")
        number = str(int(value))
        alternatives = set()
        for chunk in chunks:
            if chunk["company"] != metric["company"] or chunk["report_year"] != case["fiscal_year"]:
                continue
            text = chunk["text"].lower()
            compact = re.sub(r"(?<=\d)[,\s]+(?=\d)", "", text)
            if (any(label in text for label in LABELS[metric["name"]])
                and re.search(r"(?<!\d)" + number + r"(?!\d)", compact)
                and str(metric["fiscal_year"]) in text):
                alternatives.add(chunk["id"])
        groups.append({"company": metric["company"], "metric": metric["name"],
                       "fiscal_year": metric["fiscal_year"], "alternatives": sorted(alternatives)})
    return groups


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("development", "validation", "test"), default="validation")
    parser.add_argument("--repeats", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "data/evaluation")
    args = parser.parse_args()
    frozen = json.loads((args.dataset_dir / "manifest.json").read_text())
    split_path = args.dataset_dir / f"{args.split}.json"
    if not frozen.get("status", "").startswith("frozen_structured_") or sha256(split_path) != frozen["split_sha256"][args.split]:
        raise ValueError("An unchanged frozen split is required")
    tasks = [task for task in json.loads(split_path.read_text())["tasks"] if task["category"] != "boundary"]
    config = json.loads((ROOT / "configs/retrieval.json").read_text())
    run_id = uuid4().hex[:12] + "-retrieval-" + args.split
    directory = ROOT / "project_notes/experiments" / run_id
    write_json(directory / "manifest.json", manifest(ROOT, run_id, {
        "split": args.split, "repeats": args.repeats, "retrieval": config, "data_version": frozen["data_version"],
        "dataset_manifest_sha256": sha256(args.dataset_dir / "manifest.json"),
        "gold_method": "automatic_label_amount_year_alternative_groups_not_human_calibrated",
        "strategies": ["fixed", "structured"], "modes": ["bm25", "vector", "hybrid"],
        "index_sha256": {strategy: sha256(ROOT / f"data/indexes/{strategy}.json") for strategy in ("fixed", "structured")}}))
    encoder = PassageEncoder(config, ROOT / "data/models", local_files_only=True)
    rows, summaries = [], []
    for strategy in ("fixed", "structured"):
        path = ROOT / f"data/indexes/{strategy}.json"
        chunks = json.loads(path.read_text())
        index = ReportIndex(chunks, load_vectors(path, chunks, config), encoder)
        gold = {case["id"]: gold_groups(case, chunks) for case in tasks}
        write_json(directory / (strategy + "_gold.json"), gold)
        for mode in ("bm25", "vector", "hybrid"):
            for repeat in range(1, args.repeats + 1):
                for case in tasks:
                    started = time.monotonic()
                    companies = case.get("companies", [case.get("company")])
                    per_company = [index.search(case["question"], company=company, year=case["fiscal_year"],
                                                mode=mode, limit=5) for company in companies]
                    retrieved = {item["id"] for batch in per_company for item in batch}
                    groups = gold[case["id"]]
                    supported = sum(bool(set(group["alternatives"]) & retrieved) for group in groups)
                    row = {"case_id": case["id"], "category": case["category"], "strategy": strategy,
                           "mode": mode, "repeat": repeat, "required_groups": len(groups),
                           "groups_with_gold": sum(bool(group["alternatives"]) for group in groups),
                           "supported_groups": supported, "group_recall_at_5_per_company": supported / len(groups),
                           "all_groups_supported": supported == len(groups), "retrieved_ids": sorted(retrieved),
                           "elapsed_seconds": round(time.monotonic() - started, 5)}
                    rows.append(row)
                    with (directory / "retrieval.jsonl").open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(row) + "\n")
            subset = [row for row in rows if row["strategy"] == strategy and row["mode"] == mode]
            summary = {"strategy": strategy, "mode": mode, "evaluations": len(subset),
                       "mean_group_recall_at_5_per_company": sum(row["group_recall_at_5_per_company"] for row in subset) / len(subset),
                       "all_groups_supported_rate": sum(row["all_groups_supported"] for row in subset) / len(subset),
                       "gold_coverage": sum(row["groups_with_gold"] for row in subset) / sum(row["required_groups"] for row in subset),
                       "mean_latency_seconds": sum(row["elapsed_seconds"] for row in subset) / len(subset)}
            summaries.append(summary)
            write_json(directory / "summary.json", {"run_id": run_id, "status": "running", "results": summaries})
            print(json.dumps(summary), flush=True)
    write_json(directory / "summary.json", {"run_id": run_id, "status": "complete", "split": args.split,
        "gold_method": "automatic_label_amount_year_alternative_groups_not_human_calibrated", "results": summaries})


if __name__ == "__main__":
    main()
