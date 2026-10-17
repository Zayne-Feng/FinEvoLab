"""Call source-bound request planners with explicit model identities and token accounting."""

import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from finevolab.experiments import sha256
from finevolab.grounded import request_instruction
from finevolab.planner_benchmark import PlannerInput, parsed_plan

ROOT = Path(__file__).resolve().parents[1]
PlannerBackend = Literal["api", "lora_bf16", "lora_nf4"]


class PlanRequest(PlannerInput):
    backend: Literal["lora_bf16", "lora_nf4"]
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlannerServiceError(RuntimeError):
    def __init__(self, message, usage=None):
        super().__init__(message)
        self.usage = usage


class PlannerServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_url: str
    timeout_seconds: int = Field(ge=1, le=120)
    model: str
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    adapter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlannerClient:
    def __init__(self, config_path=None):
        path = Path(config_path or os.environ.get("FINEVOLAB_PLANNER_CONFIG", ROOT / "configs/planner_service.json"))
        self.config = PlannerServiceConfig.model_validate_json(path.read_text(encoding="utf-8"))
        url = urlsplit(self.config.base_url)
        if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"} or url.username or url.password:
            raise ValueError("The planner service requires a loopback HTTP endpoint")
        self.bundle = ROOT / "data/training/planner_formal/frozen_v1"
        self.contract = json.loads((self.bundle / "contract.json").read_text(encoding="utf-8"))
        self.contract_sha256 = sha256(self.bundle / "contract.json")

    def identity(self, backend):
        if backend not in {"lora_bf16", "lora_nf4"}:
            raise ValueError("Unknown planner backend")
        return {"backend": backend, "model": self.config.model, "revision": self.config.revision,
                "adapter_sha256": self.config.adapter_sha256, "contract_sha256": self.contract_sha256,
                "quantization": "nf4" if backend == "lora_nf4" else "none", "dtype": "bfloat16"}

    async def available(self):
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                response = await client.get(self.config.base_url.rstrip("/") + "/health")
                response.raise_for_status()
                value = response.json()
            return value.get("status") == "ready" and all(value.get(key) == expected for key, expected in
                (("model", self.config.model), ("revision", self.config.revision),
                 ("adapter_sha256", self.config.adapter_sha256), ("contract_sha256", self.contract_sha256)))
        except (httpx.HTTPError, ValueError):
            return False

    async def plan(self, backend, question, documents, candidate, timeout_seconds):
        if self.contract["instruction"] != request_instruction(candidate):
            raise PlannerServiceError("The planner instruction differs from the served contract")
        request = PlanRequest(question=question, selected_documents=documents, backend=backend,
                              contract_sha256=self.contract_sha256)
        try:
            async with httpx.AsyncClient(timeout=min(timeout_seconds, self.config.timeout_seconds), trust_env=False) as client:
                response = await client.post(self.config.base_url.rstrip("/") + "/v1/plans", json=request.model_dump())
                response.raise_for_status()
                record = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise PlannerServiceError("The selected planner service is unavailable") from error
        if record.get("identity") != self.identity(backend):
            raise PlannerServiceError("The planner response has an unexpected model identity")
        usage = record.get("usage")
        fields = ("requests", "input_tokens", "output_tokens", "total_tokens")
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in fields) or usage["requests"] != 1 or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
            raise PlannerServiceError("The planner response has invalid token accounting")
        try:
            parsed = parsed_plan(record["raw"])
        except (ValueError, TypeError, KeyError) as error:
            raise PlannerServiceError("The selected planner returned an invalid request plan", usage) from error
        return parsed, record
