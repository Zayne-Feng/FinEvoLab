"""Freeze experiment sources, configurations, and exact restoration patches."""

import difflib
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def initialize(directory, config, inputs):
    directory.mkdir(parents=True, exist_ok=True)
    paths = sorted(set([p.relative_to(ROOT).as_posix() for p in (ROOT / "finevolab").glob("*.py")] + inputs +
        ["scripts/service_experiment_utils.py", "configs/retrieval.json", "configs/reranker.json",
         "configs/retrieval_services.json", "configs/agent_review.json", "prompts/benchmark.json",
         "requirements/development.lock.txt", "requirements/retrieval_services.in",
         "finevolab/templates/analysis.html", "data/evaluation/scope_v3/documents.json"]))
    hashes = {name: digest(ROOT / name) for name in paths}
    path = directory / "manifest.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["config"] != config or saved["source_hashes"] != hashes:
            raise ValueError("Frozen experiment sources or configuration changed; use a new run ID")
        return saved
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    patch = []
    for name in paths:
        original = subprocess.run(["git", "show", commit + ":" + name], cwd=ROOT, capture_output=True)
        old = original.stdout if original.returncode == 0 else b""
        new = (ROOT / name).read_bytes()
        if old != new:
            patch.extend(difflib.unified_diff(old.decode("utf-8").splitlines(keepends=True),
                new.decode("utf-8").splitlines(keepends=True),
                fromfile="a/" + name if original.returncode == 0 else "/dev/null", tofile="b/" + name))
    (directory / "source.patch").write_text("".join(patch), encoding="utf-8", newline="\n")
    saved = {"run_id": directory.name, "commit": commit, "source_hashes": hashes, "config": config,
        "source_patch": "source.patch", "source_patch_sha256": digest(directory / "source.patch"),
        "packages": {name: version(name) for name in ("redis", "pymilvus", "milvus-lite", "numpy", "pyarrow", "faiss-cpu")}}
    write(path, saved)
    return saved


def write(path, value):
    from finevolab.runtime import write_json
    write_json(Path(path), value)
