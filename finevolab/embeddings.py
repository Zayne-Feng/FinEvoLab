"""Encode local passages with a pinned CPU multilingual model."""

import hashlib
import json
from pathlib import Path

import numpy as np


def chunk_fingerprint(chunks: list[dict]) -> str:
    return hashlib.sha256(json.dumps(chunks, sort_keys=True).encode()).hexdigest()


class PassageEncoder:
    def __init__(self, config: dict, cache_path: Path, local_files_only: bool = False):
        from sentence_transformers import SentenceTransformer
        import torch
        torch.set_num_threads(4)
        self.config = config
        self.model = SentenceTransformer(config["model"], revision=config["revision"],
                                         device="cpu", cache_folder=str(cache_path), token=False,
                                         local_files_only=local_files_only)
        self.model.max_seq_length = config["max_sequence_length"]

    def encode(self, texts: list[str], query: bool = False) -> np.ndarray:
        prefix = self.config["query_prefix" if query else "passage_prefix"]
        return self.model.encode([prefix + text for text in texts],
                                 batch_size=self.config["batch_size"], normalize_embeddings=True,
                                 show_progress_bar=False, convert_to_numpy=True)


def load_vectors(path: Path, chunks: list[dict], config: dict) -> np.ndarray:
    metadata = json.loads(path.with_suffix(".metadata.json").read_text())
    if metadata["chunks_sha256"] != chunk_fingerprint(chunks) or metadata["config"] != config:
        raise ValueError("Vector index versions do not match the requested corpus and model")
    vectors = np.load(path.with_suffix(".npy"), allow_pickle=False)
    if list(vectors.shape) != metadata["shape"] or not np.isfinite(vectors).all():
        raise ValueError("Invalid vector array")
    if not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4):
        raise ValueError("Vectors must be normalized")
    return vectors
