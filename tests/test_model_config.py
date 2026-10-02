"""Verify secret-safe configuration loading."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from finevolab.config import ModelConfig


class ModelConfigTests(unittest.TestCase):
    def test_environment_selects_a_configuration_path_without_exposing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider.ini"
            path.write_text("[API_KEYS]\nopenai_api_key=private-value\nopenai_model=example\nopenai_base_url=https://example.com/v1/\n")
            with patch.dict("os.environ", {"FINEVOLAB_MODEL_CONFIG": str(path)}):
                settings = ModelConfig.load()
        self.assertEqual(settings.model, "example")
        self.assertNotIn("private-value", repr(settings))

    def test_provider_options_do_not_leak_to_other_endpoints(self):
        self.assertEqual(ModelConfig("glm", "https://open.bigmodel.cn/api/paas/v4/", "unused").extra_body,
                         {"thinking": {"type": "disabled"}})
        self.assertEqual(ModelConfig("other", "https://api.example.com/v1/", "unused").extra_body, {})
        self.assertEqual(ModelConfig("deepseek-flash", "https://api.deepseek.com/", "unused").extra_body,
                         {"thinking": {"type": "disabled"}})
        self.assertEqual(ModelConfig("other", "https://deepseek.com.example.com/", "unused").extra_body, {})

    def test_key_is_not_in_repr_and_percent_is_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.ini"
            path.write_text("[API_KEYS]\nopenai_api_key=private%value\nopenai_model=example\n"
                            "openai_base_url=https://example.com/v1/\n", encoding="utf-8-sig")
            result = ModelConfig.load(path)
        self.assertEqual(result.api_key, "private%value")
        self.assertNotIn("private", repr(result))

    def test_base_url_with_credentials_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.ini"
            path.write_text("[API_KEYS]\nopenai_api_key=private\nopenai_model=example\n"
                            "openai_base_url=https://user:password@example.com/v1/\n")
            with self.assertRaisesRegex(ValueError, "credentials"):
                ModelConfig.load(path)


if __name__ == "__main__":
    unittest.main()
