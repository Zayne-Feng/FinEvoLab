"""Validate financial outputs against source observations without task labels."""

from decimal import Decimal

from finevolab.evaluation import TaskAnswer
from finevolab.tools import FinancialStore


def validate_answer(store: FinancialStore, output: dict) -> dict:
    if output.get("upload_ids", []) != store.upload_ids:
        raise ValueError("Answer upload scope differs from the selected documents")
    if output.get("answer_type") == "hybrid":
        numeric = validate_answer(store, {**output, "answer_type": "numeric"})
        from pathlib import Path
        from finevolab.grounded import validate_explanation
        passages = [item for item in output.get("evidence", []) if item["id"].startswith("passage:")]
        narrative = validate_explanation(Path(__file__).resolve().parents[1],
            {**output, "answer": output["narrative"], "evidence": passages}, store)
        numeric_complete = output["answer"]["status"] == "complete"
        text_complete = output["narrative"]["status"] == "complete"
        expected_status = "complete" if numeric_complete and text_complete else "partial" if numeric_complete or text_complete else "insufficient_data"
        if output.get("overall_status") != expected_status:
            raise ValueError("Combined task status differs from its component results")
        return {"status": "passed", "numeric": numeric, "narrative": narrative}
    if output.get("answer_type") == "explanation":
        from pathlib import Path
        from finevolab.grounded import validate_explanation
        return validate_explanation(Path(__file__).resolve().parents[1], output, store)
    answer = TaskAnswer.model_validate(output["answer"])
    if not answer.answer.strip():
        raise ValueError("An explanation is required")
    observations = answer.metrics + [item for calculation in answer.calculations for item in calculation.inputs]
    identities = [(item.company, item.name, item.fiscal_year) for item in observations]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate financial observations")
    for item in observations:
        expected = store.get_metric(item.company, item.name, item.fiscal_year)
        if item.model_dump() != expected.model_dump():
            raise ValueError("An observation differs from the source snapshot")
    for calculation in answer.calculations:
        expected = store.growth(calculation.company, calculation.metric, calculation.fiscal_year)
        if not calculation.value.is_finite() or abs(calculation.value - expected.value) > Decimal("0.000001"):
            raise ValueError("The annual calculation is incorrect")
        actual = calculation.model_dump(exclude={"value"})
        if actual != expected.model_dump(exclude={"value"}):
            raise ValueError("Calculation inputs or definitions differ from the source")
    identifiers = {item.evidence_id for item in observations}
    if len(answer.evidence_ids) != len(set(answer.evidence_ids)) or set(answer.evidence_ids) != identifiers:
        raise ValueError("Evidence references do not support the observations")
    if answer.status in {"insufficient_data", "clarification_required", "invalid_parameters"}:
        if observations or answer.evidence_ids or answer.periods_aligned is not None:
            raise ValueError("Unresolved requests must not contain financial conclusions")
    elif not observations:
        raise ValueError("A completed answer requires source observations")
    if answer.periods_aligned is not None:
        if len({item.company for item in answer.metrics}) < 2:
            raise ValueError("Period alignment requires two distinct companies")
        aligned = len({(item.period_start, item.period_end) for item in answer.metrics}) == 1
        if aligned != answer.periods_aligned:
            raise ValueError("The period alignment statement is incorrect")
    if answer.status == "periods_not_aligned" and answer.periods_aligned is not False:
        raise ValueError("Misaligned-period status requires a comparison")
    if "request" in output:
        from finevolab.grounded import RequestPlan, assemble_numeric
        plan = RequestPlan.model_validate(output["request"])
        expected = TaskAnswer.model_validate(assemble_numeric(plan, store, []))
        if (answer.status, answer.periods_aligned) != (expected.status, expected.periods_aligned):
            raise ValueError("Answer status differs from the request contract")
        def identities_of(value):
            return ({(item.company, item.name, item.fiscal_year) for item in value.metrics},
                    {(item.company, item.metric, item.fiscal_year) for item in value.calculations})
        if identities_of(answer) != identities_of(expected):
            raise ValueError("Answer observations do not cover the request")
    return {"status": "passed", "observations": len(observations), "evidence_ids": sorted(identifiers)}
