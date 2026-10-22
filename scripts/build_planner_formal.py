"""Build bilingual, template-separated planner cases from verified filing metadata."""

from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.grounded import RequestPlan
from finevolab.planner_benchmark import normalize_question, validate_cases
from finevolab.runtime import write_json


def build():
    target = ROOT / "data/training/planner_formal/cases_v1.json"
    if target.exists():
        raise ValueError("The dataset already exists")
    sources = json.loads((ROOT / "data/filing_sources.json").read_text(encoding="utf-8"))
    documents = [{"id": s["sha256"], "company": s["company"], "report_year": s["fiscal_year"],
        "path": s["path"], "sha256": s["sha256"]} for s in sources]
    documents += json.loads((ROOT / "data/evaluation/scope_v3/documents.json").read_text(encoding="utf-8"))
    for document in documents:
        if sha256(ROOT / document["path"]) != document["sha256"]:
            raise ValueError("A filing snapshot changed")
    catalogue = {(d["company"], d["report_year"]): d for d in documents}
    known = ["MSFT", "AAPL", "GOOGL", "AMZN", "META"]
    names = {"MSFT": "Microsoft", "AAPL": "Apple", "GOOGL": "Alphabet", "AMZN": "Amazon", "META": "Meta",
             "NVDA": "Nvidia", "TSLA": "Tesla", "ADBE": "Adobe"}
    metrics = [("revenue", "revenue", "营收"), ("operating_income", "operating income", "营业利润"),
               ("net_income", "net income", "净利润")]
    templates = (ROOT / "data/training/planner_formal/templates.tsv").read_text(encoding="utf-8").splitlines()
    counts = Counter(line.split("\t")[0] for line in templates)
    if counts != {"development": 30, "validation": 30, "test": 60}:
        raise ValueError(f"Unexpected template allocation: {counts}")
    cases, split_counts = [], Counter()
    for line in templates:
        split, category, kind, english, chinese = line.split("\t")
        number = split_counts[split]
        split_counts[split] += 1
        for scenario in range(10 if split == "development" else 2):
            company = known[(number + scenario) % 5]
            other = known[(number + scenario + 1) % 5]
            if split == "test" and scenario == 1:
                company = ["NVDA", "TSLA", "ADBE"][number % 3]
            year = 2023 + (number + scenario) % 2
            if kind == "missing_company" and split == "development":
                year = 2015 + scenario
            metric, en_metric, zh_metric = metrics[(number + scenario) % 3]
            plan = dict(intent=kind, companies=[company], fiscal_year=year, report_year=year,
                        metrics=[metric], topic="revenue", scope="annual_fiscal", numeric_intent="metric")
            selected = []
            if kind in {"comparison", "alignment"}:
                plan["companies"] = [company, other]
                if kind == "alignment":
                    plan["metrics"] = ["revenue"]
            elif kind == "summary":
                plan["metrics"] = [m[0] for m in metrics]
            elif kind.startswith("e_"):
                plan.update(intent="explanation", topic=kind[2:], metrics=["net_income" if kind == "e_profit" else "revenue"])
                if "operating-income" in english or "operating-profit" in english:
                    plan["metrics"] = ["operating_income"]
            elif kind.startswith("h_"):
                numeric = kind[2:]
                plan.update(intent="hybrid", numeric_intent=numeric,
                    metrics=["net_income" if numeric == "growth" else "revenue"],
                    topic="profit" if numeric == "growth" else "risk" if numeric == "comparison" else "revenue")
                if numeric == "comparison":
                    plan["companies"] = [company, other]
            elif kind in {"quarter", "forecast", "series"}:
                plan.update(intent="unavailable", scope="unsupported")
            elif kind in {"missing_year", "missing_company", "fx"}:
                plan.update(intent="clarification", scope="ambiguous")
                if kind == "missing_year":
                    plan.update(fiscal_year=1900, report_year=None)
                if kind == "missing_company":
                    plan["companies"] = []
            elif kind.startswith("selected_"):
                document = catalogue[company, year]
                selected = [{key: document[key] for key in ("company", "report_year", "id")}]
                plan["intent"] = "explanation" if kind == "selected_risk" else "metric"
                if kind == "selected_prior":
                    plan["fiscal_year"] = year - 1
                if kind == "selected_risk":
                    plan.update(topic="risk", metrics=["revenue"])
            expected = RequestPlan.model_validate(plan, strict=True).model_dump()
            fields = ["intent", "companies", "scope"]
            if category != "boundary" or kind == "missing_year":
                fields.append("fiscal_year")
            if category != "boundary":
                fields.append("report_year")
                if kind not in {"alignment", "e_risk", "selected_risk"}:
                    fields.append("metrics")
            if expected["intent"] in {"explanation", "hybrid"}:
                fields.append("topic")
            if expected["intent"] == "hybrid":
                fields.append("numeric_intent")
            for language, text in (("en", english), ("zh", chinese)):
                shown_company = names[company] if (number + scenario) % 2 else company
                question = text.format(c=shown_company, d=other, y=year, p=year-1,
                                       m=en_metric if language == "en" else zh_metric)
                cases.append(dict(id=f"{split}_{number:02d}_{scenario}_{language}",
                    group_id=f"{split}_{number:02d}", split=split, category=category, language=language,
                    semantic_fields=fields, input={"question": question, "selected_documents": selected}, expected=expected))
    identities = {}
    for row in cases:
        identity = (normalize_question(row["input"]["question"]), json.dumps(row["input"]["selected_documents"], sort_keys=True))
        if identity in identities:
            raise ValueError(f"Duplicate model inputs: {identities[identity]} / {row['id']}")
        identities[identity] = row["id"]
    validate_cases(cases)
    old = json.loads((ROOT / "data/training/planner_pilot/cases_v2.json").read_text(encoding="utf-8"))["cases"]
    previous = {normalize_question(row["input"]["question"]) for row in old}
    if any(normalize_question(row["input"]["question"]) in previous for row in cases):
        raise ValueError("An exact pilot question was reused")
    write_json(target, {"version": "planner_formal_v1", "source_role": "authored_synthetic_bilingual_planner",
        "documents": documents, "cases": cases})
    print(json.dumps({"status": "built", "cases": dict(Counter(c["split"] for c in cases)),
        "template_groups": dict(counts), "source_documents": len(documents)}))


if __name__ == "__main__":
    build()
