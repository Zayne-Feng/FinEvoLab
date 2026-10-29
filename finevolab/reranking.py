"""Score query-passage pairs with a pinned CPU Cross-Encoder."""

from functools import lru_cache
from pathlib import Path
from threading import RLock

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class RerankerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    model: str
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    device: str = Field(default="cpu", pattern=r"^cpu$")
    max_length: int = Field(default=384, ge=64, le=512)
    batch_size: int = Field(default=8, ge=1, le=32)
    max_candidates: int = Field(default=20, ge=5, le=40)


def load_config(root: Path) -> RerankerConfig:
    return RerankerConfig.model_validate_json((root / "configs/reranker.json").read_text(encoding="utf-8"))


class PassageReranker:
    def __init__(self, config: RerankerConfig, cache_path: Path):
        from sentence_transformers import CrossEncoder
        import torch
        torch.set_num_threads(4)
        self.config = config
        self.model = CrossEncoder(config.model, revision=config.revision, device="cpu",
            max_length=config.max_length, cache_folder=str(cache_path), local_files_only=True)
        self.lock = RLock()

    def rank(self, query: str, passages: list[dict]) -> list[dict]:
        if not passages:
            return []
        pairs = [(query, item.get("body_text", item["text"])) for item in passages]
        with self.lock:
            scores = np.asarray(self.model.predict(pairs, batch_size=self.config.batch_size,
                show_progress_bar=False), dtype=float).reshape(-1)
        if len(scores) != len(passages) or not np.isfinite(scores).all():
            raise ValueError("Reranker scores must be finite and match the candidate count")
        ranked = sorted(zip(passages, scores), key=lambda row: (-float(row[1]), row[0]["id"]))
        return [{**item, "rerank_score": float(score)} for item, score in ranked]


@lru_cache(maxsize=2)
def _load(config_json: str, cache_path: str) -> PassageReranker:
    return PassageReranker(RerankerConfig.model_validate_json(config_json), Path(cache_path))


def rerank_passages(root: Path, query: str, passages: list[dict]) -> list[dict]:
    config = load_config(root)
    if len(passages) > config.max_candidates:
        raise ValueError("Reranker candidate budget exceeded")
    return _load(config.model_dump_json(), str((root / "data/models").resolve())).rank(query, passages)
