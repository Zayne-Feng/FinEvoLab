"""Audit source references and freeze the structured numerical benchmark."""

from uuid import uuid4
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.evaluation import reference_answer, score_task, source_audit
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json
from finevolab.tools import FinancialStore


def main():
    store = FinancialStore(ROOT / "data/processed/reports.json")
    target = ROOT / "data/evaluation"
    existing = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if existing.get("status") == "frozen_structured_v1":
        for name, digest in existing["split_sha256"].items():
            if sha256(target / (name + ".json")) != digest:
                raise ValueError("A frozen split has changed")
        print(json.dumps({"status": existing["status"], "split_sha256": existing["split_sha256"]}))
        return
    audit = source_audit(ROOT, store)
    splits, identities = {}, set()
    for split, size in (("development", 30), ("validation", 20), ("test", 30)):
        payload = json.loads((target / (split + ".json")).read_text(encoding="utf-8"))
        if payload["data_version"] != store.version or len(payload["tasks"]) != size:
            raise ValueError("Invalid split version or size")
        for case in payload["tasks"]:
            if case["id"] in identities or case["split"] != split:
                raise ValueError("Task identity overlap or invalid split")
            identities.add(case["id"])
            case.pop("open_text_review", None)
            case.pop("review_status", None)
            if case.get("scenario") == "fiscal_alignment":
                case["expected"].update(store.compare(["MSFT", "AAPL"], "revenue", 2024).model_dump(mode="json"))
                case["expected"]["status"] = "periods_not_aligned"
            reference = reference_answer(case)
            if score_task(case, {"answer": reference})["score"] != 1:
                raise ValueError("A reference answer does not satisfy the scorer")
        payload.update(status="frozen_structured_v1", scope="source_bound_structured_financial_tasks",
                       prose_quality_scored=False, human_review_status="not_performed")
        write_json(target / (split + ".json"), payload)
        splits[split] = payload["tasks"]
    overlap = {}
    for left, right in combinations(splits, 2):
        overlap[left + "_vs_" + right] = {
            "shared_exact_questions": sorted({item["question"] for item in splits[left]} & {item["question"] for item in splits[right]}),
            "shared_categories": sorted({item["category"] for item in splits[left]} & {item["category"] for item in splits[right]}),
            "entity_and_template_overlap": True}
    record = {"status": "frozen_structured_v1", "data_version": store.version,
              "frozen_at": datetime.now(timezone.utc).isoformat(), "source_audit": audit,
              "split_sizes": {name: len(items) for name, items in splits.items()},
              "category_counts": dict(Counter(item["category"] for items in splits.values() for item in items)),
              "split_sha256": {name: sha256(target / (name + ".json")) for name in splits},
              "overlap_audit": overlap, "generalization_scope": "within_company_year_and_template_distribution",
              "human_review_status": "not_performed", "open_text_scoring": "excluded",
              "scoring": "strict_status_numbers_currency_units_fiscal_periods_formula_citations",
              "test_used_by_optimizer": False}
    write_json(target / "manifest.json", record)
    report = manifest(ROOT, uuid4().hex[:12] + "-reference-audit", record)
    write_json(ROOT / "project_notes/evaluations/reference_audit.json", report)
    print(json.dumps({"status": record["status"], "audit": audit, "split_sizes": record["split_sizes"]}))


if __name__ == "__main__":
    main()
