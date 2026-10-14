"""Score source integrity, financial references, and narrative coverage independently."""

import copy
import json
from pathlib import Path

from finevolab.evaluation import score_task
from finevolab.tools import FinancialStore
from finevolab.uploads import ingest_upload
from finevolab.validation import validate_answer


def task_store(root: Path, case: dict, documents: list[dict]) -> FinancialStore:
    for identifier in case.get("upload_ids", []):
        document = next(d for d in documents if d["id"] == identifier)
        result = ingest_upload((root / document["path"]).read_bytes(), Path(document["path"]).suffix,
            document["company"], document["report_year"], root / "data/uploads")
        if result["id"] != identifier:
            raise ValueError("The selected document hash changed")
    return FinancialStore.from_uploads(root / "data/processed/reports.json", case["upload_ids"], root / "data/uploads") if case.get("upload_ids") else FinancialStore(root / "data/processed/reports.json")


def assess_scope(root: Path, store: FinancialStore, case: dict, output: dict) -> dict:
    checks = {"source_validation": False, "task_type": False, "numeric": False, "narrative": False}
    try:
        validate_answer(store, output)
        checks["source_validation"] = True
    except (ValueError, KeyError, TypeError) as error:
        return {"score": 0, "checks": checks, "validation_error": str(error)[:1200]}
    expected_type = "hybrid" if case["operation"].startswith("hybrid") else "explanation" if case["operation"] == "explanation" else "numeric"
    checks["task_type"] = output.get("answer_type", "numeric") == expected_type
    if case["category"] != "boundary":
        request = output.get("request", {})
        checks["request_scope"] = (sorted(request.get("companies", [])) == sorted(case["companies"])
            and request.get("fiscal_year") == case["fiscal_year"])
        if case.get("focus"):
            checks["request_scope"] = (checks["request_scope"] and request.get("topic") == case["topic"]
                and (request.get("report_year") or request.get("fiscal_year")) == case["report_year"])
    if case.get("reference_answer") is not None:
        adjusted = copy.deepcopy(output)
        reference = case["reference_answer"]
        if case.get("upload_ids"):
            expected_metrics = reference["metrics"] + [m for calc in reference["calculations"] for m in calc["inputs"]]
            identifiers = {(m["company"], m["name"], m["fiscal_year"]): m["evidence_id"] for m in expected_metrics}
            for metric in adjusted["answer"].get("metrics", []) + [m for calc in adjusted["answer"].get("calculations", []) for m in calc["inputs"]]:
                key = (metric["company"], metric["name"], metric["fiscal_year"])
                if key in identifiers:
                    metric["evidence_id"] = identifiers[key]
            for calc in adjusted["answer"].get("calculations", []):
                calc["evidence_ids"] = [m["evidence_id"] for m in calc["inputs"]]
            adjusted["answer"]["evidence_ids"] = sorted({m["evidence_id"] for m in adjusted["answer"].get("metrics", [])} |
                {m["evidence_id"] for calc in adjusted["answer"].get("calculations", []) for m in calc["inputs"]})
        result = score_task(case, adjusted)
        checks.update({"financial_" + key: value for key, value in result["checks"].items()})
        checks["numeric"] = bool(result["score"])
    else:
        checks["numeric"] = True
    if expected_type in {"explanation", "hybrid"}:
        narrative = output.get("narrative", {}) if expected_type == "hybrid" else output.get("answer", {})
        checks["narrative"] = narrative.get("status") == "complete" and bool(narrative.get("claims"))
        if expected_type == "hybrid":
            checks["joint_completion"] = output.get("overall_status") == "complete"
    else:
        checks["narrative"] = True
    return {"score": int(all(checks.values())), "checks": checks,
            "semantic_support": "pending_blind_review" if expected_type in {"explanation", "hybrid"} else "not_applicable"}
