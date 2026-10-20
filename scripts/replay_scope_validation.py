"""Replay fixed numeric fault controls against the active source validator."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json
from finevolab.tools import FinancialStore
from run_harness_ablations import validation_suite


def main():
    source = ROOT / "project_notes/experiments/component-numeric/tasks.jsonl"
    directory = ROOT / "project_notes/experiments/scope-validation-controls"
    directory.mkdir(parents=True, exist_ok=True)
    store = FinancialStore(ROOT / "data/processed/reports.json")
    write_json(directory / "manifest.json", manifest(ROOT, directory.name, {
        "source": str(source.relative_to(ROOT)), "source_sha256": sha256(source), "model_calls": 0,
        "intervention": "identical saved outputs and identical controlled corruptions; active validation implementation",
        "data_version": store.version}))
    validation_suite(directory, store, source)
    summary = json.loads((directory / "validation_summary.json").read_text(encoding="utf-8"))
    strict = summary["request_contract"]
    if strict["false_accepts"] or strict["false_rejects"] or strict["invalid"] != 150 or strict["valid"] != 36:
        raise ValueError("The active validator failed its fixed control set")
    print(json.dumps({"status": "passed", "groups": summary}))


if __name__ == "__main__":
    main()
