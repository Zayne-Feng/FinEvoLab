"""Bind runtime input bytes to Git while verifying historical newline equivalence."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.runtime import write_json


def main(args):
    archived_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    for name in args.experiment:
        directory = ROOT / "project_notes/experiments" / name
        record = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        inputs = {**record["config"]["source_hashes"], **record["config"]["source_files"]}
        variants = []
        for path, expected in inputs.items():
            runtime = (ROOT / path).read_bytes()
            archived = subprocess.check_output(["git", "show", f"{archived_commit}:{path}"], cwd=ROOT)
            historical = subprocess.check_output(["git", "show", f"{record['commit']}:{path}"], cwd=ROOT)
            historical_digest = hashlib.sha256(historical).hexdigest()
            if sha256(ROOT / path) != expected or hashlib.sha256(archived).hexdigest() != expected:
                raise ValueError(f"Runtime input is not archived byte-for-byte: {path}")
            if historical_digest != expected:
                if (not path.startswith("data/") or not path.endswith(".json")
                        or historical.replace(b"\r\n", b"\n") != runtime.replace(b"\r\n", b"\n")
                        or json.loads(historical) != json.loads(runtime)):
                    raise ValueError(f"Historical input differs beyond JSON line endings: {path}")
                variants.append({"path": path, "runtime_sha256": expected,
                    "executed_git_sha256": historical_digest, "difference": "CRLF_LF_only", "json_equal": True})
        write_json(directory / "input_archive.json", {"run_id": record["run_id"],
            "created_at": datetime.now(timezone.utc).isoformat(), "executed_commit": record["commit"],
            "archive_commit": archived_commit, "input_sha256": inputs,
            "historical_newline_variants": variants, "runtime_bytes_changed": False,
            "financial_values_changed": False, "normalization_rule": "JSON outer line endings only; code must match executed commit exactly"})
        print(json.dumps({"run_id": record["run_id"], "inputs_verified": len(inputs), "newline_variants": len(variants)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", action="append", required=True)
    main(parser.parse_args())
