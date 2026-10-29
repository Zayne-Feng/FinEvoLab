"""Project public evidence and business events without provider transcripts."""

import hashlib
import json
from pathlib import Path


def public_evidence(item: dict) -> dict:
    return {key: item[key] for key in ("id", "company", "report_year", "source_url", "source_sha256",
        "section", "locator", "text", "kind", "public_source_url") if key in item}


def public_result(state: dict) -> dict:
    phases = state.get("phases", {})
    result = phases.get("analyze", {}).get("result", {})
    events = []
    for event in result.get("events", []):
        if "tool" in event:
            item = {key: event[key] for key in ("event", "tool", "parameters", "call", "cache_hit", "status") if key in event}
            if "output" in event:
                output = event["output"]
                item["output"] = ([public_evidence(row) for row in output] if isinstance(output, list)
                                  else output)
            events.append(item)
    return {"task_id": state["task_id"], "status": state["status"],
        "phases": {key: {field: value[field] for field in ("status", "error_type", "duration_seconds", "attempt_seconds", "attempts") if field in value}
                   for key, value in phases.items()},
        "request": result.get("request"), "answer_type": result.get("answer_type"),
        "business_status": result.get("overall_status", result.get("answer", {}).get("status")),
        "answer": result.get("answer"), "narrative": result.get("narrative"),
        "evidence": [public_evidence(item) for item in result.get("evidence", [])],
        "events": events,
        "operations": [{key: event[key] for key in ("kind", "operation", "phase", "status", "duration_seconds",
            "error_type", "call", "cache_hit", "model", "tokens", "attempt") if key in event}
            for event in state.get("operations", result.get("events", [])) if event.get("event") == "operation"],
        "validation": phases.get("validate", {}).get("result"),
        "repair": result.get("repair"), "review": result.get("review"),
        "retrieval": [{key: event[key] for key in ("event", "backend", "status", "error_type", "configured_backend", "actual_backend") if key in event}
            for event in result.get("events", []) if event.get("event") in {"retrieval_cache", "vector_backend"}],
        "usage": result.get("usage"),
        "elapsed_seconds": state.get("elapsed_seconds", result.get("elapsed_seconds")),
        "data_version": result.get("data_version"), "runtime_backend": result.get("runtime_backend")}


def build_public_data(root: Path) -> dict:
    source = "tests/frontend/fixtures/task.json"
    raw = (root / source).read_bytes()
    state = json.loads(raw)
    result = state["phases"]["analyze"]["result"]
    question = next(json.loads(item["content"])["question"] for item in result["messages"][0]["input"]
                    if item["role"] == "user")
    replay = public_result(state)
    replay.update(question=question, source=source, sha256=hashlib.sha256(raw).hexdigest(),
                  mode="archived_execution")
    sources = json.loads((root / "data/acceptance_sources.json").read_text(encoding="utf-8"))
    by_digest = {item["sha256"]: item for item in sources}
    def resolve(value):
        if isinstance(value, dict):
            if value.get("source_url", "").startswith("/api/uploads/"):
                source = by_digest[value["source_sha256"]]
                if hashlib.sha256((root / source["path"]).read_bytes()).hexdigest() != value["source_sha256"]:
                    raise ValueError("A public source mapping requires matching bytes")
                value["public_source_url"] = source["url"]
            for child in list(value.values()):
                resolve(child)
        elif isinstance(value, list):
            for child in value:
                resolve(child)
    resolve(replay)
    for claim in (replay.get("narrative") or {}).get("claims", []):
        evidence = next(item for item in replay["evidence"] if item["id"] == claim["chunk_id"])
        if claim["quote"] not in evidence["text"]:
            raise ValueError("An archived quote must match its evidence")
    reports = json.loads((root / "data/processed/reports.json").read_text(encoding="utf-8"))
    observations = []
    for report in reports["reports"]:
        evidence = {item["id"]: item for item in report["evidence"]}
        for metric in report["metrics"]:
            observations.append({**metric, "company": report["source"]["company"],
                                 "evidence": public_evidence(evidence[metric["evidence_id"]])})
    return {"data_version": reports["version"], "replays": [replay], "observations": observations}
