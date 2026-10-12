"""Extract annual observations from Inline XBRL and explicit report tables."""

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup

from finevolab.ingestion import Evidence, FinancialMetric, extract_report
from finevolab.sec_ingestion import TAGS
from finevolab.tools import FinancialStore


ROOT = Path(__file__).resolve().parents[1]
LABELS = {"revenue": "revenue", "total revenue": "revenue", "total revenues": "revenue",
          "net sales": "revenue", "total net sales": "revenue", "operating income": "operating_income",
          "income from operations": "operating_income", "operating income (loss)": "operating_income",
          "net income": "net_income", "net income (loss)": "net_income", "net loss": "net_income"}


def local_name(tag) -> str:
    return tag.name.lower().split(":")[-1] if tag.name else ""


def parse_number(text: str) -> Decimal:
    text = text.strip().replace(",", "").replace("$", "").replace("\u2212", "-")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1].strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        raise ValueError("Unrecognized monetary value")
    value = Decimal(text)
    if not value.is_finite():
        raise ValueError("A finite monetary value is required")
    return value


def parse_date(text: str) -> date:
    text = re.sub(r"([A-Za-z])(?=\d)", r"\1 ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    for pattern in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(re.sub(r"\s+", " ", text.strip()), pattern).date()
        except ValueError:
            continue
    raise ValueError("Unrecognized fiscal period date")


def _observation(company, name, year, value, start, end, report_year, digest, locator, text, method):
    identifier = hashlib.sha256((digest + locator + text).encode()).hexdigest()[:24]
    evidence = Evidence(id=identifier, source_sha256=digest, source_url=f"/api/uploads/{digest}/original",
                        company=company, report_year=report_year, report_type="uploaded",
                        section="income-statements", locator=locator, text=text)
    metric = FinancialMetric(company=company, name=name, fiscal_year=year, value=value, currency="USD", unit="USD",
                             period_start=start, period_end=end, period_kind="annual_flow", evidence_id=identifier,
                             report_year=report_year, extraction_method=method)
    return {"metric": metric.model_dump(mode="json"), "evidence": evidence.model_dump()}


def inline_observations(soup, company, report_year, digest):
    contexts, units = {}, {}
    for node in soup.find_all(lambda tag: local_name(tag) == "context"):
        start = node.find(lambda tag: local_name(tag) == "startdate")
        end = node.find(lambda tag: local_name(tag) == "enddate")
        if start is None or end is None or node.find(lambda tag: local_name(tag) in {"segment", "scenario"}):
            continue
        try:
            beginning, ending = date.fromisoformat(start.get_text(strip=True)), date.fromisoformat(end.get_text(strip=True))
        except ValueError:
            continue
        if 330 <= (ending - beginning).days <= 400:
            contexts[node.get("id")] = (beginning.isoformat(), ending.isoformat())
    for node in soup.find_all(lambda tag: local_name(tag) == "unit"):
        measures = node.find_all(lambda tag: local_name(tag) == "measure")
        if len(measures) == 1 and measures[0].get_text(strip=True).lower() == "iso4217:usd":
            units[node.get("id")] = "USD"
    focus = [node.get_text(strip=True) for node in soup.find_all(attrs={"name": re.compile(r"DocumentFiscalYearFocus$", re.I)})]
    period = [node.get_text(" ", strip=True) for node in soup.find_all(attrs={"name": re.compile(r"DocumentPeriodEndDate$", re.I)})]
    offset = 0
    if focus and period:
        if len(set(focus)) != 1 or int(focus[0]) != report_year:
            raise ValueError("The uploaded fiscal year conflicts with the document")
        offset = report_year - parse_date(period[0]).year
    symbols = {node.get_text(strip=True).upper() for node in soup.find_all(attrs={"name": re.compile(r"TradingSymbol$", re.I)})}
    if symbols and company not in symbols:
        raise ValueError("The uploaded company conflicts with the document")
    reverse = {("us-gaap:" + tag).lower(): (name, rank) for name, tags in TAGS.items() for rank, tag in enumerate(tags)}
    grouped = defaultdict(list)
    for index, node in enumerate(soup.find_all(lambda tag: local_name(tag) == "nonfraction")):
        definition = reverse.get(node.get("name", "").lower())
        context = contexts.get(node.get("contextref"))
        if definition is None or context is None or node.get("unitref") not in units:
            continue
        if node.get("format", "").split(":")[-1].lower() not in {"", "num-dot-decimal", "numdotdecimal"}:
            continue
        try:
            raw_value = "".join(str(part) for part in node.find_all(string=True)
                                if not part.find_parent(lambda tag: local_name(tag) == "exclude")).strip()
            value = parse_number(raw_value) * Decimal(10) ** int(node.get("scale", "0"))
            if node.get("sign") == "-":
                if value < 0:
                    raise ValueError("Conflicting monetary signs")
                value = -value
        except (ValueError, InvalidOperation):
            continue
        name, priority = definition
        year = int(context[1][:4]) + offset
        if year > report_year or year < report_year - 3:
            continue
        locator = f"ix:nonfraction|index={index}|context={node.get('contextref')}"
        item = _observation(company, name, year, value, *context, report_year, digest, locator,
                            json.dumps({"tag": node.get("name"), "context": node.get("contextref"),
                                        "raw_value": raw_value, "scale": node.get("scale", "0"),
                                        "sign": node.get("sign", "+"), "unit": "USD", "period": context}), "inline_xbrl")
        grouped[(name, year)].append((priority, item))
    observations, conflicts = [], []
    for (name, year), values in grouped.items():
        priority = min(item[0] for item in values)
        eligible = [item for rank, item in values if rank == priority]
        definitions = {(item["metric"]["value"], item["metric"]["period_start"], item["metric"]["period_end"]) for item in eligible}
        if len(definitions) != 1:
            conflicts.append({"metric": name, "fiscal_year": year, "reason": "conflicting_annual_contexts"})
        else:
            observations.append(eligible[0])
    return observations, conflicts


def table_observations(tables, text, company, report_year, digest):
    observations, unresolved = [], []
    periods = {int(year): (start, end) for year, start, end in re.findall(
        r"FY\s*(20\d{2})\s*[:=]?\s*(20\d{2}-\d{2}-\d{2})\s*(?:to|through)\s*(20\d{2}-\d{2}-\d{2})", text, re.I)}
    for locator, rows, local_text in tables:
        context = local_text + " " + text[:1500]
        if not re.search(r"\bUSD\b|U\.?S\.? dollars", context, re.I):
            currency = None
        else:
            currency = "USD"
        multipliers = {Decimal("1000000") for _ in re.finditer(r"in millions", context, re.I)}
        multipliers.update(Decimal("1000") for _ in re.finditer(r"in thousands", context, re.I))
        if not multipliers and re.search(r"in (?:USD|U\.?S\.? dollars)(?!\s*(?:millions|thousands))", context, re.I):
            multipliers.add(Decimal(1))
        header = next((row for row in rows[:8] if sum(bool(re.fullmatch(r"20\d{2}", cell)) for cell in row) >= 2), None)
        if header is None:
            continue
        columns = [(index, int(cell)) for index, cell in enumerate(header) if re.fullmatch(r"20\d{2}", cell)]
        for row_index, row in enumerate(rows):
            if not row:
                continue
            name = LABELS.get(re.sub(r"\s+", " ", row[0].strip().lower()).rstrip(":"))
            if name is None:
                continue
            for column, year in columns:
                if column >= len(row) or len(row) != len(header):
                    unresolved.append({"locator": locator, "metric": name, "fiscal_year": year, "reason": "column_alignment_unverified"})
                    continue
                try:
                    number = parse_number(row[column])
                except ValueError:
                    continue
                if currency is None or len(multipliers) != 1 or year not in periods:
                    unresolved.append({"locator": f"{locator}|row={row_index}|column={column}", "metric": name,
                                       "fiscal_year": year, "raw_value": row[column], "reason": "currency_unit_or_period_unverified"})
                    continue
                start, end = periods[year]
                if not 330 <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= 400:
                    unresolved.append({"locator": locator, "metric": name, "fiscal_year": year, "reason": "nonannual_period"})
                    continue
                observations.append(_observation(company, name, year, number * next(iter(multipliers)), start, end,
                    report_year, digest, f"{locator}|row={row_index}|column={column}",
                    " | ".join(header) + "\n" + " | ".join(row), "explicit_annual_table"))
    return observations, unresolved


def extract_uploaded_metrics(path: Path, company: str, year: int, pages: list[tuple[str, str]]) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    observations, unresolved = [], []
    if path.suffix.lower() in {".html", ".htm"}:
        soup = BeautifulSoup(path.read_bytes(), "html.parser")
        observations, unresolved = inline_observations(soup, company, year, digest)
        if not observations:
            for source in json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8")):
                if source["sha256"] == digest:
                    if (source["company"], source["fiscal_year"]) != (company, year):
                        raise ValueError("Upload metadata conflicts with the pinned report")
                    report = extract_report({**source, "path": path.name,
                                             "url": f"/api/uploads/{digest}/original"}, path.parent)
                    evidence = {item["id"]: item for item in report["evidence"]}
                    observations = [{"metric": item, "evidence": evidence[item["evidence_id"]]} for item in report["metrics"]]
                    break
        tables = [(f"table={index}", [[cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
                                      for row in table.find_all("tr")], table.get_text(" ", strip=True)) for index, table in enumerate(soup.find_all("table"))
                  if not table.find("table")]
    else:
        tables = []
        for locator, text in pages:
            rows = []
            for line in text.splitlines():
                match = re.match(r"^(.*?)\s+((?:\(?-?[\d,]+(?:\.\d+)?\)?\s+)+\(?-?[\d,]+(?:\.\d+)?\)?)\s*$", line.strip())
                if match:
                    rows.append([match[1].strip(), *match[2].split()])
            tables.append((locator, rows, text))
    if not observations:
        observations, pending = table_observations(tables, "\n".join(text for _, text in pages), company, year, digest)
        unresolved.extend(pending)
    if not observations and not unresolved:
        unresolved.append({"reason": "no_supported_annual_metric_structure"})
    store = FinancialStore(ROOT / "data/processed/reports.json")
    counts = defaultdict(int)
    for item in observations:
        metric = FinancialMetric.model_validate(item["metric"])
        try:
            expected = store.get_metric(company, metric.name, metric.fiscal_year)
        except ValueError:
            item["verification"] = {"status": "reference_unavailable"}
        else:
            fields = ("value", "currency", "unit", "period_start", "period_end", "period_kind")
            differences = [field for field in fields if getattr(metric, field) != getattr(expected, field)]
            item["verification"] = {"status": "conflict" if differences else "matched_snapshot",
                                    "differences": differences, "reference_evidence_id": expected.evidence_id}
        counts[item["verification"]["status"]] += 1
    has_conflicts = counts["conflict"] or any(item.get("reason") == "conflicting_annual_contexts" for item in unresolved)
    status = "conflict" if has_conflicts else "matched_snapshot" if observations and not unresolved and counts["matched_snapshot"] == len(observations) else "review_required"
    return {"version": "source_metrics_v1", "status": status, "observations": observations,
            "unresolved": unresolved, "counts": dict(counts), "reference_data_version": store.version,
            "promoted_to_corpus": False}
