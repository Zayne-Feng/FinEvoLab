"""Download the pinned reranker into the local model cache."""

import json
from pathlib import Path
import sys

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.reranking import load_config


if __name__ == "__main__":
    config = load_config(ROOT)
    snapshot_download(config.model, revision=config.revision, cache_dir=ROOT / "data/models",
        allow_patterns=["*.json", "*.safetensors", "*.model", "README.md", "LICENSE*"])
    print(json.dumps({"status": "downloaded", "model": config.model, "revision": config.revision}))
