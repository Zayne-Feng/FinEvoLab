"""Serve a pinned LoRA request planner on a loopback-only HTTP interface."""

import argparse
import asyncio
from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI, HTTPException
from finevolab.experiments import sha256
from finevolab.planner_benchmark import load_bundle, parsed_plan, planner_messages
from finevolab.planning import PlanRequest
from finevolab.runtime import fingerprint
from scripts.planner_benchmark import MODEL_SPEC, TransformersBackend, adapter_identity


class PlannerEngine:
    def __init__(self, bundle, adapter, default_backend):
        _, self.contract = load_bundle(bundle, "development")
        self.contract_sha256 = sha256(bundle / "contract.json")
        identity = adapter_identity(adapter)
        if identity["training"] != {**identity["training"], "model": MODEL_SPEC["model"],
            "revision": MODEL_SPEC["revision"], "bundle_sha256": sha256(bundle / "manifest.json")}:
            raise ValueError("The adapter uses a different base model or contract")
        self.adapter, self.backend, self.active = adapter, None, None
        self.completed_requests = 0
        self.adapter_sha256 = identity["files"]["adapter_model.safetensors"]
        self.lock = threading.Lock()
        self.load(default_backend)

    def identity(self, backend):
        return {"backend": backend, "model": MODEL_SPEC["model"], "revision": MODEL_SPEC["revision"],
            "adapter_sha256": self.adapter_sha256, "contract_sha256": self.contract_sha256,
            "quantization": "nf4" if backend == "lora_nf4" else "none", "dtype": "bfloat16"}

    def load(self, backend):
        if self.active == backend:
            return
        if backend not in {"lora_bf16", "lora_nf4"}:
            raise ValueError("Unknown planner backend")
        if self.backend is not None:
            self.backend = None
            gc.collect()
            import torch
            torch.cuda.empty_cache()
        self.active = None
        self.backend = TransformersBackend(SimpleNamespace(device="cuda", dtype="bfloat16",
            quantization="nf4" if backend == "lora_nf4" else "none", model=MODEL_SPEC["model"],
            revision=MODEL_SPEC["revision"], adapter=self.adapter, max_input_tokens=8192, max_new_tokens=512))
        self.active = backend


def create_app(engine):
    app = FastAPI(title="FinEvoLab Planner", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        return {"status": "ready", **{key: value for key, value in engine.identity(engine.active).items() if key != "backend"},
                "loaded_backend": engine.active, "supported_backends": ["lora_bf16", "lora_nf4"],
                "completed_requests": engine.completed_requests}

    @app.post("/v1/plans")
    def plan(request: PlanRequest):
        if request.contract_sha256 != engine.contract_sha256:
            raise HTTPException(409, "Planning contract differs")
        if not engine.lock.acquire(blocking=False):
            raise HTTPException(429, "Planner is busy")
        try:
            engine.load(request.backend)
            messages = planner_messages(engine.contract, {"question": request.question,
                "selected_documents": [document.model_dump() for document in request.selected_documents]})
            result = asyncio.run(engine.backend.generate(messages))
            try:
                parsed_plan(result["raw"])
                valid = True
            except ValueError:
                valid = False
            identity = engine.identity(request.backend)
            engine.completed_requests += 1
            request_id = uuid4().hex
            record = {"request_id": request_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "input_sha256": fingerprint({"question": request.question,
                    "selected_documents": [d.model_dump() for d in request.selected_documents]}),
                "identity": identity, "usage": result["usage"], "schema_valid": valid,
                "elapsed_seconds": result["elapsed_seconds"], "peak_allocated_bytes": result["peak_allocated_bytes"]}
            directory = ROOT / "project_notes/run_logs"
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / "planner_service_requests.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
                handle.flush()
            return {**result, "identity": identity, "schema_valid": valid, "request_id": request_id}
        finally:
            engine.lock.release()

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--default-backend", choices=["lora_bf16", "lora_nf4"], default="lora_bf16")
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(PlannerEngine(args.bundle, args.adapter, args.default_backend)), host="127.0.0.1",
                port=args.port, access_log=False)
