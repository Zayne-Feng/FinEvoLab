"""Encode report chunks and bind vectors to model and source fingerprints."""

import json
import hashlib
import sqlite3
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from finevolab.embeddings import PassageEncoder, chunk_fingerprint


def main() -> None:
    config = json.loads((ROOT / "configs/retrieval.json").read_text())
    encoder = PassageEncoder(config, ROOT / "data/models")
    started = time.monotonic()
    cache = sqlite3.connect(ROOT / "data/indexes/embedding_cache.sqlite")
    cache.execute("CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector BLOB NOT NULL)")
    semantic_config = {key: value for key, value in config.items() if key not in {"batch_size", "device", "rrf_constant"}}
    configuration = json.dumps(semantic_config, sort_keys=True)
    for strategy in ("fixed", "structured"):
        path = ROOT / f"data/indexes/{strategy}.json"
        chunks = json.loads(path.read_text())
        vectors = np.empty((len(chunks), encoder.model.get_sentence_embedding_dimension()), dtype=np.float32)
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
                for (key, item), vector in zip(missing.items(), encoded):
                    cache.execute("INSERT OR REPLACE INTO embeddings VALUES (?,?)", (key, vector.astype(np.float32).tobytes()))
                    for position in item["positions"]:
                        vectors[position] = vector
                cache.commit()
            if start % 512 == 0:
                print(json.dumps({"strategy": strategy, "encoded_through": min(start + 32, len(chunks)), "total": len(chunks)}), flush=True)
        np.save(path.with_suffix(".npy"), vectors, allow_pickle=False)
        metadata = {"config": config, "chunks_sha256": chunk_fingerprint(chunks),
                    "shape": list(vectors.shape), "elapsed_seconds": round(time.monotonic() - started, 3)}
        path.with_suffix(".metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"strategy": strategy, **metadata}))
    cache.close()


if __name__ == "__main__":
    main()
