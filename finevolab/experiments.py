"""Capture reproducible experiment identities and aggregate task outcomes."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(root: Path, run_id: str, config: dict) -> dict:
    paths = []
    for directory, pattern in (("finevolab", "*.py"), ("scripts", "*.py"),
                               ("prompts", "*.json"), ("configs", "*.json"),
                               ("requirements", "*.txt"), ("data/evaluation", "*.json")):
        paths.extend(path for path in (root / directory).rglob(pattern) if "candidates" not in path.parts)
    hashes = {path.relative_to(root).as_posix(): sha256(path) for path in sorted(paths)}
    return {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "worktree_status": subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).splitlines(),
            "source_hashes": hashes, "config": config,
            "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()}


def aggregate(records: list[dict], planned: int) -> dict:
    attempted = len(records)
    scored = [record for record in records if record["execution_status"] == "completed"]
    successes = sum(record.get("score", 0) for record in records)
    token_values = [record["output"]["usage"]["total_tokens"] for record in records
                    if record.get("output", {}).get("usage")]
    latency = [record["elapsed_seconds"] for record in records]
    categories = {}
    for name in sorted({record["category"] for record in records}):
        subset = [record for record in records if record["category"] == name]
        categories[name] = {"attempted": len(subset), "success_rate": sum(row.get("score", 0) for row in subset) / len(subset)}
    return {"planned": planned, "attempted": attempted, "unattempted": planned - attempted,
            "completed": len(scored), "successes": successes,
            "success_rate_attempted": successes / attempted if attempted else None,
            "execution_status_counts": dict(Counter(record["execution_status"] for record in records)),
            "error_types": dict(Counter(record["error_type"] for record in records if "error_type" in record)),
            "mean_latency_seconds": statistics.mean(latency) if latency else None,
            "known_total_tokens": sum(token_values), "unknown_usage_tasks": attempted - len(token_values),
            "partial_observed_tokens": sum(event["tokens"] for record in records
                for event in record.get("partial_telemetry", {}).get("lifecycle_events", []) if event["event"] == "model_end"),
            "cost": None, "categories": categories,
            "checks": {name: sum(record.get("checks", {}).get(name, False) for record in records) / attempted
                       for name in ("schema", "status", "numeric", "units", "periods", "citations", "formula")} if attempted else {}}
