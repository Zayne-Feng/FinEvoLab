"""Verify shared provider circuits, recovery probes, and failure categories."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from filelock import FileLock
import httpx
from openai import AsyncOpenAI, APIStatusError

from finevolab.circuit import ProviderCircuit, provider_circuit
from finevolab.provider import SerializedAsyncTransport, SerializedTransport


class CircuitTests(unittest.TestCase):
    def test_one_recovery_probe_is_allowed(self):
        clock = [0.0]
        circuit = ProviderCircuit(threshold=2, recovery_seconds=10, clock=lambda: clock[0])
        for status in (500, 429):
            self.assertTrue(circuit.allow())
            circuit.record(status)
        self.assertFalse(circuit.allow())
        clock[0] = 11
        self.assertTrue(circuit.allow())
        self.assertFalse(circuit.allow())
        circuit.record(503)
        self.assertFalse(circuit.allow())
        clock[0] = 22
        self.assertTrue(circuit.allow())
        circuit.record(200)
        self.assertTrue(circuit.allow())
        self.assertEqual(circuit.failures, 0)

    def test_authentication_errors_do_not_open_the_circuit(self):
        circuit = ProviderCircuit()
        for _ in range(10):
            self.assertTrue(circuit.allow())
            circuit.record(401)
        self.assertEqual(circuit.failures, 0)

    def test_sync_transport_blocks_after_transient_failures(self):
        calls = []
        def respond(request):
            calls.append(1)
            return httpx.Response(503)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch("finevolab.provider.lock_paths", return_value=(FileLock(str(path / "lock")), path / "state.json")), patch(
                    "finevolab.provider.delay_remaining", return_value=0):
                transport = SerializedTransport("https://sync.invalid", httpx.MockTransport(respond), ProviderCircuit())
                with httpx.Client(transport=transport) as client:
                    responses = [client.get("https://sync.invalid") for _ in range(4)]
        self.assertEqual(len(calls), 3)
        self.assertEqual(responses[-1].json()["error"]["code"], "circuit_open")


class AsyncCircuitTests(unittest.IsolatedAsyncioTestCase):
    async def test_clients_share_state_and_sdk_does_not_retry_open_circuit(self):
        calls = []
        async def respond(request):
            calls.append(1)
            return httpx.Response(500, json={"error": {"message": "Unavailable"}})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            provider_circuit.cache_clear()
            with patch("finevolab.provider.lock_paths", return_value=(FileLock(str(path / "lock")), path / "state.json")), patch(
                    "finevolab.provider.delay_remaining", return_value=0):
                one = SerializedAsyncTransport("https://shared.invalid/", httpx.MockTransport(respond))
                two = SerializedAsyncTransport("https://shared.invalid", httpx.MockTransport(respond))
                self.assertIs(one.circuit, two.circuit)
                async with httpx.AsyncClient(transport=one) as client:
                    for _ in range(3):
                        await client.get("https://shared.invalid")
                async with AsyncOpenAI(api_key="test", base_url="https://shared.invalid/v1",
                        http_client=httpx.AsyncClient(transport=two)) as sdk:
                    with self.assertRaises(APIStatusError) as raised:
                        await sdk.chat.completions.create(model="test", messages=[])
                self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(len(calls), 3)

    async def test_network_timeouts_trip_but_cancellation_does_not(self):
        async def timeout(request):
            raise httpx.ReadTimeout("Unavailable", request=request)
        async def cancel(request):
            raise asyncio.CancelledError()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            circuit = ProviderCircuit()
            with patch("finevolab.provider.lock_paths", return_value=(FileLock(str(path / "lock")), path / "state.json")), patch(
                    "finevolab.provider.delay_remaining", return_value=0):
                async with httpx.AsyncClient(transport=SerializedAsyncTransport("https://timeout.invalid",
                        httpx.MockTransport(timeout), circuit)) as client:
                    for _ in range(3):
                        with self.assertRaises(httpx.ReadTimeout):
                            await client.get("https://timeout.invalid")
                    self.assertEqual((await client.get("https://timeout.invalid")).status_code, 503)
                other = ProviderCircuit()
                async with httpx.AsyncClient(transport=SerializedAsyncTransport("https://cancel.invalid",
                        httpx.MockTransport(cancel), other)) as client:
                    with self.assertRaises(asyncio.CancelledError):
                        await client.get("https://cancel.invalid")
                self.assertEqual(other.failures, 0)


if __name__ == "__main__":
    unittest.main()
