"""Create an immutable task revision with separated question templates."""

from uuid import uuid4
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.evaluation import reference_answer, score_task, source_audit
from finevolab.experiments import manifest, sha256
from finevolab.runtime import fingerprint, write_json
from finevolab.tools import FinancialStore


TEMPLATES = {
    "development": {
        "metric_query": "Extract {company}'s annual {metric} for fiscal {year}. Preserve the disclosed amount, currency, unit, actual period and source identifier.",
        "annual_growth": "Use the annual calculation tool to determine {company}'s FY{year} {metric} percentage change against the preceding fiscal year. Include the formula and both source observations.",
        "company_comparison": "Compare the annual {metric} observations of {companies} for FY{year}, retaining each actual date range and an explicit alignment result.",
        "integrated_analysis": "Explain the revenue, operating income and net income levels of {company} in FY{year}. Ground the explanation in all three amounts, fiscal periods and source references."},
    "validation": {
        "metric_query": "For FY{year}, what {metric} did {company} disclose? Return the full source-bound observation without rescaling or changing its fiscal dates.",
        "annual_growth": "How much did {metric} change year over year for {company} in fiscal {year}? Obtain the program-computed percent and retain the prior/current inputs and evidence.",
        "company_comparison": "Place {companies} side by side on FY{year} {metric}. Identify the monetary amounts and determine whether the disclosed fiscal intervals coincide.",
        "integrated_analysis": "Prepare a source-backed income-statement interpretation for {company}, fiscal {year}, covering revenue and both operating and net income. Distinguish these measures and retain their reported units and dates."},
    "test": {
        "metric_query": "Locate the yearly {metric} amount attributable to {company} for FY{year}; attach its original currency, unit, annual interval and evidence ID.",
        "annual_growth": "Determine the FY{year}-versus-prior-year movement in {company} {metric}, expressed as a percentage. Use (current / previous - 1) * 100 and provide the source-backed calculation object.",
        "company_comparison": "Assess the difference in reported FY{year} {metric} between {companies}. Keep the company observations separate and state the fiscal-period compatibility.",
        "integrated_analysis": "Using disclosures for {company} FY{year}, describe the relationship among its top line, operating result and bottom line. Supply revenue, operating_income and net_income with verifiable observations and citations."}}


def semantic_key(case):
    return fingerprint({"category": case["category"], "company": case.get("company"),
                        "companies": sorted(case.get("companies", [])), "metric": case.get("metric"),
                        "year": case.get("fiscal_year"), "scenario": case.get("scenario")})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation/v2")
    args = parser.parse_args()
    if (args.output / "manifest.json").exists():
        existing = json.loads((args.output / "manifest.json").read_text(encoding="utf-8"))
        for split, digest in existing["split_sha256"].items():
            if sha256(args.output / (split + ".json")) != digest:
                raise ValueError("A frozen task revision has changed")
        print(json.dumps({"status": existing["status"], "version": existing["version"]}))
        return
    store = FinancialStore(ROOT / "data/processed/reports.json")
    source = ROOT / "data/evaluation"
    frozen = json.loads((source / "manifest.json").read_text())
    audit = source_audit(ROOT, store)
    splits, seen, families = {}, set(), {}
    for split in ("development", "validation", "test"):
        path = source / (split + ".json")
        if sha256(path) != frozen["split_sha256"][split]:
            raise ValueError("The source split hash differs from its frozen manifest")
        payload = json.loads(path.read_text())
        for case in payload["tasks"]:
            key = semantic_key(case)
            if key in seen:
                raise ValueError("A semantic task identity is duplicated")
            seen.add(key)
            if case["category"] in TEMPLATES[split]:
                case["question"] = TEMPLATES[split][case["category"]].format(
                    company=case.get("company"), companies=" and ".join(case.get("companies", [])),
                    metric=case.get("metric", "").replace("_", " "), year=case.get("fiscal_year"))
                case["template_family"] = split + "_" + case["category"]
            else:
                case["template_family"] = "boundary_" + case["scenario"]
            case["semantic_key"] = key
            families.setdefault(split, set()).add(case["template_family"])
            if score_task(case, {"answer": reference_answer(case)})["score"] != 1:
                raise ValueError("A task reference fails the frozen scorer")
        payload.update(status="frozen_structured_v2", version="v2", open_text_rubric="open_text_v1",
                       human_review_status="calibration_pending", prose_quality_scored=False)
        write_json(args.output / (split + ".json"), payload)
        splits[split] = payload["tasks"]
    overlap = {left + "_vs_" + right: sorted(families[left] & families[right])
               for left, right in (("development", "validation"), ("development", "test"), ("validation", "test"))}
    record = {"status": "frozen_structured_v2", "version": "v2", "data_version": store.version,
              "frozen_at": datetime.now(timezone.utc).isoformat(), "parent_manifest_sha256": sha256(source / "manifest.json"),
              "source_audit": audit, "semantic_task_duplicates": 0, "shared_template_families": overlap,
              "split_sizes": {name: len(cases) for name, cases in splits.items()},
              "category_counts": dict(Counter(case["category"] for cases in splits.values() for case in cases)),
              "split_sha256": {name: sha256(args.output / (name + ".json")) for name in splits},
              "entity_year_overlap": "retained_and_disclosed", "human_calibration": "pending",
              "open_text_scoring": "review_only_until_calibrated", "test_used_by_optimizer": False}
    write_json(args.output / "manifest.json", record)
    run_id = uuid4().hex[:12] + "-evaluation-v2-audit"
    write_json(ROOT / "project_notes/evaluations/task_revision_audit.json", manifest(ROOT, run_id, record))
    print(json.dumps({"status": record["status"], "split_sizes": record["split_sizes"], "template_overlap": overlap}))


if __name__ == "__main__":
    main()
