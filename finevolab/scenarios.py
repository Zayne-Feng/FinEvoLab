"""Evaluate explicit revenue and profit-margin assumptions against an observed baseline."""

from decimal import Decimal, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finevolab.derived import DerivedResult, validate_derived


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    company: str = Field(min_length=1, max_length=100)
    fiscal_year: int = Field(ge=1900, le=2200)
    margin: Literal["operating_margin", "net_margin"]
    revenue_changes_percent: list[Decimal] = Field(min_length=1, max_length=9)
    margin_changes_percentage_points: list[Decimal] = Field(min_length=1, max_length=9)

    @model_validator(mode="after")
    def explicit_unique_assumptions(self):
        if any(item < -100 for item in self.revenue_changes_percent):
            raise ValueError("Revenue assumptions must not produce negative revenue")
        for values in (self.revenue_changes_percent, self.margin_changes_percentage_points):
            if len(set(values)) != len(values):
                raise ValueError("Scenario assumptions must be unique")
        return self


class ScenarioPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revenue_change_percent: Decimal
    margin_change_percentage_points: Decimal
    revenue: Decimal
    profit: Decimal
    margin_percent: Decimal


class ScenarioResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: Literal["scenario_v1"]
    assumption_based: Literal[True]
    request: ScenarioRequest
    baseline: DerivedResult
    formula: str
    points: list[ScenarioPoint]
    audit: list[str]


def calculate_scenarios(store, request: ScenarioRequest) -> ScenarioResult:
    request = ScenarioRequest.model_validate(request)
    baseline = validate_derived(store, store.derive(request.company, request.margin, request.fiscal_year))
    profit, revenue = baseline.inputs
    points = []
    with localcontext() as context:
        context.prec = 34
        for growth in request.revenue_changes_percent:
            for delta in request.margin_changes_percentage_points:
                adjusted_revenue = revenue.value * (1 + growth / 100)
                adjusted_margin = profit.value / revenue.value + delta / 100
                adjusted_profit = (profit.value + revenue.value * delta / 100) * (1 + growth / 100)
                points.append(ScenarioPoint(revenue_change_percent=growth, margin_change_percentage_points=delta,
                    revenue=adjusted_revenue, profit=adjusted_profit, margin_percent=adjusted_margin * 100))
    return ScenarioResult(contract_version="scenario_v1", assumption_based=True, request=request,
        baseline=baseline, formula="revenue * (1 + revenue_change_percent / 100) * (profit / revenue + margin_change_percentage_points / 100)",
        points=points, audit=["source_bound_baseline", "explicit_assumptions", "decimal_recomputation",
                             "hypothetical_values_separated_from_disclosures"])


def validate_scenarios(store, output):
    result = ScenarioResult.model_validate(output)
    if result.model_dump() != calculate_scenarios(store, result.request).model_dump():
        raise ValueError("Scenario results or assumptions differ from recomputation")
    return result
