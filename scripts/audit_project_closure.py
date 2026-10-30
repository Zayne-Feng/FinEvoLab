"""Recheck source lineage, frozen experiments, model exports, and local delivery."""

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
from zipfile import ZipFile

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from finevolab.experiments import manifest, sha256
from finevolab.planner_benchmark import load_bundle
from finevolab.runtime import write_json
from finevolab.scope_evaluation import assess_scope, task_store
from finevolab.tools import FinancialStore
from scripts.audit_full_project import vector_indexes
from scripts.run_followup_uploads import selected_reference


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def captured(script):
    namespace = runpy.run_path(str(ROOT / "scripts" / script))
    records = []
    namespace["main"].__globals__["write_json"] = lambda path, record: records.append(record)
    with redirect_stdout(io.StringIO()):
        namespace["main"]()
    if len(records) != 1 or records[0].get("status") not in {"passed", "completed"}:
        raise ValueError("A retained artifact check failed: " + script)
    return records[0]


def historical_runs():
    retained = read("project_notes/run_logs/full_project_experiment_recheck.json")
    results = {}
    for run_id in retained["runs"]:
        directory = ROOT / "project_notes/experiments" / run_id
        execution = read(directory / "manifest.json")
        config = execution["config"]
        previous = retained["runs"][run_id]
        for filename, key in (("tasks.jsonl", "tasks_sha256"), ("assessment.json", "assessment_sha256")):
            if sha256(directory / filename) != previous[key]:
                raise ValueError("A retained experiment changed")
        for name, digest in config["source_hashes"].items():
            raw = subprocess.check_output(["git", "show", f"{execution['commit']}:{name}"], cwd=ROOT)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("A historical implementation identity differs")
        dataset = ROOT / config.get("dataset_path", f"data/evaluation/scope_v3/{config['split']}.json")
        if sha256(dataset) != config.get("dataset_sha256", config.get("dataset")):
            raise ValueError("An evaluated dataset changed")
        cases = read(dataset)["tasks"]
        if config.get("case_ids"):
            cases = [case for case in cases if case["id"] in config["case_ids"]]
        if config.get("limit"):
            cases = cases[:config["limit"]]
        indexed = {case["id"]: case for case in cases}
        documents = read(dataset.parent / "documents.json")
        rows = [json.loads(line) for line in (directory / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
        expected = {(case["id"], repeat, arm) for case in cases for repeat in range(config["repeats"])
                    for arm in config.get("arms", ["current"])}
        actual = {(row["case_id"], row["repeat"], row["arm"]) for row in rows}
        if expected != actual or len(rows) != len(actual):
            raise ValueError("A paired experiment grid is incomplete")
        stores = {}
        for row in rows:
            case = indexed[row["case_id"]]
            scope = tuple(case["upload_ids"])
            if scope not in stores:
                stores[scope] = task_store(ROOT, case, documents)
            if "output" in row and assess_scope(ROOT, stores[scope], selected_reference(case), row["output"])["score"] != row["score"]:
                raise ValueError("A retained mechanical score differs")
        results[run_id] = {"rows": len(rows), "historical_commit": execution["commit"], "pairing_and_scores": "passed"}
    return results


def main():
    checks = {}
    upstream = "2717499b8e30f242640af08c4ad9afd1113c2d45"
    for name in ("LICENSE", "NOTICE", "TRADEMARK_POLICY.md"):
        original = subprocess.check_output(["git", "show", upstream + ":" + name], cwd=ROOT)
        if original.replace(b"\r\n", b"\n") != (ROOT / "licenses/finrobot" / name).read_bytes().replace(b"\r\n", b"\n"):
            raise ValueError("An upstream legal file changed")
    checks["legal_files"] = "passed"
    sources = {}
    for name in ("sources.json", "sec_sources.json", "filing_sources.json", "acceptance_sources.json"):
        for row in read("data/" + name):
            if row.get("path") and row.get("sha256"):
                if sha256(ROOT / row["path"]) != row["sha256"]:
                    raise ValueError("A raw financial snapshot changed")
                sources[row["path"]] = row["sha256"]
    checks["raw_sources"] = {"count": len(sources), "hashes": sources}
    frozen = {}
    for path in sorted((ROOT / "data/evaluation").rglob("manifest.json")):
        document = read(path)
        for key in ("files", "split_sha256"):
            for name, digest in document.get(key, {}).items():
                target = path.parent / (name + ".json" if key == "split_sha256" else name)
                if sha256(target) != digest:
                    raise ValueError("A frozen split changed")
        frozen[path.relative_to(ROOT).as_posix()] = sha256(path)
    checks["business_split_manifests"] = frozen
    checks["planner_splits"] = {}
    for split in ("development", "validation", "test"):
        cases, _ = load_bundle(ROOT / "data/training/planner_formal/frozen_v1", split)
        checks["planner_splits"][split] = {"cases": len(cases), "groups": len({case.group_id for case in cases})}
    store = FinancialStore(ROOT / "data/processed/reports.json")
    checks["financial_store"] = {"disclosures": len(store.metrics),
        "unique_facts": len({(m.company, m.name, m.fiscal_year) for m in store.metrics})}
    checks["vectors"] = vector_indexes()
    source = captured("audit_scope_sources.py")
    checks["independent_source_check"] = {key: source[key] for key in
        ("independently_verified_observations", "documents", "unit_context_status")}
    checks["historical_experiments"] = historical_runs()
    formal = captured("analyze_planner_formal.py")
    retained = read("project_notes/evaluations/planner_formal_results.json")
    for key in ("seeds", "selection", "verified_adapter_files", "total_inference_requests", "known_inference_tokens"):
        if formal[key] != retained[key]:
            raise ValueError("A formal SFT result differs: " + key)
    checks["sft_scores"] = {seed: {arm: values["strict_successes"] for arm, values in data["arms"].items()}
                           for seed, data in formal["seeds"].items()}
    quantization = captured("summarize_quantization.py")
    if quantization != read("project_notes/evaluations/planner_quantization.json"):
        raise ValueError("A quantization result differs")
    checks["quantization"] = {mode: data["first"]["successes"] for mode, data in quantization["groups"].items()}
    delivery = captured("audit_planner_delivery.py")
    checks["live_gpu_receipts"] = {key: delivery[key] for key in
        ("retained_tasks", "gpu_generations", "api_narrative_requests", "business_passes")}
    archived = read("output/cloud/release_v1_manifest.json")
    for name, expected in archived["archives"].items():
        if sha256(ROOT / "output/cloud" / name) != expected["sha256"]:
            raise ValueError("A release backup changed")
    with ZipFile(ROOT / "output/cloud/finevolab_release_v1.zip") as archive:
        if archive.testzip() is not None:
            raise ValueError("The source ZIP failed CRC verification")
    checks["backup_archives"] = list(archived["archives"])
    receipts = read("project_notes/run_logs/planner_final_checks.json")
    for name, digest in receipts["receipts"].items():
        if sha256(ROOT / f"project_notes/run_logs/{name}.json") != digest:
            raise ValueError("A delivery receipt changed")
    checks["retained_receipts"] = len(receipts["receipts"])
    with httpx.Client(base_url="http://127.0.0.1:8003", timeout=90, trust_env=False) as client:
        page = client.get("/finevolab")
        if page.status_code != 200 or "planner-backend" not in page.text or "language-toggle" not in page.text:
            raise ValueError("The live page is unavailable")
        state = client.post("/api/tasks", json={"company": "MSFT", "fiscal_year": 2024, "mode": "offline"}).json()
        if state["status"] != "complete":
            raise ValueError("The current offline workflow failed")
        task_id = state["task_id"]
        resumed = client.post(f"/api/tasks/{task_id}/resume")
        pdf = client.get(f"/api/tasks/{task_id}/report/pdf")
        html = client.get(f"/api/tasks/{task_id}/report/html")
        if resumed.json()["phases"] != state["phases"] or not pdf.content.startswith(b"%PDF-") or html.status_code != 200:
            raise ValueError("Current report delivery or recovery failed")
        response = client.get("/api/search", params={"query": "revenue", "company": "MSFT", "year": 2024, "mode": "hybrid"})
        response.raise_for_status()
        planners = client.get("/api/planners").json()
        checks["current_http"] = {"task_id": task_id, "report_and_resume": "passed", "search": "passed",
                                  "planner_availability": {key: value["available"] for key,value in planners.items()}}
    log = (ROOT / "project_notes/run_logs/project_closure_reinspection_tests.log").read_text(encoding="utf-8")
    matched = re.search(r"Ran (\d+) tests in ([\d.]+)s", log)
    if not matched or int(matched[1]) != 158 or "\nOK\n" not in log:
        raise ValueError("The current complete regression failed")
    checks["current_unit_tests"] = {"passed": 158, "seconds": float(matched[2])}
    result = {**manifest(ROOT, "project-closure-reinspection", {}), "status": "passed", "checks": checks,
        "new_model_requests": 0, "new_gpu_requests": 0, "new_fmp_requests": 0,
        "gpu_review_method": "retained_predictions_and_model_hashes_without_restarting_instance"}
    write_json(ROOT / "project_notes/run_logs/project_closure_reinspection.json", result)
    print(json.dumps({"status": "passed", "raw_sources": len(sources), "business_manifests": len(frozen),
        "historical_rows": sum(run["rows"] for run in checks["historical_experiments"].values()),
        "current_tests": 158, "new_model_requests": 0}))


if __name__ == "__main__":
    main()
