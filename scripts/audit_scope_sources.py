"""Audit independent financial references and versioned table contexts."""

from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json
from finevolab.scope_evaluation import task_store


def main():
    bank = ROOT / "data/evaluation/scope_v3"
    documents = json.loads((bank / "documents.json").read_text(encoding="utf-8"))
    references = json.loads((bank / "financial_reference_audit.json").read_text(encoding="utf-8"))
    verified = []
    for document in documents:
        store = task_store(ROOT, {"upload_ids": [document["id"]]}, documents)
        for reference in references:
            if reference["filing_sha256"] != document["sha256"]:
                continue
            expected = reference["metric"]
            actual = store.get_metric(expected["company"], expected["name"], expected["fiscal_year"]).model_dump(mode="json")
            fields = ("company", "name", "fiscal_year", "currency", "unit", "period_start", "period_end", "period_kind", "report_year")
            if any(actual[field] != expected[field] for field in fields) or Decimal(actual["value"]) != Decimal(expected["value"]):
                raise ValueError("An uploaded iXBRL observation differs from independent companyfacts")
            path = ROOT / reference["source_path"]
            if sha256(path) != reference["source_sha256"]:
                raise ValueError("Reference raw source changed")
            node = json.loads(path.read_text(encoding="utf-8"))
            for part in reference["locator"].strip("/").split("/"):
                node = node[int(part)] if isinstance(node, list) else node[part]
            if Decimal(str(node["val"])) != Decimal(actual["value"]) or node["accn"] != reference["accession"]:
                raise ValueError("Reference JSON pointer is invalid")
            verified.append({"document_id": document["id"], "company": actual["company"], "fiscal_year": actual["fiscal_year"],
                "metric": actual["name"], "value": actual["value"], "reference_locator": reference["locator"],
                "runtime_locator": store.evidence[actual["evidence_id"]]["locator"]})
    chunks = json.loads((ROOT / "data/indexes/contextual/structured.json").read_text(encoding="utf-8"))
    contexts = [c["unit_context"] for c in chunks if c.get("unit_context")]
    if len(verified) != len(references):
        raise ValueError("Financial reference coverage is incomplete")
    result = {**manifest(ROOT, "scope-source-audit", {"reference_sha256": sha256(bank / "financial_reference_audit.json"),
        "index_sha256": sha256(ROOT / "data/indexes/contextual/structured.json")}), "status": "passed",
        "independently_verified_observations": len(verified), "documents": len(documents), "verified": verified,
        "contextual_chunks": len(chunks), "unit_context_status": dict(Counter(c["status"] for c in contexts)),
        "unit_context_coverage": "status is explicit; unresolved or ambiguous contexts are not verified units"}
    write_json(ROOT / "project_notes/run_logs/scope_source_audit.json", result)
    print(json.dumps({key: result[key] for key in ("status", "independently_verified_observations", "documents", "unit_context_status")}))


if __name__ == "__main__":
    main()
