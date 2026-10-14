"""Freeze natural source-context questions with independent annual references."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import fingerprint, write_json
from prepare_scope_tasks import answer, growth, independent_upload_references


def main():
    directory = ROOT / "data/evaluation/scope_supplemental"
    if (directory / "manifest.json").exists():
        raise ValueError("The supplemental task set is already frozen")
    sources = json.loads((ROOT / "data/acceptance_sources.json").read_text(encoding="utf-8"))
    references, _ = independent_upload_references(sources)
    document = next(s for s in sources if s["company"] == "NVDA" and s.get("fiscal_year") == 2023)
    seeds = json.loads((directory / "seeds.json").read_text(encoding="utf-8"))
    cases = []
    for seed in seeds:
        year, names, operation = seed.get("fiscal_year", 2023), seed["metrics"], seed["operation"]
        reference = None
        if seed.get("status"):
            reference = answer(status=seed["status"])
        elif operation != "explanation":
            values = [references[("NVDA", 2023, year, name)] for name in names]
            reference = answer(calculations=[growth(m, references[("NVDA", 2023, year - 1, m.name)]) for m in values]) if operation.endswith("growth") else answer(values)
        key = fingerprint({"source": document["sha256"], "operation": operation, "year": year,
                           "metrics": names, "focus": seed.get("focus"), "scenario": seed.get("scenario")})
        focus = seed.get("focus")
        company = seed.get("company", "NVDA")
        cases.append({"id": key[:20], "semantic_key": key, "split": "test", "category": "boundary" if seed.get("status") else "upload",
            "company": company, "companies": [company], "fiscal_year": year, "report_year": 2023,
            "operation": operation, "metrics": names, "focus": focus, "scenario": seed.get("scenario"),
            "topic": "risk" if focus == "risk" else "revenue" if focus == "revenue" else "profit",
            "question": seed["question"], "upload_ids": [document["sha256"]], "reference_answer": reference,
            "common_support": False, "fact_clusters": [f"{company}:{year}"],
            "language": "zh" if any("\u4e00" <= c <= "\u9fff" for c in seed["question"]) else "en"})
    original = {case["semantic_key"] for split in ("development", "validation", "test") for case in
        json.loads((ROOT / f"data/evaluation/scope_v3/{split}.json").read_text(encoding="utf-8"))["tasks"]}
    if len(cases) != 20 or len({c["semantic_key"] for c in cases}) != 20 or original & {c["semantic_key"] for c in cases}:
        raise ValueError("Supplemental task identity validation failed")
    write_json(directory / "test.json", {"data_version": "scope_supplemental_v1", "tasks": cases})
    write_json(directory / "manifest.json", {"status": "frozen", "questions": len(cases),
        "dataset_sha256": sha256(directory / "test.json"), "seeds_sha256": sha256(directory / "seeds.json"),
        "source_id": document["sha256"], "languages": {name: sum(c["language"] == name for c in cases) for name in ("en", "zh")},
        "scope": "newly selected NVDA FY2023 filing and natural source references; not a new unseen company",
        "narrative_output": "English explicitly requested for Chinese explanation questions",
        "shared_financial_facts": True, "prepared_before_supplemental_agent_outputs": True})
    write_json(ROOT / "project_notes/data/scope_supplemental_freeze.json", manifest(ROOT,
        "scope-supplemental-freeze", {"dataset_sha256": sha256(directory / "test.json"), "questions": 20,
                                    "agent_outputs_observed": False}))
    print("Supplemental tasks frozen")


if __name__ == "__main__":
    main()
