"""Extract annual income statements with immutable source locations."""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import re

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source_sha256: str
    source_url: str
    company: str
    report_year: int
    report_type: str
    section: str
    locator: str
    text: str


class FinancialMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str
    name: str
    fiscal_year: int
    value: Decimal
    currency: str
    unit: str
    period_start: str
    period_end: str
    period_kind: str
    evidence_id: str
    report_year: int
    extraction_method: str


def extract_report(source: dict, root: Path) -> dict:
    path = root / source["path"]
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != source["sha256"]:
        raise ValueError("Report snapshot hash does not match the source manifest")
    if source["currency"] != "USD" or source["period_end"] != f"{source['fiscal_year']}-06-30":
        raise ValueError("Report currency or annual period does not match the parsing profile")
    soup = BeautifulSoup(raw, "html.parser")
    if source["profile"] != "microsoft_annual":
        raise ValueError("Unsupported report profile")
    section_tables = soup.select("article#income-statements table")
    tables = [(index, table) for index, table in enumerate(section_tables)
              if any(row.find(["td", "th"]) and row.find(["td", "th"]).get_text(" ", strip=True) == "Total revenue"
                     for row in table.find_all("tr"))]
    if len(tables) != 1:
        raise ValueError("Exactly one annual income statement is required")
    table_index, table = tables[0]
    rows = [
        [" ".join(cell.stripped_strings) for cell in row.find_all(["td", "th"])]
        for row in table.find_all("tr")
    ]
    if "In millions" not in " ".join(rows[0]):
        raise ValueError("Unrecognized income statement unit")
    header = next(row for row in rows if row[0].startswith("Year Ended June 30"))
    columns = [(index, int(value)) for index, value in enumerate(header)
               if re.fullmatch(r"20\d{2}", value)]
    if not columns or columns[0][1] != source["fiscal_year"]:
        raise ValueError("Report year does not match the income statement")
    metrics, evidence = [], []
    labels = {"Total revenue": "revenue", "Operating income": "operating_income", "Net income": "net_income"}
    for row_index, row in enumerate(rows):
        if not row or row[0] not in labels:
            continue
        locator = f"css:article#income-statements table|table={table_index}|row={row_index}"
        text = " | ".join(rows[0]) + "\n" + " | ".join(header) + "\n" + " | ".join(row)
        evidence_id = hashlib.sha256((digest + locator + text).encode()).hexdigest()[:24]
        item = Evidence(
            id=evidence_id, source_sha256=digest, source_url=source["url"],
            company=source["company"], report_year=source["fiscal_year"],
            report_type=source["report_type"], section="income-statements",
            locator=locator, text=text,
        )
        evidence.append(item.model_dump())
        for index, year in columns:
            number = row[index].replace("$", "").replace(",", "").strip()
            if not re.fullmatch(r"\d+(\.\d+)?", number):
                raise ValueError("Unrecognized financial value")
            metrics.append(FinancialMetric(
                company=source["company"], name=labels[row[0]], fiscal_year=year,
                value=Decimal(number) * 1_000_000, currency=source["currency"], unit="USD",
                period_start=f"{year - 1}-07-01", period_end=f"{year}-06-30",
                period_kind="annual_flow", evidence_id=evidence_id,
                report_year=source["fiscal_year"], extraction_method="html_table_profile",
            ).model_dump(mode="json"))
    if len(evidence) != len(labels):
        raise ValueError("Required income statement rows are missing")
    return {
        "source": {**source, "sha256": digest, "bytes": len(raw),
                   "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()},
        "evidence": evidence, "metrics": metrics,
    }
