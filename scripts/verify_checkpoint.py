"""Audit source integrity, local indexes, reports, and credential exclusion."""

from uuid import uuid4
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pypdf import PdfReader
from finevolab.config import ModelConfig
from finevolab.embeddings import load_vectors


def main() -> None:
    files = set(subprocess.check_output(["git", "ls-files", "-co", "--exclude-standard"],
                                        cwd=ROOT, text=True).splitlines())
    config = ROOT / "finrobot_equity/core/config/config.ini"
    if config.exists():
        key = ModelConfig.load(config).api_key.encode()
        if any(key in (ROOT / name).read_bytes() for name in files if (ROOT / name).is_file()):
            raise ValueError("A credential was found in a version-controlled candidate")
    verified = []
    for name in ("sources.json", "sec_sources.json", "filing_sources.json"):
        sources = json.loads((ROOT / "data" / name).read_text())
        for item in sources:
            if item.get("status", "downloaded") != "downloaded":
                raise ValueError("A source snapshot is unavailable")
            path = ROOT / item["path"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("A source hash is invalid")
            verified.append(item["path"])
    config_data = json.loads((ROOT / "configs/retrieval.json").read_text())
    shapes = {}
    for strategy in ("fixed", "structured"):
        path = ROOT / "data/indexes" / (strategy + ".json")
        chunks = json.loads(path.read_text())
        shapes[strategy] = list(load_vectors(path, chunks, config_data).shape)
    pdf = ROOT / "output/reports/msft_2024/report.pdf"
    reader = PdfReader(pdf)
    text = "\n".join(page.extract_text() for page in reader.pages)
    if "15.669962" not in text or "245,122,000,000" not in text:
        raise ValueError("The report does not contain the expected calculation")
    record = {
        "run_id": uuid4().hex[:12] + "-checkpoint",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "verified_snapshots": len(verified), "vectors": shapes, "credential_exclusion": "passed",
        "pdf_pages": len(reader.pages), "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "source_files_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (ROOT / "finevolab").rglob("*")
                                if path.is_file() and path.suffix in {".py", ".html"}},
    }
    (ROOT / "project_notes/run_logs/checkpoint_audit.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in record.items() if key != "source_files_sha256"}, indent=2))


if __name__ == "__main__":
    main()
