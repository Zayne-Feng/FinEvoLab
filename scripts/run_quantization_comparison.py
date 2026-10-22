"""Measure paired BF16 and NF4 inference with a shared adapter and frozen input order."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.planner_benchmark import experiment_manifest, load_bundle, planner_messages, summarize_predictions
from finevolab.runtime import fingerprint, write_json
from scripts.planner_benchmark import MODEL_SPEC, TransformersBackend, adapter_identity, packages


async def main(args):
    if args.output.exists():
        raise ValueError("A new run directory is required")
    cases, contract = load_bundle(args.bundle, "test")
    warmups, _ = load_bundle(args.bundle, "development")
    order = list(cases)
    random.Random(73).shuffle(order)
    adapter = adapter_identity(args.adapter)
    config = {"bundle_sha256": sha256(args.bundle / "manifest.json"), "adapter": adapter,
        "mode": args.mode, "generation": {"temperature": 0, "max_new_tokens": 512, "max_input_tokens": 8192,
            "repairs": 0}, "case_order": [c.id for c in order], "order_seed": 73, "warmup_cases": [c.id for c in warmups[:3]],
        "model": MODEL_SPEC, "dtype": "bfloat16", "repeats": 1, "packages": packages(),
        "source_role": "post_training_quantization_regression_on_observed_frozen_test",
        "code": {name: sha256(ROOT / name) for name in ("scripts/run_quantization_comparison.py", "scripts/planner_benchmark.py",
            "finevolab/planner_benchmark.py")}}
    args.output.mkdir(parents=True)
    write_json(args.output / "manifest.json", experiment_manifest(ROOT, args.output.name, config))
    backend = TransformersBackend(SimpleNamespace(device="cuda", dtype="bfloat16", quantization=args.mode,
        model=MODEL_SPEC["model"], revision=MODEL_SPEC["revision"], adapter=args.adapter,
        max_input_tokens=8192, max_new_tokens=512))
    warmed = []
    for case in warmups[:3]:
        warmed.append(await backend.generate(planner_messages(contract, case.input.model_dump())))
    write_json(args.output / "warmups.json", {"excluded_from_scores": True, "attempts": warmed})
    rows = []
    for case in order:
        result = await backend.generate(planner_messages(contract, case.input.model_dump()))
        row = {"case_id": case.id, "repeat": 0, "input_sha256": fingerprint(case.input.model_dump()), "attempts": [result]}
        with (args.output / "predictions.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
        rows.append(row)
        print(json.dumps({"mode": args.mode, "completed": len(rows), "planned": len(cases)}), flush=True)
    write_json(args.output / "summary.json", {"status": "completed", **summarize_predictions(cases, rows, contract["aliases"]),
        "completed_at": datetime.now(timezone.utc).isoformat(), "hardware": backend.hardware})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--mode", choices=["none", "nf4"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
