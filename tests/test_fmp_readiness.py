"""Verify missing credentials cannot cause requests or credential disclosure."""

import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_fmp_readiness import readiness
from scripts import seed_fmp_cache


class FmpReadinessTests(unittest.TestCase):
    def test_acquisition_time_can_be_recovered_from_private_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "project_notes/run_logs/fmp_stable_access.json"
            stamp = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
            audit_path = root / "project_notes/run_logs/file_cleanup_timestamp_cleanup.json"
            audit_path.parent.mkdir(parents=True)
            audit_path.write_text(json.dumps({"removed_metadata": {relative: {"/created_at": stamp}}}))
            with patch.object(seed_fmp_cache, "ROOT", root):
                self.assertEqual(seed_fmp_cache.acquisition_time({}, root / relative), stamp)

    def test_missing_acquisition_time_is_not_replaced_with_the_current_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(seed_fmp_cache, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "acquisition time"):
                    seed_fmp_cache.acquisition_time({}, root / "access.json")

    def test_missing_and_placeholder_credentials_do_not_create_a_client(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            for value in ("", "YOUR_FMP_API_KEY"):
                path.write_text("[API_KEYS]\nfmp_api_key = " + value, encoding="utf-8")
                with patch("scripts.check_fmp_readiness.httpx.Client") as client:
                    result = readiness(path, probe=True, environment={})
                self.assertEqual(result["status"], "waiting_for_fmp_key")
                self.assertEqual(result["requests_sent"], 0)
                client.assert_not_called()

    def test_configuration_presence_does_not_claim_verified_access(self):
        value = "fixture-" + "x" * 24
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text("[API_KEYS]\nfmp_api_key = " + value, encoding="utf-8")
            result = readiness(path, environment={})
        self.assertTrue(result["configured"])
        self.assertEqual(result["legacy_endpoint_access"], "not_verified")
        self.assertNotIn(value, json.dumps(result))

    def test_environment_credentials_are_supported_without_a_file(self):
        result = readiness(Path("missing-config.ini"), environment={"FMP_API_KEY": "fixture-" + "x" * 24})
        self.assertEqual(result["status"], "ready_for_endpoint_probe")
        self.assertEqual(result["requests_sent"], 0)

    def test_stable_probe_does_not_claim_legacy_access(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.get.return_value.status_code = 200
        client.get.return_value.is_success = True
        client.get.return_value.json.return_value = [{"symbol": "MSFT"}]
        with patch("scripts.check_fmp_readiness.httpx.Client") as factory:
            factory.return_value.__enter__.return_value = client
            result = readiness(Path("missing-config.ini"), probe=True, api_version="stable",
                               environment={"FMP_API_KEY": "fixture-" + "x" * 24})
        self.assertEqual(result["stable_endpoint_access"], "verified")
        self.assertEqual(result["legacy_endpoint_access"], "not_verified")
        self.assertEqual(result["requests_sent"], 5)
        self.assertTrue(all(call.args[0].startswith("https://financialmodelingprep.com/stable/") for call in client.get.call_args_list))


if __name__ == "__main__":
    unittest.main()
