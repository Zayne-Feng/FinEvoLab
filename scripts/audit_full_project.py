"""Recheck frozen data, retained experiments, model artifacts, and local HTTP contracts."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from finevolab.experiments import manifest, sha256
from finevolab.planner_benchmark import load_bundle
from finevolab.runtime import write_json
from finevolab.tools import FinancialStore
from scripts.check_planner_formal import status as planner_status


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def retained_audit(script, destination):
    namespace = runpy.run_path(str(ROOT / "scripts" / script))
    captured = []
    def save(path, record):
        captured.append(record)
        write_json(ROOT / destination, record)
    namespace["main"].__globals__["write_json"] = save
    namespace["main"]()
    if len(captured) != 1 or captured[0]["status"] != "passed":
        raise ValueError("A retained experiment audit did not pass")
    return {"status": "passed", "record": destination, "sha256": sha256(ROOT / destination)}


def http(path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request("http://127.0.0.1:8003" + path, data=body,
        headers={"Content-Type": "application/json"} if body else {})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.status, response.read()


def vector_indexes():
    from finevolab.embeddings import load_vectors
    result = {}
    config = read(ROOT / "configs/retrieval.json")
    for name in ("fixed", "structured", "contextual/structured"):
        stem = ROOT / "data/indexes" / name
        metadata = read(stem.with_suffix(".metadata.json"))
        chunks = read(stem.with_suffix(".json"))
        vectors = load_vectors(stem.with_suffix(".json"), chunks, config)
        if vectors.shape != (len(chunks), 384):
            raise ValueError("Vector and chunk dimensions differ")
        result[name] = {"shape": list(vectors.shape), "vector_sha256": sha256(stem.with_suffix(".npy")),
                        "chunk_fingerprint": metadata["chunks_sha256"],
                        "chunk_file_sha256": sha256(stem.with_suffix(".json")), "revision": config["revision"]}
    return result


def main():
    checks = {}
    for name in ("LICENSE", "NOTICE", "TRADEMARK_POLICY.md"):
        original = subprocess.check_output(["git", "show", f"2717499b8e30f242640af08c4ad9afd1113c2d45:{name}"], cwd=ROOT)
        if original.replace(b"\r\n", b"\n") != (ROOT / "licenses/finrobot" / name).read_bytes().replace(b"\r\n", b"\n"):
            raise ValueError("An upstream legal file changed")
    checks["legal_files"] = "passed"
    checks["branch"] = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    checks["remotes"] = subprocess.check_output(["git", "remote"], cwd=ROOT, text=True).splitlines()
    checks["tags"] = subprocess.check_output(["git", "tag", "--list", "upstream*"], cwd=ROOT, text=True).splitlines()
    verified = {}
    for name in ("sources.json", "sec_sources.json", "filing_sources.json", "acceptance_sources.json"):
        rows = read(ROOT / "data" / name)
        for row in rows:
            if row.get("path") and row.get("sha256"):
                path = ROOT / row["path"]
                if sha256(path) != row["sha256"]:
                    raise ValueError("A public source snapshot changed: " + row["path"])
                verified[row["path"]] = row["sha256"]
    checks["raw_sources"] = {"unique_files": len(verified), "hashes": verified}
    datasets = {}
    for path in sorted((ROOT / "data/evaluation").rglob("manifest.json")):
        document = read(path)
        verified_files = 0
        for key in ("files", "split_sha256"):
            for name, digest in document.get(key, {}).items():
                if not isinstance(digest, str) or len(digest) != 64:
                    raise ValueError("A frozen file digest is invalid")
                target = path.parent / (name + ".json" if key == "split_sha256" else name)
                if sha256(target) != digest:
                    raise ValueError("A frozen dataset file changed: " + str(target))
                verified_files += 1
        datasets[path.relative_to(ROOT).as_posix()] = {"sha256": sha256(path), "verified_files": verified_files}
    checks["dataset_manifests"] = datasets
    bundle = ROOT / "data/training/planner_formal/frozen_v1"
    checks["planner_splits"] = {}
    for split in ("development", "validation", "test"):
        cases, _ = load_bundle(bundle, split)
        checks["planner_splits"][split] = {"cases": len(cases), "template_groups": len({c.group_id for c in cases})}
    checks["planner_completion"] = planner_status(ROOT)
    if checks["planner_completion"]["status"] != "completed":
        raise ValueError("The planner experiment is incomplete")
    epoch = read(ROOT / "project_notes/evaluations/planner_formal_selection_details.json")["selected_epoch"]
    for seed in (17, 29):
        model = read(ROOT / f"project_notes/experiments/planner-formal-lora{seed}-test/manifest.json")["config"]["model"]
        directory = ROOT / f"output/training/formal-seed{seed}/checkpoint-{epoch*75}"
        if any(sha256(directory / name) != digest for name, digest in model["adapter"]["files"].items()):
            raise ValueError("A retained adapter differs from its tested identity")
        checks[f"model_seed{seed}"] = {"inference_dtype": model["dtype"], "inference_quantization": model["quantization"]}
    store = FinancialStore(ROOT / "data/processed/reports.json")
    checks["financial_store"] = {"records": len(store.metrics),
        "unique_facts": len({(m.company, m.name, m.fiscal_year) for m in store.metrics})}
    checks["vector_indexes"] = vector_indexes()
    checks["source_recheck"] = retained_audit("audit_scope_sources.py", "project_notes/run_logs/full_project_source_recheck.json")
    checks["experiment_recheck"] = retained_audit("audit_renewed_release.py", "project_notes/run_logs/full_project_experiment_recheck.json")
    code, page = http("/finevolab")
    if code != 200 or b"language-toggle" not in page:
        raise ValueError("The live bilingual page is unavailable")
    _, raw_schema = http("/openapi.json")
    paths = read_json_bytes(raw_schema)["paths"]
    required = ("/api/tasks", "/api/questions", "/api/search", "/api/uploads", "/api/tasks/{task_id}/resume")
    if not set(required) <= set(paths):
        raise ValueError("A required HTTP endpoint is missing")
    _, raw = http("/api/tasks", {"company": "MSFT", "fiscal_year": 2024, "mode": "offline"})
    state = read_json_bytes(raw)
    if state["status"] != "complete":
        raise ValueError("The live offline workflow did not complete")
    task_id = state["task_id"]
    _, pdf = http(f"/api/tasks/{task_id}/report/pdf")
    _, html = http(f"/api/tasks/{task_id}/report/html")
    _, resumed = http(f"/api/tasks/{task_id}/resume", {})
    if not pdf.startswith(b"%PDF-") or not html or read_json_bytes(resumed)["phases"] != state["phases"]:
        raise ValueError("Report delivery or completed-task resume failed")
    _, raw_search = http("/api/search?query=revenue&company=MSFT&year=2024&mode=bm25")
    checks["live_http"] = {"status": "passed", "task_id": task_id, "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "html_sha256": hashlib.sha256(html).hexdigest(), "search_response_type": type(read_json_bytes(raw_search)).__name__,
        "required_paths": list(required), "completed_resume_preserved": True}
    record = {**manifest(ROOT, "full-project-audit", {}), "status": "passed", "checks": checks,
        "gpu_requests": 0, "fmp_requests": 0, "model_requests": 0,
        "scope": {"production_planner": "configured_api", "lora": "trained_and_evaluated_separately",
            "training_quantization": "nf4", "formal_inference_quantization": "none",
            "browser_visual_screenshot": "not_verified_by_this_script"}}
    write_json(ROOT / "project_notes/run_logs/full_project_audit.json", record)
    print(json.dumps({"status": "passed", "raw_sources": len(verified), "dataset_manifests": len(datasets),
                      "live_task": task_id, "model_requests": 0}), flush=True)


def read_json_bytes(value):
    return json.loads(value.decode("utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors-only", action="store_true")
    args = parser.parse_args()
    if args.vectors_only:
        path = ROOT / "project_notes/run_logs/full_project_audit.json"
        record = read(path)
        record["checks"]["vector_indexes"] = vector_indexes()
        record["vector_recheck_code_sha256"] = sha256(Path(__file__))
        write_json(path, record)
        print(json.dumps({"status": "passed", "vector_indexes": record["checks"]["vector_indexes"]}))
    else:
        main()
