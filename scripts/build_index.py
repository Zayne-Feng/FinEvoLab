"""Build deterministic report chunks from local snapshots."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.retrieval import chunk_report, chunk_filing


def main() -> None:
    sources = json.loads((ROOT / "data/sources.json").read_text())
    target = ROOT / "data/indexes"
    target.mkdir(parents=True, exist_ok=True)
    for strategy in ("fixed", "structured"):
        chunks = [chunk for source in sources for chunk in chunk_report(source, ROOT, strategy)]
        if (ROOT / "data/filing_sources.json").exists():
            filings = json.loads((ROOT / "data/filing_sources.json").read_text())
            for source in filings:
                if source["status"] == "downloaded":
                    chunks.extend(chunk_filing(source, ROOT, strategy))
        (target / f"{strategy}.json").write_text(json.dumps(chunks, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"strategy": strategy, "chunks": len(chunks)}))


if __name__ == "__main__":
    main()
