"""Serialize provider requests and honor bounded rate-limit cooldowns."""

import asyncio
import hashlib
import json
from pathlib import Path
import time

from filelock import FileLock, Timeout
import httpx
from openai import AsyncOpenAI, OpenAI

from finevolab.config import ModelConfig
from finevolab.circuit import provider_circuit, unavailable_response


ROOT = Path(__file__).resolve().parents[1]


def lock_paths(base_url: str):
    directory = ROOT / "output/locks"
    directory.mkdir(parents=True, exist_ok=True)
    identifier = hashlib.sha256(base_url.encode()).hexdigest()[:20]
    return FileLock(str(directory / (identifier + ".lock"))), directory / (identifier + ".json")


def delay_remaining(path: Path) -> float:
    if not path.exists():
        return 0
    return max(0, json.loads(path.read_text())["next_request_at"] - time.time())


def save_cooldown(path: Path, status: int | None) -> None:
    path.write_text(json.dumps({"next_request_at": time.time() + (15 if status in {None, 429} else 2)}), encoding="utf-8")


class SerializedAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, base_url: str, transport=None, circuit=None):
        self.circuit = circuit or provider_circuit(base_url.rstrip("/"))
        self.lock, self.path = lock_paths(base_url)
        self.local_lock = asyncio.Lock()
        self.transport = transport or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        async with self.local_lock:
            return await self.handle_serialized(request)

    async def handle_serialized(self, request):
        started = time.monotonic()
        while True:
            try:
                self.lock.acquire(timeout=0)
                break
            except Timeout:
                if time.monotonic() - started > 60:
                    raise httpx.PoolTimeout("Provider request queue timed out")
                await asyncio.sleep(0.1)
        try:
            if not self.circuit.allow():
                return unavailable_response(request)
            status = None
            try:
                await asyncio.sleep(delay_remaining(self.path))
                response = await self.transport.handle_async_request(request)
                await response.aread()
                status = response.status_code
            except httpx.TransportError:
                self.circuit.record(None)
                raise
            except BaseException:
                self.circuit.release()
                raise
            else:
                self.circuit.record(status)
                return response
            finally:
                save_cooldown(self.path, status)
        finally:
            self.lock.release()

    async def aclose(self):
        await self.transport.aclose()


class SerializedTransport(httpx.BaseTransport):
    def __init__(self, base_url: str, transport=None, circuit=None):
        self.circuit = circuit or provider_circuit(base_url.rstrip("/"))
        self.lock, self.path = lock_paths(base_url)
        self.transport = transport or httpx.HTTPTransport()

    def handle_request(self, request):
        with self.lock.acquire(timeout=60):
            if not self.circuit.allow():
                return unavailable_response(request)
            status = None
            try:
                time.sleep(delay_remaining(self.path))
                response = self.transport.handle_request(request)
                response.read()
                status = response.status_code
            except httpx.TransportError:
                self.circuit.record(None)
                raise
            except BaseException:
                self.circuit.release()
                raise
            else:
                self.circuit.record(status)
                return response
            finally:
                save_cooldown(self.path, status)

    def close(self):
        self.transport.close()


def async_client(settings: ModelConfig) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=45, max_retries=2,
                       http_client=httpx.AsyncClient(transport=SerializedAsyncTransport(settings.base_url)))


def sync_client(settings: ModelConfig) -> OpenAI:
    return OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=60, max_retries=2,
                  http_client=httpx.Client(transport=SerializedTransport(settings.base_url)))
