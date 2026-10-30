"""Select versioned financial methods without changing the request-plan schema."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[1]


class AnalysisSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    context_encoding: bool = False
    subtask_recovery: bool = True
    method_selection: bool = False
    source_reconciliation: bool = True
    singleflight: bool = True


def load_analysis_settings(root: Path = ROOT):
    path = root / "configs/analysis_extensions.json"
    return AnalysisSettings.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else AnalysisSettings()


def method_for(plan, root: Path = ROOT):
    path = root / "configs/analysis_methods.json"
    config = json.loads((path if path.exists() else ROOT / "configs/analysis_methods.json").read_text(encoding="utf-8"))
    intent = plan.intent if hasattr(plan, "intent") else str(plan)
    topic = getattr(plan, "topic", None)
    key = "risk" if topic == "risk" and intent in {"explanation", "hybrid"} else intent
    method = config["methods"].get(key, config["methods"]["metric"])
    return {"name": key if key in config["methods"] else "metric", "version": config["version"], **method}
