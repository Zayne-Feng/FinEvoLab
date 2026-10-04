"""Select annual entity-level XBRL observations from SEC snapshots."""

from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from finevolab.ingestion import Evidence, FinancialMetric


TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
}


def extract_sec(source: dict, root: Path) -> dict:
    raw = (root / source["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("SEC snapshot hash mismatch")
    document = json.loads(raw)
    if int(document["cik"]) != int(source["cik"]):
        raise ValueError("SEC entity mismatch")
    metrics, evidence, missing = [], [], []
    for name, tags in TAGS.items():
        for year in source["fiscal_years"]:
            candidates = []
            for tag in tags:
                facts = document["facts"]["us-gaap"].get(tag, {}).get("units", {}).get("USD", [])
                for index, fact in enumerate(facts):
                    if fact.get("form") != "10-K" or fact.get("fp") != "FY" or "start" not in fact:
                        continue
                    start, end = date.fromisoformat(fact["start"]), date.fromisoformat(fact["end"])
                    if end.year == year and 330 <= (end - start).days <= 400:
                        candidates.append((fact, tag, index))
                if candidates:
                    break
            if not candidates:
                missing.append({"metric": name, "fiscal_year": year, "status": "extraction_failed"})
                continue
            candidates.sort(key=lambda item: (item[0]["filed"], item[0]["accn"]), reverse=True)
            fact, tag, index = candidates[0]
            latest = [item[0] for item in candidates
                      if (item[0]["filed"], item[0]["accn"]) == (fact["filed"], fact["accn"])]
            if len({(item["val"], item["start"], item["end"]) for item in latest}) != 1:
                raise ValueError("Conflicting annual XBRL contexts")
            locator = f"/facts/us-gaap/{tag}/units/USD/{index}"
            evidence_id = hashlib.sha256((source["sha256"] + locator).encode()).hexdigest()[:24]
            evidence.append(Evidence(
                id=evidence_id, source_sha256=source["sha256"], source_url=source["url"],
                company=source["company"], report_year=fact["fy"], report_type="10-K",
                section="income-statements", locator=locator,
                text=json.dumps({"tag": tag, "unit": "USD", **fact}, sort_keys=True),
            ).model_dump())
            metrics.append(FinancialMetric(
                company=source["company"], name=name, fiscal_year=year, value=Decimal(str(fact["val"])),
                currency="USD", unit="USD", period_start=fact["start"], period_end=fact["end"],
                period_kind="annual_flow", evidence_id=evidence_id, report_year=fact["fy"],
                extraction_method="sec_companyfacts_annual_context",
            ).model_dump(mode="json"))
    return {"source": {**source, "profile": "sec_companyfacts", "report_type": "companyfacts"},
            "metrics": metrics, "evidence": evidence, "missing": missing}
