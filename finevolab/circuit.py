"""Coordinate transient provider failures across clients in one process."""

from functools import lru_cache
from threading import RLock
import time

import httpx


class ProviderCircuit:
    def __init__(self, threshold: int = 3, recovery_seconds: float = 30, clock=None):
        if threshold < 1 or recovery_seconds <= 0:
            raise ValueError("Positive circuit limits are required")
        self.threshold, self.recovery_seconds = threshold, recovery_seconds
        self.clock = clock or time.monotonic
        self.failures, self.opened, self.probe = 0, None, False
        self.lock = RLock()

    def allow(self) -> bool:
        with self.lock:
            if self.opened is None:
                return True
            if self.clock() - self.opened < self.recovery_seconds or self.probe:
                return False
            self.probe = True
            return True

    def record(self, status: int | None):
        with self.lock:
            transient = status is None or status in {408, 409, 429} or status >= 500
            if transient:
                self.failures += 1
                if self.probe or self.failures >= self.threshold:
                    self.opened = self.clock()
            else:
                self.failures, self.opened = 0, None
            self.probe = False

    def release(self):
        with self.lock:
            self.probe = False


@lru_cache(maxsize=64)
def provider_circuit(base_url: str) -> ProviderCircuit:
    return ProviderCircuit()


def unavailable_response(request) -> httpx.Response:
    return httpx.Response(503, request=request, headers={"x-should-retry": "false"},
        json={"error": {"message": "Provider temporarily unavailable", "type": "provider_unavailable",
                        "code": "circuit_open"}})
