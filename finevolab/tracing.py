"""Correlate execution spans using W3C trace context and relative durations."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
import logging
import re
from threading import RLock
import time
from uuid import uuid4

ACTIVE_TRACE = ContextVar("financial_trace", default=None)
ACTIVE_SPAN = ContextVar("financial_span", default=None)
TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
ATTRIBUTES = {"kind", "phase", "attempt", "cache_hit", "tokens", "backend", "model",
              "status_code", "method", "recovered", "task_id", "data_version"}


def parse_traceparent(value: str | None):
    match = TRACEPARENT.fullmatch(value or "")
    if not match or int(match[1], 16) == 0 or int(match[2], 16) == 0:
        return None
    return match[1], match[2]


@dataclass
class TraceBuffer:
    trace_id: str
    directory: Path | None
    started: float = field(default_factory=time.perf_counter)
    spans: list = field(default_factory=list)
    segment_id: str = field(default_factory=lambda: uuid4().hex[:16])
    lock: RLock = field(default_factory=RLock)
    export_errors: int = 0
    active_spans: int = 0

    def flush(self):
        if self.directory is None:
            return
        from finevolab.runtime import write_json
        try:
            with self.lock:
                write_json(self.directory / (self.trace_id + "." + self.segment_id + ".json"),
                           {"schema_version": 1, "trace_id": self.trace_id, "segment_id": self.segment_id, "spans": list(self.spans)})
        except OSError as error:
            self.export_errors += 1
            logging.getLogger(__name__).warning("Trace export failed: %s", type(error).__name__)


def traceparent() -> str | None:
    trace, parent = ACTIVE_TRACE.get(), ACTIVE_SPAN.get()
    return f"00-{trace.trace_id}-{parent}-01" if trace and parent else None


@contextmanager
def span(name: str, **attributes):
    trace = ACTIVE_TRACE.get()
    if trace is None:
        yield {}
        return
    started = time.perf_counter()
    record = {"name": name, "trace_id": trace.trace_id, "span_id": uuid4().hex[:16],
              "parent_span_id": ACTIVE_SPAN.get(), "status": "running",
              "start_offset_seconds": round(started - trace.started, 6),
              "attributes": {key: value for key, value in attributes.items() if key in ATTRIBUTES}}
    with trace.lock:
        trace.spans.append(record)
        trace.active_spans += 1
    token = ACTIVE_SPAN.set(record["span_id"])
    try:
        yield record
    except BaseException as error:
        record.update(status="failed", error_type=type(error).__name__)
        if isinstance(error, TimeoutError):
            record["status"] = "timeout"
        raise
    else:
        if record["status"] == "running":
            record["status"] = "complete"
    finally:
        record["duration_seconds"] = round(time.perf_counter() - started, 6)
        ACTIVE_SPAN.reset(token)
        with trace.lock:
            trace.active_spans -= 1
            flush = trace.active_spans == 0 or trace.spans[0] is record
        if flush:
            trace.flush()


@contextmanager
def trace_scope(name: str, directory: Path | None = None, incoming: str | None = None):
    if ACTIVE_TRACE.get() is not None:
        with span(name) as record:
            yield ACTIVE_TRACE.get(), record
        return
    parent = parse_traceparent(incoming)
    trace = TraceBuffer(parent[0] if parent else uuid4().hex, directory)
    token = ACTIVE_TRACE.set(trace)
    parent_token = ACTIVE_SPAN.set(parent[1] if parent else None)
    try:
        with span(name) as record:
            yield trace, record
    finally:
        ACTIVE_SPAN.reset(parent_token)
        ACTIVE_TRACE.reset(token)


class TraceMiddleware:
    def __init__(self, app, directory: Path):
        self.app, self.directory = app, directory

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = dict(scope.get("headers", [])).get(b"traceparent", b"").decode("ascii", errors="ignore")
        with trace_scope("http.request", self.directory, incoming) as (_, record):
            record["attributes"]["method"] = scope["method"]
            async def traced_send(message):
                if message["type"] == "http.response.start":
                    record["attributes"]["status_code"] = message["status"]
                    if message["status"] >= 500:
                        record["status"] = "failed"
                    message = dict(message)
                    message["headers"] = list(message.get("headers", [])) + [(b"traceparent", traceparent().encode())]
                await send(message)
            await self.app(scope, receive, traced_send)
