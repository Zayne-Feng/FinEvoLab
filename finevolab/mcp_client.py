"""Call local financial MCP tools through a validated SDK session."""

from contextlib import asynccontextmanager
from datetime import timedelta
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from finevolab.tracing import span, traceparent, trace_mode


@asynccontextmanager
async def financial_session(root: Path, policy_path: Path | None = None, preload_retrieval: bool = False):
    arguments = [str(root / "scripts/serve_mcp.py")]
    if preload_retrieval:
        arguments += ["--preload-retrieval"]
    if policy_path is not None:
        arguments += ["--policy", str(policy_path)]
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_DLL_SEARCH_MODIFICATION_ENABLE", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA") if key in os.environ}
    environment.update(PYTHONUTF8="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                       FINEVOLAB_TRACE_MODE=trace_mode())
    parameters = StdioServerParameters(command=sys.executable, args=arguments, cwd=str(root), env=environment)
    try:
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=60)) as session:
                await session.initialize()
                yield session
    except BaseExceptionGroup as error:
        leaf = error
        while isinstance(leaf, BaseExceptionGroup) and len(leaf.exceptions) == 1:
            leaf = leaf.exceptions[0]
        if not isinstance(leaf, BaseExceptionGroup):
            raise leaf from None
        raise



async def call_financial_tool(session: ClientSession, name: str, request: dict, timeout_seconds: float = 30, flush_trace: bool = False):
    if timeout_seconds <= 0:
        raise ValueError("A positive timeout is required")
    with span("mcp.client." + name, kind="client"):
        parent = traceparent()
        meta = {"traceparent": parent} if parent else {}
        if flush_trace:
            meta["flush_trace"] = True
        try:
            result = await session.call_tool(name, {"request": request},
                read_timeout_seconds=timedelta(seconds=timeout_seconds),
                meta=meta or None)
        except McpError as error:
            if error.error.code == 408:
                raise TimeoutError("The financial MCP request timed out") from error
            raise
        if result.isError:
            raise ValueError("The financial MCP tool rejected the request")
        if not isinstance(result.structuredContent, dict) or "data" not in result.structuredContent:
            raise ValueError("The financial MCP tool returned an invalid structure")
        return result.structuredContent
