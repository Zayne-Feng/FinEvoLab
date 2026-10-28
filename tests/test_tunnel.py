"""Verify connector integrity, token handling, and readiness without external calls."""

import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import manage_tunnel as tunnel


class TunnelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.token = self.root / "token.txt"
        self.executable = self.root / "cloudflared.exe"
        self.value = base64.b64encode(json.dumps({"a": "account", "t": "tunnel", "s": "secret"}).encode()).decode()
        self.token.write_text(self.value, encoding="utf-8")
        self.binary = b"verified-release"
        self.config = {"hostname": "app.lunelu.com", "origin": "http://127.0.0.1:8085",
            "metrics": "127.0.0.1:20241", "protocol": "auto", "cloudflared": {
                "version": "2026.10.5", "url": "https://github.com/cloudflare/cloudflared/release.exe",
                "sha256": hashlib.sha256(self.binary).hexdigest(), "bytes": len(self.binary)}}
        for key, value in (("DIRECTORY", self.root), ("TOKEN", self.token), ("EXECUTABLE", self.executable)):
            context = patch.object(tunnel, key, value)
            context.start()
            self.addCleanup(context.stop)

    def response(self, content=None, status=206):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = status
        response.content = self.binary if content is None else content
        response.headers = {"Content-Range": f"bytes 0-{len(self.binary)-1}/{len(self.binary)}"}
        return response

    def test_token_file_rejects_commands_and_invalid_data(self):
        self.assertEqual(tunnel.read_token(), self.value)
        for value in ("cloudflared.exe service install " + self.value, "not-a-token", "W10=", "e30="):
            self.token.write_text(value)
            with self.assertRaises(ValueError) as error:
                tunnel.read_token()
            self.assertNotIn(self.value, str(error.exception))

    def test_download_is_verified_before_execution(self):
        with patch.object(tunnel.requests, "get", return_value=self.response()), patch.object(tunnel.subprocess, "run") as run:
            run.return_value.stdout = "cloudflared version 2026.10.5"
            result = tunnel.install(self.config)
            self.assertEqual(result["status"], "installed")
            self.assertEqual(self.executable.read_bytes(), self.binary)
            self.assertEqual(run.call_args.args[0], [str(self.executable), "--version"])
        with patch.object(tunnel.requests, "get") as get:
            self.assertEqual(tunnel.install(self.config)["status"], "installed")
            get.assert_not_called()

    def test_wrong_digest_and_range_never_execute(self):
        for response in (self.response(content=b"tampered-release"), self.response(status=200)):
            with patch.object(tunnel.requests, "get", return_value=response), patch.object(tunnel.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    tunnel.install(self.config)
                run.assert_not_called()
                self.assertFalse(self.executable.exists())
                self.assertFalse(self.executable.with_suffix(".download").exists())

    def test_start_passes_only_token_path_to_the_process(self):
        self.executable.write_bytes(self.binary)
        ready = {"backend_ready": True, "public_origin_configured": True, "tunnel_ready": False}
        with patch.object(tunnel, "status", return_value=ready), patch.object(tunnel.subprocess, "Popen") as launch:
            launch.return_value.pid = 12345
            self.assertEqual(tunnel.start(self.config)["pid"], 12345)
            command = launch.call_args.args[0]
            self.assertIn("--token-file", command)
            self.assertIn(str(self.token), command)
            self.assertNotIn(self.value, command)
            self.assertEqual(launch.call_args.kwargs["stdin"], tunnel.subprocess.DEVNULL)
        (self.root / "connector.pid").unlink()
        with patch.object(tunnel, "status", return_value={**ready, "tunnel_ready": True}):
            with self.assertRaisesRegex(ValueError, "unmanaged"):
                tunnel.start(self.config)

    def test_readiness_requires_the_public_origin(self):
        with patch.object(tunnel.requests, "Session") as constructor:
            session = constructor.return_value
            response = MagicMock(status_code=200)
            response.json.return_value = {"application": "FinEvoLab Experience", "public_origin": None}
            session.get.return_value = response
            result = tunnel.status(self.config)
            self.assertTrue(result["backend_ready"])
            self.assertFalse(result["public_origin_configured"])

    def test_verification_is_read_only_and_requires_authentication(self):
        ready = {"backend_ready": True, "public_origin_configured": True, "tunnel_ready": True}
        health = MagicMock(url="https://app.lunelu.com/experience/api/health")
        health.json.return_value = {"public_origin": "https://app.lunelu.com"}
        page = MagicMock(text='<html data-live="true">')
        denied = MagicMock(status_code=401)
        with patch.object(tunnel, "status", return_value=ready), patch.object(tunnel.requests, "Session") as constructor:
            session = constructor.return_value
            session.get.side_effect = [health, page, denied]
            self.assertEqual(tunnel.verify(self.config)["status"], "passed")
            session.post.assert_not_called()
            session.get.side_effect = [health, page, MagicMock(status_code=200)]
            with self.assertRaisesRegex(ValueError, "Anonymous"):
                tunnel.verify(self.config)


if __name__ == "__main__":
    unittest.main()
