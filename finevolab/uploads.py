"""Extract source-located text from uploaded reports."""

import hashlib
from functools import lru_cache
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup

from finevolab.runtime import write_json
from finevolab.provenance import file_digest


def parser_signature() -> str:
    return hashlib.sha256("".join(file_digest(Path(__file__).with_name(name))
        for name in ("uploads.py", "upload_metrics.py", "table_context.py", "retrieval.py",
                     "ingestion.py", "sec_ingestion.py")).encode()).hexdigest()


def ingest_upload(raw: bytes, suffix: str, company: str, year: int, root: Path, refresh: bool = False) -> dict:
    if suffix not in {".pdf", ".html", ".htm"}:
        raise ValueError("Only PDF and HTML reports are supported")
    if not re.fullmatch(r"[A-Z0-9.-]{1,12}", company) or not 2000 <= year <= 2100:
        raise ValueError("Invalid report metadata")
    digest = hashlib.sha256(raw).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    metadata_path = root / (digest + ".json")
    existing = None
    if metadata_path.exists():
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        if existing["company"] != company or existing["report_year"] != year:
            raise ValueError("The same document already has different metadata")
        if existing["path"] not in {digest + extension for extension in (".pdf", ".html", ".htm")}:
            raise ValueError("Invalid stored upload path")
        if not refresh and existing.get("parser_signature") == parser_signature():
            return existing
    path = root / (existing["path"] if existing else digest + suffix)
    if not path.exists():
        path.write_bytes(raw)
    pages = []
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        for index, page in enumerate(reader.pages, start=1):
            pages.append((f"page={index}", page.extract_text() or ""))
    else:
        soup = BeautifulSoup(raw, "html.parser")
        for element in soup.find_all(["script", "style", "noscript"]):
            element.decompose()
        pages.append(("html-body", soup.get_text("\n", strip=True)))
    chunks = []
    for locator, text in pages:
        for offset in range(0, len(text), 1200):
            piece = text[offset:offset + 1200].strip()
            if not piece:
                continue
            location = f"{locator}|offset={offset}"
            chunks.append({"id": hashlib.sha256((digest + location + company + str(year)).encode()).hexdigest()[:24],
                           "company": company, "report_year": year, "report_type": "uploaded",
                           "source_sha256": digest, "source_url": f"/api/uploads/{digest}/original",
                           "section": "uploaded-report", "locator": location, "text": piece, "kind": "text"})
    if suffix in {".html", ".htm"}:
        from finevolab.retrieval import chunk_filing
        located = chunk_filing({"path": path.name, "sha256": digest, "company": company,
            "fiscal_year": year, "url": f"/api/uploads/{digest}/original"}, root, contextual=True)
        if located:
            chunks = [{**item, "report_type": "uploaded"} for item in located]
    from finevolab.upload_metrics import extract_uploaded_metrics
    metric_results = extract_uploaded_metrics(path, company, year, pages)
    metadata = {"id": digest, "path": path.name, "sha256": digest, "company": company,
                "report_year": year, "metadata_origin": "user_supplied", "pages": len(pages),
                "status": "text_extracted" if chunks else ("needs_ocr" if suffix == ".pdf" else "extraction_failed"),
                "metric_extraction": metric_results["status"], "metric_results": metric_results,
                "chunks": chunks, "parser_signature": parser_signature()}
    if metadata != existing:
        write_json(metadata_path, metadata)
    return metadata


def uploaded_chunks(root: Path) -> list[dict]:
    return [chunk for path in root.glob("*.json")
            for chunk in json.loads(path.read_text(encoding="utf-8"))["chunks"]]


def verified_upload(identifier: str, root: Path) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise ValueError("Invalid upload identifier")
    metadata_path = root / (identifier + ".json")
    if not metadata_path.exists():
        raise ValueError("The selected report is unavailable")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata["path"] not in {identifier + suffix for suffix in (".pdf", ".html", ".htm")}:
        raise ValueError("Invalid stored upload path")
    path = root / metadata["path"]
    if not path.exists():
        raise ValueError("The selected report source is unavailable")
    if file_digest(path) != identifier or metadata["sha256"] != identifier:
        raise ValueError("Uploaded source hash differs from its identity")
    signature = tuple((item.stat().st_mtime_ns, item.stat().st_size) for item in (metadata_path, path))
    return _verified_upload(str(root.resolve()), identifier, parser_signature(), signature)


@lru_cache(maxsize=16)
def _verified_upload(root_path: str, identifier: str, parser: str, signature: tuple) -> dict:
    root = Path(root_path)
    stored = json.loads((root / (identifier + ".json")).read_text(encoding="utf-8"))
    path = root / stored["path"]
    actual = ingest_upload(path.read_bytes(), path.suffix, stored["company"], stored["report_year"], root, refresh=True)
    if stored.get("parser_signature") == parser and any(stored.get(key) != actual[key] for key in ("chunks", "metric_results")):
        raise ValueError("Uploaded metadata differs from the source extraction")
    return actual
