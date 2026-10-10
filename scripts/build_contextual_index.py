"""Build unit-contextualized chunks and reuse pinned embeddings by text identity."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from finevolab.embeddings import PassageEncoder, chunk_fingerprint
from finevolab.experiments import manifest
from finevolab.retrieval import chunk_filing, chunk_report
from finevolab.runtime import write_json


def main():
    directory = ROOT / "data/indexes/contextual"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "structured.json"
    sources = json.loads((ROOT / "data/sources.json").read_text())
    filings = json.loads((ROOT / "data/filing_sources.json").read_text())
    chunks = []
    for source in sources:
        chunks.extend(chunk_report(source, ROOT, contextual=True))
    for source in filings:
        if source["status"] == "downloaded":
            chunks.extend(chunk_filing(source, ROOT, contextual=True))
            print(json.dumps({"company": source["company"], "year": source["fiscal_year"], "chunks": len(chunks)}), flush=True)
    write_json(path, chunks)
    config = json.loads((ROOT / "configs/retrieval.json").read_text())
    encoder = PassageEncoder(config, ROOT / "data/models", local_files_only=True)
    cache = sqlite3.connect(ROOT / "data/indexes/embedding_cache.sqlite")
    cache.execute("CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector BLOB NOT NULL)")
    semantic = {key: value for key, value in config.items() if key not in {"batch_size", "device", "rrf_constant"}}
    configuration = json.dumps(semantic, sort_keys=True)
    vectors = np.empty((len(chunks), encoder.model.get_sentence_embedding_dimension()), dtype=np.float32)
    missing_count, started = 0, time.monotonic()
    for start in range(0, len(chunks), 32):
        missing = {}
        for position in range(start, min(start + 32, len(chunks))):
            key = hashlib.sha256((configuration + chunks[position]["text"]).encode()).hexdigest()
            row = cache.execute("SELECT vector FROM embeddings WHERE key=?", (key,)).fetchone()
            if row:
                vectors[position] = np.frombuffer(row[0], dtype=np.float32)
            else:
                missing.setdefault(key, {"text": chunks[position]["text"], "positions": []})["positions"].append(position)
        if missing:
            encoded = encoder.encode([item["text"] for item in missing.values()])
            missing_count += len(missing)
            for (key, item), vector in zip(missing.items(), encoded):
                cache.execute("INSERT OR REPLACE INTO embeddings VALUES (?,?)", (key, vector.astype(np.float32).tobytes()))
                for position in item["positions"]:
                    vectors[position] = vector
            cache.commit()
        if start % 512 == 0:
            print(json.dumps({"through": start, "new_embeddings": missing_count, "total": len(chunks)}), flush=True)
    np.save(path.with_suffix(".npy"), vectors, allow_pickle=False)
    write_json(path.with_suffix(".metadata.json"), {"config": config, "chunks_sha256": chunk_fingerprint(chunks),
        "shape": list(vectors.shape), "new_embeddings": missing_count, "elapsed_seconds": time.monotonic() - started})
    contexts = Counter(item["unit_context"]["status"] for item in chunks if item.get("unit_context"))
    write_json(ROOT / "project_notes/run_logs/contextual_index.json", manifest(ROOT, "contextual-index-v1", {
        "chunks": len(chunks), "unit_contexts": dict(contexts), "new_embeddings": missing_count,
        "legacy_indexes_modified": False, "config": config, "source_manifest": "data/filing_sources.json"}))
    print(json.dumps({"status": "complete", "chunks": len(chunks), "unit_contexts": dict(contexts)}), flush=True)


if __name__ == "__main__":
    main()
