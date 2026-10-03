"""Download complete annual filings with SEC submission provenance."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    companies = json.loads((ROOT / "configs/sec_companies.json").read_text())
    output = ROOT / "data/filing_sources.json"
    previous = json.loads(output.read_text()) if output.exists() else []
    known = {item["path"]: item["sha256"] for item in previous if item.get("status") == "downloaded"}
    sources = []
    with requests.Session() as session:
        session.headers["User-Agent"] = "FinEvoLab/0.1 public-filings-research"
        session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])))
        for company in companies:
            submissions = ROOT / "data/raw" / f"{company['company'].lower()}_submissions.json"
            if not submissions.exists():
                response = session.get(f"https://data.sec.gov/submissions/CIK{company['cik']}.json", timeout=(10, 60))
                response.raise_for_status()
                submissions.write_bytes(response.content)
            document = json.loads(submissions.read_bytes())
            batches = [document["filings"]["recent"]]
            candidates = {}

            def collect(batch):
                for index, form in enumerate(batch["form"]):
                    end = batch["reportDate"][index]
                    if form == "10-K" and end[:4] in {"2022", "2023", "2024"}:
                        year = int(end[:4])
                        candidate = {"accession": batch["accessionNumber"][index],
                                     "document": batch["primaryDocument"][index],
                                     "publication_date": batch["filingDate"][index], "period_end": end}
                        if year not in candidates or candidate["publication_date"] > candidates[year]["publication_date"]:
                            candidates[year] = candidate
            collect(batches[0])
            if len(candidates) < 3:
                for older in document["filings"]["files"]:
                    if older["filingTo"] < "2022-01-01" or older["filingFrom"] > "2025-12-31":
                        continue
                    response = session.get("https://data.sec.gov/submissions/" + older["name"], timeout=(10, 60))
                    response.raise_for_status()
                    collect(response.json())
            for year in (2022, 2023, 2024):
                entry = {**company, "fiscal_year": year, "report_type": "10-K"}
                if year not in candidates:
                    sources.append({**entry, "status": "filing_not_found"})
                    continue
                filing = candidates[year]
                url = (f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/"
                       + filing["accession"].replace("-", "") + "/" + filing["document"])
                relative = f"data/raw/sec_{company['company'].lower()}_{year}.html"
                target = ROOT / relative
                try:
                    if not target.exists():
                        response = session.get(url, timeout=(10, 60))
                        response.raise_for_status()
                        if len(response.content) < 10000 or b"<html" not in response.content.lower():
                            raise ValueError("The response is not a complete HTML filing")
                        temporary = target.with_suffix(".download")
                        temporary.write_bytes(response.content)
                        temporary.replace(target)
                        time.sleep(0.55)
                    raw = target.read_bytes()
                    digest = hashlib.sha256(raw).hexdigest()
                    if relative in known and known[relative] != digest:
                        raise ValueError("Existing filing differs from the pinned manifest")
                    sources.append({**entry, **filing, "url": url, "path": relative,
                                    "status": "downloaded", "sha256": digest, "bytes": len(raw),
                                    "submissions_sha256": hashlib.sha256(submissions.read_bytes()).hexdigest(),
                                    "retrieved_at": datetime.fromtimestamp(target.stat().st_mtime, timezone.utc).isoformat()})
                    print(f"Verified full annual filing: {company['company']} FY{year} ({len(raw)} bytes)")
                except (requests.RequestException, ValueError) as error:
                    sources.append({**entry, **filing, "url": url, "status": "download_failed",
                                    "error_type": type(error).__name__})
                    print(f"Annual filing unavailable: {company['company']} FY{year}")
                output.write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
    output.write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
