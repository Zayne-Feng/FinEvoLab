"""Measure task, template, source, and referenced-fact overlap across splits."""

from collections import Counter
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.runtime import write_json
from finevolab.grounded import RequestPlan, assemble_numeric
from finevolab.scope_evaluation import assess_scope, task_store


def main():
    directory = ROOT / "data/evaluation/scope_v3"
    frozen = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    splits, semantic, facts, families, documents = {}, {}, {}, {}, {}
    for split, digest in frozen["split_sha256"].items():
        path = directory / f"{split}.json"
        if sha256(path) != digest:
            raise ValueError("A frozen split changed")
        cases = json.loads(path.read_text(encoding="utf-8"))["tasks"]
        semantic[split] = {case["semantic_key"] for case in cases}
        facts[split], families[split], documents[split] = set(), set(), set()
        for case in cases:
            families[split].add((case["operation"], case.get("focus"), case.get("scenario"), bool(case["upload_ids"])))
            documents[split].update(case["upload_ids"])
            reference = case.get("reference_answer") or {}
            observations = reference.get("metrics", []) + [m for calc in reference.get("calculations", []) for m in calc["inputs"]]
            facts[split].update((m["company"], m["fiscal_year"], m["name"]) for m in observations)
        splits[split] = {"tasks": len(cases), "semantic_keys": len(semantic[split]),
            "categories": dict(Counter(c["category"] for c in cases)),
            "companies": sorted({company for case in cases for company in case["companies"]}),
            "primary_company_year_clusters": len({case["fact_clusters"][0] for case in cases if case["category"] != "boundary"}),
            "shared_boundary_contract_cluster": any(case["category"] == "boundary" for case in cases),
            "referenced_fact_identities": len(facts[split]), "template_families": len(families[split]),
            "selected_upload_documents": len(documents[split])}
    pairs = {}
    for left, right in itertools.combinations(splits, 2):
        pairs[f"{left}:{right}"] = {"duplicate_semantic_keys": len(semantic[left] & semantic[right]),
            "shared_referenced_fact_identities": len(facts[left] & facts[right]),
            "shared_template_families": len(families[left] & families[right]),
            "shared_selected_upload_documents": len(documents[left] & documents[right])}
    audit = json.loads((directory / "financial_reference_audit.json").read_text(encoding="utf-8"))
    unique_upload_facts = {(r["metric"]["company"], r["metric"]["fiscal_year"], r["metric"]["name"]) for r in audit}
    result = {"status": "passed", "dataset_manifest_sha256": sha256(directory / "manifest.json"),
        "splits": splits, "pairwise_overlap": pairs, "upload_reference_observations": len(audit),
        "unique_upload_fact_identities": len(unique_upload_facts),
        "scope": "task combinations and selected-source capability; shared templates and facts are explicitly retained",
        "repetitions_are_independent_questions": False, "unseen_language_template_generalization": False}
    supplemental = directory.parent / "scope_supplemental/test.json"
    if supplemental.exists():
        supplemental_manifest = json.loads((supplemental.parent / "manifest.json").read_text(encoding="utf-8"))
        if sha256(supplemental) != supplemental_manifest["dataset_sha256"]:
            raise ValueError("The supplemental set changed")
        cases = json.loads(supplemental.read_text(encoding="utf-8"))["tasks"]
        selected = {identifier for case in cases for identifier in case["upload_ids"]}
        if selected & set().union(*documents.values()):
            raise ValueError("The supplemental document is already selected in the main task bank")
        document_records = json.loads((directory / "documents.json").read_text(encoding="utf-8"))
        store = task_store(ROOT, cases[0], document_records)
        reference_checks = 0
        for case in cases:
            if case["reference_answer"] is None:
                continue
            operation = case["operation"].removeprefix("hybrid_")
            intent, scope = operation, "annual_fiscal"
            if case.get("scenario") == "quarterly":
                intent, scope = "unavailable", "unsupported"
            elif case.get("scenario") == "currency":
                intent, scope = "clarification", "ambiguous"
            plan = RequestPlan(intent=intent, companies=case["companies"], fiscal_year=case["fiscal_year"],
                report_year=case["report_year"], metrics=case["metrics"], scope=scope)
            events = []
            output = {"answer_type": "numeric", "answer": assemble_numeric(plan, store, events),
                "request": plan.model_dump(), "upload_ids": store.upload_ids, "events": events}
            numeric_case = {**case, "operation": operation, "focus": None}
            if not assess_scope(ROOT, store, numeric_case, output)["score"]:
                raise ValueError("A supplemental financial reference differs from its raw filing")
            reference_checks += 1
        result["supplemental"] = {"questions": len(cases), "selected_documents_shared_with_main": 0,
            "numeric_and_boundary_references_verified": reference_checks,
            "languages": dict(Counter(c["language"] for c in cases)), "dataset_sha256": sha256(supplemental),
            "scope": "new source-context and natural query forms; narrative output in English when explicitly requested"}
    if any(pair["duplicate_semantic_keys"] for pair in pairs.values()):
        raise ValueError("Task semantic keys overlap across splits")
    write_json(ROOT / "project_notes/data/scope_task_audit.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
