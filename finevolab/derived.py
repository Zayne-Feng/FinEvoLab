"""Compute annual profit margins with source-bound field audits."""

from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from finevolab.ingestion import FinancialMetric

if TYPE_CHECKING:
    from finevolab.tools import FinancialStore

DerivedMetric = Literal["operating_margin", "net_margin", "operating_margin_change", "net_margin_change"]
DERIVED_METRICS = {"operating_margin", "net_margin", "operating_margin_change", "net_margin_change"}


class FieldAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    rule: str
    status: Literal["passed"]
    evidence_ids: list[str]


class DerivedResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["derived"]
    contract_version: Literal["derived_v1"]
    company: str
    metric: DerivedMetric
    fiscal_year: int
    previous_year: int | None
    value: Decimal
    unit: Literal["percent", "percentage_points"]
    formula: str
    inputs: list[FinancialMetric]
    evidence_ids: list[str]
    data_version: str
    source_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    audit: list[FieldAudit]


def calculate_derived(store: "FinancialStore", company: str, metric: DerivedMetric,
                      fiscal_year: int) -> DerivedResult:
    if metric not in DERIVED_METRICS:
        raise ValueError("Unsupported derived metric")
    profit = "operating_income" if metric.startswith("operating_") else "net_income"
    change = metric.endswith("_change")
    years = [fiscal_year - 1, fiscal_year] if change else [fiscal_year]
    inputs, ratios = [], []
    with localcontext() as context:
        context.prec = 34
        for year in years:
            numerator = store.get_metric(company, profit, year)
            denominator = store.get_metric(company, "revenue", year)
            pair = [numerator, denominator]
            if any(not item.value.is_finite() for item in pair):
                raise ValueError("Margin inputs must be finite")
            if denominator.value <= 0:
                raise ValueError("Margin requires positive revenue")
            if len({(item.company, item.fiscal_year, item.currency, item.unit, item.period_kind,
                     item.period_start, item.period_end) for item in pair}) != 1:
                raise ValueError("Margin inputs have incompatible units or periods")
            if numerator.period_kind != "annual_flow":
                raise ValueError("Margin inputs must be annual flows")
            start, end = date.fromisoformat(numerator.period_start), date.fromisoformat(numerator.period_end)
            if not 330 <= (end - start).days <= 400:
                raise ValueError("Margin requires an annual reporting period")
            for item in pair:
                source = store.evidence.get(item.evidence_id)
                if not source or source.get("company") != item.company:
                    raise ValueError("Margin input evidence is missing or belongs to another company")
            inputs.extend(item.model_copy(deep=True) for item in pair)
            ratios.append(numerator.value / denominator.value)
        if change:
            if (inputs[0].unit, inputs[0].currency) != (inputs[2].unit, inputs[2].currency):
                raise ValueError("Margin change inputs have incompatible units or currencies")
            if date.fromisoformat(inputs[0].period_end) + timedelta(days=1) != date.fromisoformat(inputs[2].period_start):
                raise ValueError("Margin change requires consecutive annual periods")
            value = (ratios[1] - ratios[0]) * 100
            formula = "(current_profit / current_revenue - previous_profit / previous_revenue) * 100"
        else:
            value = ratios[0] * 100
            formula = f"({profit} / revenue) * 100"
    evidence_ids = list(dict.fromkeys(item.evidence_id for item in inputs))
    audit = [FieldAudit(field=f"inputs.{index}", rule="source_snapshot_match", status="passed",
                        evidence_ids=[item.evidence_id]) for index, item in enumerate(inputs)]
    audit.extend(FieldAudit(field=field, rule=rule, status="passed", evidence_ids=evidence_ids)
                 for field, rule in (("disclosures", "latest_available_disclosure_per_input"),
                                     ("periods", "aligned_annual_flows_and_consecutive_years" if change else "aligned_annual_flows"),
                                     ("unit", "percentage_point_difference" if change else "profit_to_revenue_percent"),
                                     ("value", "decimal_formula_recomputation")))
    return DerivedResult(kind="derived", contract_version="derived_v1", company=inputs[-1].company,
        metric=metric, fiscal_year=fiscal_year, previous_year=fiscal_year - 1 if change else None,
        value=value, unit="percentage_points" if change else "percent", formula=formula,
        inputs=inputs, evidence_ids=evidence_ids, data_version=store.version,
        source_signature=store.source_signature, audit=audit)


def validate_derived(store: "FinancialStore", output: dict | DerivedResult) -> DerivedResult:
    result = DerivedResult.model_validate(output)
    expected = calculate_derived(store, result.company, result.metric, result.fiscal_year)
    if result.model_dump() != expected.model_dump():
        raise ValueError("Derived calculation or field audit differs from the source snapshot")
    return result
