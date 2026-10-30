"""Compare financial facts only after normalizing scope, scale, and source lineage."""

from decimal import Decimal, localcontext
import json
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finevolab.ingestion import FinancialMetric


class SourceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    metric: FinancialMetric
    source_id: str = Field(min_length=1)
    filing_id: str | None = None
    disclosed_on: str = ""
    scale: Decimal = Field(default=Decimal(1), gt=0)
    rounding_unit: Decimal = Field(default=Decimal(0), ge=0)
    restated: bool = False

    @model_validator(mode="after")
    def finite_fact(self):
        if not self.metric.value.is_finite():
            raise ValueError("Source values must be finite")
        if self.disclosed_on:
            from datetime import date
            date.fromisoformat(self.disclosed_on)
        return self


def reconcile(observations: list[SourceObservation]) -> dict:
    if not observations:
        return {"status": "insufficient_data", "selected": None, "source_groups": 0, "comparisons": []}
    rows = [SourceObservation.model_validate(item) for item in observations]
    def scope(row):
        m = row.metric
        return (m.company, m.name, m.fiscal_year, m.currency, m.period_kind, m.period_start, m.period_end)
    if len({scope(row) for row in rows}) != 1:
        return {"status": "not_comparable", "reason": "fact_scope_mismatch", "selected": None, "source_groups": 0,
                "comparisons": [], "observations": [row.model_dump(mode="json") for row in rows]}
    scales = lambda row: {row.metric.currency: Decimal(1), row.metric.currency + "_thousands": Decimal(1000),
                          row.metric.currency + "_millions": Decimal(1000000)}
    if any(scales(row).get(row.metric.unit) != row.scale for row in rows):
        return {"status": "not_comparable", "reason": "unsupported_unit_or_scale", "selected": None, "source_groups": 0,
                "comparisons": [], "observations": [row.model_dump(mode="json") for row in rows]}
    ordered = sorted(rows, key=lambda row: (row.disclosed_on, row.metric.report_year, row.source_id))
    latest = ordered[-1]
    comparisons = []
    with localcontext() as context:
        context.prec = 50
        for row in ordered[:-1]:
            delta = abs(row.metric.value * row.scale - latest.metric.value * latest.scale)
            tolerance = (row.rounding_unit * row.scale + latest.rounding_unit * latest.scale) / 2
            comparisons.append({"left": row.source_id, "right": latest.source_id,
                "difference": str(delta), "tolerance": str(tolerance), "agrees": delta <= tolerance})
    agrees = all(item["agrees"] for item in comparisons)
    lineages = {row.filing_id or "source:" + row.source_id for row in rows}
    status = ("single_source" if len(rows) == 1 else "same_filing" if len(lineages) == 1 else "corroborated") if agrees else "conflict"
    if not agrees and len(lineages) > 1 and latest.restated and all(latest.disclosed_on > row.disclosed_on for row in ordered[:-1]):
        status = "restated"
    return {"status": status, "selected": latest.source_id if status != "conflict" else None,
            "source_groups": len(lineages), "comparisons": comparisons,
            "observations": [row.model_dump(mode="json") for row in ordered]}


def reconcile_metric(store, company, name, fiscal_year):
    company = store.company_id(company)
    rows = []
    for metric in store.metrics:
        if (metric.company, metric.name, metric.fiscal_year) != (company, name, fiscal_year):
            continue
        source = store.evidence[metric.evidence_id]
        try:
            fact = json.loads(source["text"])
        except (ValueError, TypeError):
            fact = {}
        if not isinstance(fact, dict):
            fact = {}
        accession = fact.get("accn")
        if not accession:
            match = re.search(r"/([0-9]{18})/", source.get("source_url", ""))
            accession = match.group(1) if match else None
        filing_id = company + ":" + accession.replace("-", "") if accession else None
        rows.append(SourceObservation(metric=metric, source_id=metric.evidence_id, filing_id=filing_id,
                    disclosed_on=fact.get("filed", "")))
    return reconcile(rows)
