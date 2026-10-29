"""Configure optional retrieval services and collect backend observations."""

from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RedisSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    url: str = "redis://127.0.0.1:6379/0"
    prefix: str = Field(default="finevolab:retrieval", pattern=r"^[a-zA-Z0-9:_-]+$")
    ttl_seconds: int = Field(default=3600, ge=1)
    timeout_seconds: float = Field(default=0.5, gt=0, le=10)


class MilvusSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uri: str = "http://127.0.0.1:19530"
    timeout_seconds: float = Field(default=10, gt=0, le=120)
    fallback: bool = False


class RetrievalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vector_backend: Literal["numpy", "milvus"] = "numpy"
    redis: RedisSettings = Field(default_factory=RedisSettings)
    milvus: MilvusSettings = Field(default_factory=MilvusSettings)


SERVICE_EVENTS = ContextVar("retrieval_service_events", default=None)


def load_settings(root: Path) -> RetrievalSettings:
    path = Path(os.environ.get("FINEVOLAB_RETRIEVAL_SERVICES_CONFIG", root / "configs/retrieval_services.json"))
    return RetrievalSettings.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else RetrievalSettings()


def service_event(event: str, **values):
    events = SERVICE_EVENTS.get()
    if events is not None:
        events.append({"event": event, **values})


@contextmanager
def retrieval_observations(events: list):
    token = SERVICE_EVENTS.set(events)
    try:
        yield
    finally:
        SERVICE_EVENTS.reset(token)
