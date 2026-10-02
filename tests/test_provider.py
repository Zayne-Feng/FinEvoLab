"""Verify serialized provider requests without network access."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from filelock import FileLock
import httpx
from finevolab.provider import SerializedAsyncTransport, save_cooldown


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_client_never_runs_two_requests_concurrently(self):
        active, maximum = 0, 0
        async def respond(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return httpx.Response(200, json={"ok": True})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch("finevolab.provider.lock_paths", return_value=(FileLock(str(path / "lock")), path / "state.json")), patch(
                "finevolab.provider.delay_remaining", return_value=0
            ):
                transport = SerializedAsyncTransport("https://example.com/", httpx.MockTransport(respond))
                async with httpx.AsyncClient(transport=transport) as client:
                    results = await asyncio.gather(client.get("https://example.com/1"), client.get("https://example.com/2"))
            self.assertEqual(maximum, 1)
            self.assertTrue(all(response.json()["ok"] for response in results))

    async def test_rate_limit_sets_longer_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with patch("finevolab.provider.time.time", return_value=100):
                save_cooldown(path, 429)
            self.assertEqual(json.loads(path.read_text())["next_request_at"], 115)


if __name__ == "__main__":
    unittest.main()
