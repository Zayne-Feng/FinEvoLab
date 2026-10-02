"""Load model connection settings without exposing credentials."""

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str
    api_key: str = field(repr=False)

    @property
    def extra_body(self) -> dict:
        hostname = urlsplit(self.base_url).hostname or ""
        if any(hostname == domain or hostname.endswith("." + domain) for domain in ("bigmodel.cn", "deepseek.com")):
            return {"thinking": {"type": "disabled"}}
        return {}

    @classmethod
    def load(cls, path: Path | None = None) -> "ModelConfig":
        if path is None:
            path = Path(os.environ.get("FINEVOLAB_MODEL_CONFIG", str(
                Path(__file__).resolve().parents[1] / "finrobot_equity/core/config/config.ini")))
        parser = configparser.ConfigParser(interpolation=None)
        with path.open(encoding="utf-8-sig") as handle:
            parser.read_file(handle)
        section = parser["API_KEYS"]
        key = section.get("openai_api_key", "").strip()
        model = section.get("openai_model", "").strip()
        base_url = section.get("openai_base_url", "").strip().rstrip("/") + "/"
        parts = urlsplit(base_url)
        if not key or key.startswith("YOUR_"):
            raise ValueError("A model API key is required")
        if not model or parts.scheme != "https" or not parts.hostname:
            raise ValueError("A model and HTTPS base URL are required")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("The base URL must not contain credentials, query, or fragment")
        return cls(model=model, base_url=base_url, api_key=key)
