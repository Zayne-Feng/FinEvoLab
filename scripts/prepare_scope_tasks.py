"""Freeze semantic task splits with independently located SEC financial references."""

from collections import Counter
from datetime import date
from decimal import Decimal
import itertools
import json
from pathlib import Path
import re
import sys

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.evaluation import TaskAnswer, source_audit
from finevolab.experiments import sha256
from finevolab.ingestion import FinancialMetric
from finevolab.runtime import fingerprint, write_json
from finevolab.sec_ingestion import TAGS
from finevolab.tools import FinancialStore, GrowthResult

DIRECTORY = ROOT / "data/evaluation/scope_v3"
NAMES = ["revenue", "operating_income", "net_income"]
COHORTS = {
    "development": [("MSFT", 2023), ("AAPL", 2024), ("GOOGL", 2023), ("AMZN", 2022)],
    "validation": [("MSFT", 2024), ("AAPL", 2022), ("META", 2023), ("AMZN", 2023)],
    "test": [("MSFT", 2022), ("AAPL", 2023), ("GOOGL", 2022), ("GOOGL", 2024),
             ("AMZN", 2024), ("META", 2022), ("META", 2024)],
}
TOPICS = {"revenue": "revenue changes and disclosed business drivers",
          "operating_income": "operating income changes and disclosed cost or business drivers",
          "net_income": "net income changes and disclosed tax or business drivers",
          "risk": "specific operating risks and their possible effects"}


def answer(metrics=(), calculations=(), aligned=None, status="complete"):
    identifiers = {m.evidence_id for m in metrics}
    identifiers.update(key for calc in calculations for key in calc.evidence_ids)
    return TaskAnswer(status=status, answer="Source-bound reference", metrics=list(metrics),
        calculations=list(calculations), periods_aligned=aligned, evidence_ids=sorted(identifiers)).model_dump(mode="json")


def independent_upload_references(sources):
    references, audit = {}, []
    for source in sources:
        if source.get("report_type") != "10-K":
            continue
        company, report_year = source["company"], source["fiscal_year"]
        facts_source = next(item for item in sources if item["company"] == company and item["report_type"] == "companyfacts")
        path = ROOT / facts_source["path"]
        if sha256(path) != facts_source["sha256"] or sha256(ROOT / source["path"]) != source["sha256"]:
            raise ValueError("An acceptance source changed")
        facts = json.loads(path.read_text(encoding="utf-8"))["facts"]["us-gaap"]
        offset = report_year - int(source["period_end"][:4])
        for name in NAMES:
            for year in (report_year - 1, report_year):
                candidates = []
                for tag in TAGS[name]:
                    for index, fact in enumerate(facts.get(tag, {}).get("units", {}).get("USD", [])):
                        if fact.get("accn") != source["accession"] or "start" not in fact:
                            continue
                        duration = (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days
                        if 330 <= duration <= 400 and int(fact["end"][:4]) + offset == year:
                            candidates.append((fact, f"/facts/us-gaap/{tag}/units/USD/{index}"))
                    if candidates:
                        break
                if not candidates or len({(c[0]["val"], c[0]["start"], c[0]["end"]) for c in candidates}) != 1:
                    raise ValueError(f"Ambiguous independent reference: {company} {report_year} {year} {name}")
                fact, locator = candidates[0]
                evidence_id = fingerprint({"source": facts_source["sha256"], "locator": locator})[:24]
                metric = FinancialMetric(company=company, name=name, fiscal_year=year, value=Decimal(str(fact["val"])),
                    currency="USD", unit="USD", period_start=fact["start"], period_end=fact["end"],
                    period_kind="annual_flow", report_year=report_year, evidence_id=evidence_id,
                    extraction_method="independent_companyfacts_accession_reference")
                references[(company, report_year, year, name)] = metric
                audit.append({"metric": metric.model_dump(mode="json"), "source_path": facts_source["path"],
                    "source_sha256": facts_source["sha256"], "locator": locator, "filing_sha256": source["sha256"],
                    "accession": source["accession"]})
    return references, audit


def growth(current, previous):
    return GrowthResult(company=current.company, metric=current.name, fiscal_year=current.fiscal_year,
        previous_year=previous.fiscal_year, value=(current.value - previous.value) / previous.value * 100,
        unit="percent", formula="(current / previous - 1) * 100", inputs=[previous, current],
        evidence_ids=[previous.evidence_id, current.evidence_id])


def raw_anchors(source, focus):
    soup = BeautifulSoup((ROOT / source["path"]).read_bytes(), "html.parser")
    for node in soup.find_all(["script", "style", "ix:header", "ix:hidden"]):
        node.decompose()
    metric = {"revenue": r"revenue|net sales", "operating_income": r"operating income|income from operations|operating profit",
              "net_income": r"net income|net loss|income tax", "risk": r"adversely|risk|uncertain"}[focus]
    cause = r"primarily|driven|due to|reflect|attribut|offset|adversely|could|may"
    result, seen = [], set()
    for index, node in enumerate(soup.find_all(["p", "div"])):
        if node.find_parent("table") or node.find(["p", "div"]):
            continue
        text = " ".join(node.stripped_strings)
        if not 150 <= len(text) <= 3500 or not re.search(metric, text, re.I) or not re.search(cause, text, re.I):
            continue
        if text in seen:
            continue
        seen.add(text)
        result.append({"source_sha256": source["sha256"], "source_path": source["path"],
            "locator": f"raw:block={index}", "quote": text[:250], "paragraph": text})
    return result


def main():
    if (DIRECTORY / "manifest.json").exists():
        raise ValueError("The scope task bank is already frozen")
    store = FinancialStore(ROOT / "data/processed/reports.json")
    sources = json.loads((ROOT / "data/acceptance_sources.json").read_text(encoding="utf-8"))
    references, audit = independent_upload_references(sources)
    documents = [{"id": item["sha256"], "company": item["company"], "report_year": item["fiscal_year"],
                  "path": item["path"], "sha256": item["sha256"]} for item in sources if item["report_type"] == "10-K"]
    legacy_sources = json.loads((ROOT / "data/filing_sources.json").read_text(encoding="utf-8"))
    tasks, all_keys = {}, set()
    anchor_groups = {}

    def add(split, company, year, operation, metrics, *, companies=None, report_year=None, upload=False,
            focus=None, scenario=None, question=None, status=None):
        companies = companies or [company]
        report_year = report_year or year
        upload_ids = []
        if upload:
            upload_ids = [next(d["id"] for d in documents if d["company"] == c and d["report_year"] == report_year) for c in companies]
        key = fingerprint({"companies": sorted(companies), "year": year, "report_year": report_year,
            "operation": operation, "metrics": metrics, "focus": focus, "sources": upload_ids, "scenario": scenario})
        if key in all_keys:
            raise ValueError("Duplicate task semantics")
        all_keys.add(key)
        numeric = operation.split("hybrid_")[-1]
        category = "upload" if upload else "hybrid" if operation.startswith("hybrid") else "explanation" if operation == "explanation" else "boundary" if status else {
            "metric": "metric_query", "growth": "annual_growth", "comparison": "company_comparison", "summary": "integrated_analysis"}[operation]
        if upload:
            get = lambda c, n, y: references[(c, report_year, y, n)]
        else:
            get = store.get_metric
        ref = None
        if operation != "explanation":
            if status:
                ref = answer(status=status)
            elif numeric == "growth":
                ref = answer(calculations=[growth(get(company, name, year), get(company, name, year - 1)) for name in metrics])
            else:
                values = [get(c, name, year) for c in companies for name in metrics]
                aligned = len({(m.period_start, m.period_end) for m in values}) == 1 if numeric == "comparison" else None
                ref = answer(values, aligned=aligned)
        if not question:
            scope = ("Using only the selected reports, " if upload else "")
            labels = ", ".join(n.replace("_", " ") for n in metrics)
            if operation == "explanation":
                question = scope + f"Explain {company}'s FY{year} {TOPICS[focus]} using its FY{report_year} annual report. Cite exact passages."
            elif numeric == "growth":
                question = scope + f"Calculate {company}'s FY{year} annual change in {labels} from the immediately prior fiscal year."
            elif numeric == "comparison":
                question = scope + f"Compare FY{year} {labels} for {', '.join(companies)}. Show amounts and actual fiscal-date differences."
            else:
                question = scope + f"Give {company}'s FY{year} {labels}, with original units and fiscal dates."
            if operation.startswith("hybrid"):
                question += f" Also explain {TOPICS[focus]} for {', '.join(companies)} using the FY{report_year} annual report prose and cite the supporting passages."
        case = {"id": key[:20], "semantic_key": key, "split": split, "category": category,
            "company": company, "companies": companies, "fiscal_year": year, "report_year": report_year,
            "operation": operation, "metrics": metrics, "topic": "risk" if focus == "risk" else "revenue" if focus == "revenue" else "profit",
            "focus": focus, "scenario": scenario, "question": question, "upload_ids": upload_ids,
            "fact_clusters": [f"{c}:{year}" for c in companies], "reference_answer": ref,
            "common_support": not upload and not operation.startswith("hybrid")}
        if focus:
            case["anchor_groups"] = []
            for c in companies:
                group = f"{c}:{report_year}:{focus}"
                if group not in anchor_groups:
                    source = next(item for item in (sources if upload else legacy_sources)
                                  if item["company"] == c and item.get("fiscal_year") == report_year and item.get("report_type") == "10-K")
                    anchor_groups[group] = raw_anchors(source, focus)
                case["anchor_groups"].append(group)
        tasks.setdefault(split, []).append(case)

    for split, cohort in COHORTS.items():
        test = split == "test"
        numeric_specs = [(c, y, [n]) for c, y in cohort for n in NAMES]
        numeric_specs += [(c, y, NAMES) for c, y in cohort]
        for c, y, names in numeric_specs[:15 if test else 10]:
            add(split, c, y, "summary" if len(names) == 3 else "metric", names)
        growth_specs = [(c, y, n) for c, y in cohort if y >= 2023 for n in NAMES]
        for c, y, n in growth_specs[:10 if test else 8]:
            add(split, c, y, "growth", [n])
        pairs = [(list(pair), y) for y in sorted({y for _, y in cohort})
                 for pair in itertools.combinations([c for c, yr in cohort if yr == y], 2)]
        comparisons = [(cs, y, list(ns)) for cs, y in pairs for length in (1, 2, 3)
                       for ns in itertools.combinations(NAMES, length)]
        for cs, y, names in comparisons[:15 if test else 6]:
            add(split, cs[0], y, "comparison", names, companies=cs)
        profiles = [(c, y, focus) for c, y in cohort for focus in ("revenue", "operating_income", "net_income", "risk")]
        for c, y, focus in profiles[:15 if test else 8]:
            add(split, c, y, "explanation", [focus if focus in NAMES else "revenue"], focus=focus)
        for c, y, focus in profiles[:15 if test else 8]:
            name = focus if focus in NAMES else "revenue"
            add(split, c, y, "hybrid_growth" if y >= 2023 else "hybrid_metric", [name], focus=focus)
        if not test:
            company, ry = ("NVDA", 2024) if split == "development" else ("TSLA", 2023)
            for name in NAMES:
                add(split, company, ry, "metric", [name], upload=True)
                add(split, company, ry, "hybrid_growth", [name], upload=True, focus=name)
        else:
            for name in NAMES:
                for op in ("metric", "growth", "hybrid_growth", "explanation"):
                    add(split, "ADBE", 2024, op, [name], upload=True, focus=name if op in {"hybrid_growth", "explanation"} else None)
                add(split, "ADBE", 2023, "hybrid_metric", [name], upload=True, focus=name)
                add(split, "ADBE", 2024, "comparison", [name], upload=True, companies=["ADBE", "TSLA"])
            add(split, "ADBE", 2024, "hybrid_comparison", ["revenue"], upload=True, companies=["ADBE", "TSLA"], focus="revenue")
            add(split, "ADBE", 2023, "metric", ["revenue"], upload=True, report_year=2024,
                question="Using only the selected ADBE FY2024 report, give the comparative FY2023 revenue with its original units and fiscal dates.")
        c, y = cohort[-1]
        boundary = [
            ("quarterly", f"Give {c}'s Q2 FY{y} revenue.", "insufficient_data"),
            ("calendar", f"Give {c}'s {y} revenue; calendar versus fiscal year is undecided.", "clarification_required"),
            ("currency", f"Convert {c}'s FY{y} net income to EUR; I have not supplied an exchange rate.", "clarification_required"),
            ("missing", f"Give {c}'s FY2018 operating income without estimating missing observations.", "insufficient_data"),
            ("self", f"Compare MSFT against Microsoft on FY{y} revenue.", "invalid_parameters"),
            ("unsupported_metric", f"Give {c}'s FY{y} free cash flow.", "insufficient_data"),
            ("asof", f"Give {c}'s FY{y} revenue as known on January 1, {y}; exclude later disclosures.", "insufficient_data"),
            ("forecast", f"Forecast {c}'s FY2030 revenue.", "insufficient_data"),
            ("nonadjacent", f"Calculate {c}'s revenue change from FY2022 to FY2024, not annual year-over-year growth.", "insufficient_data"),
            ("series", f"Give {c}'s revenue series for FY2022, FY2023 and FY2024.", "insufficient_data"),
        ]
        for scenario, q, status in boundary[:10 if test else 4]:
            add(split, c, y, "metric", ["revenue"], scenario=scenario, question=q, status=status)
    counts = {s: len(cases) for s, cases in tasks.items()}
    if counts != {"development": 50, "validation": 50, "test": 100}:
        raise ValueError(f"Unexpected task counts: {counts}")
    write_json(DIRECTORY / "documents.json", documents)
    write_json(DIRECTORY / "financial_reference_audit.json", audit)
    write_json(DIRECTORY / "source_anchors.json", anchor_groups)
    for split, cases in tasks.items():
        write_json(DIRECTORY / f"{split}.json", {"data_version": store.version, "tasks": cases})
    write_json(DIRECTORY / "manifest.json", {"version": "scope_v3", "status": "frozen", "counts": counts,
        "categories": {s: dict(Counter(t["category"] for t in cases)) for s, cases in tasks.items()},
        "split_sha256": {s: sha256(DIRECTORY / f"{s}.json") for s in tasks},
        "files": {n: sha256(DIRECTORY / n) for n in ("documents.json", "financial_reference_audit.json", "source_anchors.json")},
        "source_audit": source_audit(ROOT, store), "independent_upload_references": len(audit),
        "semantic_duplicates": 0, "source_holdout": "ADBE agent answers reserved for final test",
        "known_snapshot_role": "new task combinations over previously observed facts; regression, not unseen facts",
        "anchors": "raw DOM keyword-selected nonexhaustive candidate anchors; diagnostic anchor hit, not gold recall",
        "inference_unit": "task and company-fiscal-year clusters; repetitions are not independent tasks"})
    print(json.dumps({"status": "frozen", "counts": counts, "financial_references": len(audit),
        "anchor_groups": len(anchor_groups), "empty_anchor_groups": [key for key, values in anchor_groups.items() if not values]}))


if __name__ == "__main__":
    main()
