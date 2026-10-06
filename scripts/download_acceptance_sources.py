"""Pin additional SEC filings and company facts without changing the baseline corpus."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.runtime import write_json


def main():
    output = ROOT / "data/acceptance_sources.json"
    previous = json.loads(output.read_text()) if output.exists() else []
    known = {item["path"]: item["sha256"] for item in previous}
    sources = []
    session = requests.Session()
    session.headers["User-Agent"] = "FinEvoLab/0.1 public-filings-research"
    session.mount("https://", HTTPAdapter(max_retries=Retry(total=2, backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504])))
    def download(url, relative):
        path = ROOT / relative
        if not path.exists():
            response = session.get(url, timeout=(10, 60))
            response.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
            time.sleep(.6)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if relative in known and known[relative] != digest:
            raise ValueError("A pinned acceptance source changed")
        return digest, len(raw)
    for company, cik in (("NVDA", "0001045810"), ("TSLA", "0001318605"), ("ADBE", "0000796343")):
        relative = f"data/raw/acceptance_{company.lower()}_submissions.json"
        download(f"https://data.sec.gov/submissions/CIK{cik}.json", relative)
        submissions = json.loads((ROOT / relative).read_bytes())
        batches = [submissions["filings"]["recent"]]
        for older in submissions["filings"]["files"]:
            if older["filingTo"] >= "2023-01-01" and older["filingFrom"] <= "2025-12-31":
                name = f"data/raw/acceptance_{company.lower()}_{older['name']}"
                download("https://data.sec.gov/submissions/" + older["name"], name)
                batches.append(json.loads((ROOT / name).read_bytes()))
        candidates = {}
        for batch in batches:
            for index, form in enumerate(batch["form"]):
                end = batch["reportDate"][index]
                if form == "10-K" and end[:4] in {"2023", "2024"}:
                    year = int(end[:4])
                    candidate = {"accession": batch["accessionNumber"][index],
                        "document": batch["primaryDocument"][index], "period_end": end,
                        "publication_date": batch["filingDate"][index]}
                    if year not in candidates or candidate["publication_date"] > candidates[year]["publication_date"]:
                        candidates[year] = candidate
        for year in (2023, 2024):
            filing = candidates[year]
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{filing['accession'].replace('-', '')}/{filing['document']}"
            relative = f"data/raw/acceptance_sec_{company.lower()}_{year}.html"
            digest, size = download(url, relative)
            sources.append({"company": company, "cik": cik, "fiscal_year": year, "report_type": "10-K",
                **filing, "url": url, "path": relative, "sha256": digest, "bytes": size,
                "retrieved_at": datetime.now(timezone.utc).isoformat()})
            print(json.dumps({"company": company, "year": year, "bytes": size}), flush=True)
        relative = f"data/raw/acceptance_{company.lower()}_companyfacts.json"
        digest, size = download(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", relative)
        sources.append({"company": company, "cik": cik, "report_type": "companyfacts", "path": relative,
            "sha256": digest, "bytes": size, "url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"})
        write_json(output, sources)
    session.close()


if __name__ == "__main__":
    main()
