"""Query source-bound annual metrics and compute comparable growth."""

from decimal import Decimal
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from finevolab.ingestion import FinancialMetric
from finevolab.derived import DerivedMetric, DerivedResult, calculate_derived
from finevolab.runtime import fingerprint
from finevolab.provenance import file_digest


class GrowthResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str
    metric: str
    fiscal_year: int
    previous_year: int
    value: Decimal
    unit: str
    formula: str
    inputs: list[FinancialMetric]
    evidence_ids: list[str]


class ComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    fiscal_year: int
    values: list[FinancialMetric]
    periods_aligned: bool


class FinancialStore:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8"))
        self.version = self.data["version"]
        self.aliases = json.loads((Path(__file__).resolve().parents[1] / "configs/company_aliases.json").read_text())
        self.evidence = {item["id"]: item for report in self.data["reports"] for item in report["evidence"]}
        self.metrics = [FinancialMetric.model_validate(item)
                        for report in self.data["reports"] for item in report["metrics"]]
        if any(metric.evidence_id not in self.evidence for metric in self.metrics):
            raise ValueError("Metric evidence is missing")
        self.upload_ids, self.uploads, self.source_issues = [], [], []
        self.upload_root = path.parents[1] / "uploads"
        self.invalid_metrics = set()
        self.source_signature = fingerprint({"reports": file_digest(path)})

    @classmethod
    def from_uploads(cls, path: Path, identifiers: list[str], upload_root: Path):
        from finevolab.uploads import verified_upload
        if len(identifiers) > 5 or not identifiers:
            raise ValueError("Select between one and five uploaded reports")
        store = cls(path)
        store.upload_root = upload_root
        store.upload_ids = sorted(set(identifiers))
        store.uploads = [verified_upload(identifier, upload_root) for identifier in store.upload_ids]
        store.metrics, store.evidence = [], {}
        reports = []
        for document in store.uploads:
            observations = document["metric_results"]["observations"]
            store.metrics.extend(FinancialMetric.model_validate(item["metric"]) for item in observations)
            store.evidence.update((item["evidence"]["id"], item["evidence"]) for item in observations)
            for item in document["metric_results"]["unresolved"]:
                if item.get("reason") == "conflicting_annual_contexts":
                    store.invalid_metrics.add((document["company"], item["metric"], item["fiscal_year"]))
            store.source_issues.extend({"document_id": document["id"], "metric": item["metric"]["name"],
                "fiscal_year": item["metric"]["fiscal_year"], **item["verification"]}
                for item in observations if item["verification"]["status"] == "conflict")
            reports.append({"source": {key: document[key] for key in ("id", "company", "report_year", "sha256")},
                "metrics": [item["metric"] for item in observations], "evidence": [item["evidence"] for item in observations]})
        store.source_signature = fingerprint({"uploads": [{"id": item["id"], "parser": item["parser_signature"],
                                                           "company": item["company"], "year": item["report_year"]}
                                                          for item in store.uploads]})
        store.version = store.source_signature
        store.data = {"version": store.version, "reports": reports}
        return store

    def company_id(self, company: str) -> str:
        normalized = company.strip().rstrip(".").casefold()
        return self.aliases.get(normalized, normalized.upper())

    def get_metric(self, company: str, name: str, fiscal_year: int) -> FinancialMetric:
        company = self.company_id(company)
        if (company, name, fiscal_year) in self.invalid_metrics:
            raise ValueError("Conflicting annual contexts in the selected source")
        candidates = [metric for metric in self.metrics
                      if (metric.company, metric.name, metric.fiscal_year) == (company, name, fiscal_year)]
        if not candidates:
            raise ValueError("Requested annual metric is unavailable")
        latest = max(metric.report_year for metric in candidates)
        selected = [metric for metric in candidates if metric.report_year == latest]
        definitions = {(item.value, item.currency, item.unit, item.period_start,
                        item.period_end, item.period_kind) for item in selected}
        if len(definitions) != 1:
            raise ValueError("Conflicting metric disclosures")
        return sorted(selected, key=lambda item: (not item.extraction_method.startswith("sec_"), item.evidence_id))[0]

    def compare(self, companies: list[str], name: str, fiscal_year: int) -> ComparisonResult:
        companies = [self.company_id(company) for company in companies]
        if len(set(companies)) < 2:
            raise ValueError("At least two distinct companies are required")
        metrics = [self.get_metric(company, name, fiscal_year) for company in dict.fromkeys(companies)]
        if len({(item.currency, item.unit, item.period_kind) for item in metrics}) != 1:
            raise ValueError("Company metrics use incompatible units or periods")
        return ComparisonResult(metric=name, fiscal_year=fiscal_year, values=metrics,
                                periods_aligned=len({(item.period_start, item.period_end) for item in metrics}) == 1)

    def derive(self, company: str, metric: DerivedMetric, fiscal_year: int) -> DerivedResult:
        return calculate_derived(self, company, metric, fiscal_year)

    def growth(self, company: str, name: str, fiscal_year: int) -> GrowthResult:
        current = self.get_metric(company, name, fiscal_year)
        previous = self.get_metric(company, name, fiscal_year - 1)
        if (current.unit, current.currency, current.period_kind) != (
            previous.unit, previous.currency, previous.period_kind
        ) or current.period_kind != "annual_flow":
            raise ValueError("Metric units or periods are not comparable")
        if previous.value == 0:
            raise ValueError("Growth is undefined for a zero baseline")
        return GrowthResult(
            company=current.company, metric=name, fiscal_year=fiscal_year, previous_year=fiscal_year - 1,
            value=(current.value / previous.value - 1) * 100, unit="percent",
            formula="(current / previous - 1) * 100", inputs=[previous, current],
            evidence_ids=list(dict.fromkeys([previous.evidence_id, current.evidence_id])),
        )


class BoundedFinancialStore:
    def __init__(self, store: FinancialStore, executor):
        self.store, self.executor = store, executor

    def __getattr__(self, name):
        return getattr(self.store, name)

    def get_metric(self, company, name, fiscal_year):
        return self.executor.call("get_financial_metrics", {"company": company, "metric": name, "fiscal_year": fiscal_year},
            lambda: self.store.get_metric(company, name, fiscal_year), FinancialMetric)

    def growth(self, company, name, fiscal_year):
        return self.executor.call("calculate_metrics", {"company": company, "metric": name, "fiscal_year": fiscal_year},
            lambda: self.store.growth(company, name, fiscal_year), GrowthResult)

    def derive(self, company, metric, fiscal_year):
        from finevolab.derived import validate_derived
        result = self.executor.call("calculate_metrics", {"company": company, "metric": metric, "fiscal_year": fiscal_year},
            lambda: self.store.derive(company, metric, fiscal_year), DerivedResult)
        return validate_derived(self.store, result)

    def compare(self, companies, name, fiscal_year):
        return self.executor.call("compare_companies", {"companies": companies, "metric": name, "fiscal_year": fiscal_year},
            lambda: self.store.compare(companies, name, fiscal_year), ComparisonResult)
