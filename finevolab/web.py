"""Serve financial analysis, evidence, and report downloads locally."""

from pathlib import Path
from uuid import uuid4
import json

from fastapi import APIRouter, FastAPI, HTTPException, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated, Literal

from finevolab.tracing import TraceMiddleware
from finevolab.tools import FinancialStore
from finevolab.derived import DerivedMetric, DerivedResult, validate_derived
from finevolab.scenarios import ScenarioRequest
from finevolab.retrieval import search_corpus
from finevolab.workflow import run_task, run_question
from finevolab.config import ModelConfig
from finevolab.uploads import ingest_upload
from finevolab.storage import recent_tasks
from finevolab.planning import PlannerBackend, PlannerClient
from finevolab.runtime import read_json, observe


ROOT = Path(__file__).resolve().parents[1]
router = APIRouter()


class TaskRequest(BaseModel):
    company: str = Field(default="MSFT", pattern=r"^[A-Z0-9.-]{1,12}$")
    fiscal_year: int = Field(default=2024, ge=2000, le=2100)
    mode: Literal["offline", "agent"] = "offline"


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=12000)
    upload_ids: list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = Field(default_factory=list, max_length=5)
    planner_backend: PlannerBackend = "api"
    tool_backend: Literal["direct", "mcp"] = "direct"


class DerivedMetricRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str = Field(min_length=1, max_length=100)
    fiscal_year: int = Field(ge=1900, le=2200)
    metric: DerivedMetric
    upload_ids: list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = Field(default_factory=list, max_length=5)


@router.post("/api/derived-metrics", response_model=DerivedResult)
def derived_metric(request: DerivedMetricRequest):
    try:
        path = ROOT / "data/processed/reports.json"
        store = (FinancialStore.from_uploads(path, request.upload_ids, ROOT / "data/uploads")
                 if request.upload_ids else FinancialStore(path))
        with observe([], "tool", "calculate_metrics"):
            return validate_derived(store, store.derive(request.company, request.metric, request.fiscal_year))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.post("/api/scenarios")
def scenarios(request: ScenarioRequest):
    from finevolab.scenarios import calculate_scenarios, validate_scenarios
    store = FinancialStore(ROOT / "data/processed/reports.json")
    try:
        return validate_scenarios(store, calculate_scenarios(store, request)).model_dump(mode="json")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


class SourceMetricRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str = Field(min_length=1, max_length=100)
    fiscal_year: int = Field(ge=1900, le=2200)
    metric: Literal["revenue", "operating_income", "net_income"]
    upload_ids: list[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = Field(default_factory=list, max_length=5)


@router.post("/api/source-reconciliation")
def source_reconciliation(request: SourceMetricRequest):
    from finevolab.reconciliation import reconcile_metric
    path = ROOT / "data/processed/reports.json"
    try:
        store = (FinancialStore.from_uploads(path, request.upload_ids, ROOT / "data/uploads")
                 if request.upload_ids else FinancialStore(path))
        return reconcile_metric(store, request.company, request.metric, request.fiscal_year)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.get("/api/planners")
async def planners():
    client = PlannerClient()
    available = await client.available()
    return {"api": {"available": True}, **{name: {"available": available, "identity": client.identity(name)}
        for name in ("lora_bf16", "lora_nf4")}}


@router.get("/", response_class=HTMLResponse)
@router.get("/finevolab", response_class=HTMLResponse)
def index():
    return (ROOT / "finevolab/templates/dashboard.html").read_text(encoding="utf-8")


@router.get("/api/corpus")
def corpus():
    store = FinancialStore(ROOT / "data/processed/reports.json")
    return {"data_version": store.version, "reports": [report["source"] for report in store.data["reports"]]}


@router.post("/api/tasks")
async def create_task(request: TaskRequest):
    task_id = uuid4().hex
    try:
        return await run_task(task_id, request.company, request.fiscal_year, request.mode)
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"task_id": task_id, "error_type": type(error).__name__}) from None
    except Exception as error:
        raise HTTPException(status_code=503, detail={"task_id": task_id, "error_type": type(error).__name__}) from None


@router.post("/api/questions")
async def create_question(request: QuestionRequest):
    task_id = uuid4().hex
    try:
        settings = ModelConfig.load()
        candidate = json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8"))
        return await run_question(task_id, request.question, settings, candidate, upload_ids=request.upload_ids,
                                  planner_backend=request.planner_backend, tool_backend=request.tool_backend)
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"task_id": task_id, "error_type": type(error).__name__}) from None
    except Exception as error:
        raise HTTPException(status_code=503, detail={"task_id": task_id, "error_type": type(error).__name__}) from None


def task_directory(task_id: str) -> Path:
    if len(task_id) != 32 or any(character not in "0123456789abcdef" for character in task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    path = ROOT / "output/tasks" / task_id
    if not (path / "state.json").exists():
        raise HTTPException(status_code=404, detail="Task not found")
    return path


@router.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    return read_json(task_directory(task_id) / "state.json")


@router.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    state = get_task(task_id)
    versions = state["versions"]
    try:
        if versions.get("workflow") == "question":
            settings = ModelConfig.load()
            candidate = json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8"))
            from finevolab.runtime import RunBudget
            return await run_question(task_id, versions["question"], settings, candidate,
                upload_ids=versions.get("upload_ids", []), budget=RunBudget(**versions["budget"]) if "budget" in versions else None,
                corpus_profile=versions.get("corpus_profile", "legacy"), planner_backend=versions.get("planner_backend", "api"))
        return await run_task(task_id, versions["company"], versions["fiscal_year"], versions["mode"])
    except Exception as error:
        raise HTTPException(status_code=409, detail={"task_id": task_id, "error_type": type(error).__name__}) from None


@router.get("/api/tasks/{task_id}/report/{format}")
def download_report(task_id: str, format: Literal["html", "pdf"]):
    directory = task_directory(task_id)
    state = read_json(directory / "state.json")
    if state["phases"].get("report", {}).get("status") != "complete":
        raise HTTPException(status_code=404, detail="Report is not ready")
    path = directory / "report" / f"report.{format}"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report is not ready")
    return FileResponse(path, media_type="application/pdf" if format == "pdf" else "text/html")


@router.get("/api/search")
def search(query: str, company: str = "MSFT", year: int = 2024,
           mode: Literal["bm25", "vector", "hybrid"] = "hybrid"):
    return search_corpus(ROOT, query, company, year, mode)


@router.post("/api/uploads")
def upload_report(file: UploadFile = File(...), company: str = Form(...), fiscal_year: int = Form(...)):
    raw = file.file.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Report exceeds the upload size limit")
    try:
        result = ingest_upload(raw, Path(file.filename or "").suffix.lower(), company, fiscal_year, ROOT / "data/uploads")
        return {key: value for key, value in result.items() if key != "chunks"} | {"chunk_count": len(result["chunks"])}
    except Exception as error:
        raise HTTPException(status_code=422, detail={"error_type": type(error).__name__}) from None


@router.get("/api/uploads")
def list_uploads():
    directory = ROOT / "data/uploads"
    results = []
    for path in sorted(directory.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        results.append({key: document[key] for key in ("id", "company", "report_year", "status")})
    return results


@router.get("/api/uploads/{identifier}/original")
def uploaded_original(identifier: str):
    if len(identifier) != 64 or any(character not in "0123456789abcdef" for character in identifier):
        raise HTTPException(status_code=404, detail="Report not found")
    metadata = ROOT / "data/uploads" / (identifier + ".json")
    if not metadata.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    record = json.loads(metadata.read_text(encoding="utf-8"))
    return FileResponse(ROOT / "data/uploads" / record["path"], filename=record["path"])


@router.get("/api/uploads/{identifier}/metrics")
def uploaded_metrics(identifier: str):
    if len(identifier) != 64 or any(character not in "0123456789abcdef" for character in identifier):
        raise HTTPException(status_code=404, detail="Report not found")
    path = ROOT / "data/uploads" / (identifier + ".json")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return json.loads(path.read_text(encoding="utf-8")).get("metric_results", {"status": "not_attempted"})


@router.get("/api/finevolab/history")
def task_history():
    return [{"task_id": item["task_id"], "status": item["status"],
             "company": item["versions"]["company"], "fiscal_year": item["versions"]["fiscal_year"]}
            for item in recent_tasks()]


app = FastAPI(title="FinEvoLab")
app.include_router(router)
app.mount("/upstream-static", StaticFiles(directory=ROOT / "finrobot_equity/web_app/static"), name="upstream-static")

app.add_middleware(TraceMiddleware, directory=Path(__file__).resolve().parents[1] / "output/traces")
