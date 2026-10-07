"""Compute paired task differences without changing model configurations."""

import argparse
import json
from pathlib import Path
import statistics


def load(directory):
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    if summary.get("status") in {"invalid_setup", "invalid_fixture"}:
        raise ValueError("Invalid experiment setups cannot enter paired comparisons")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in (directory / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    return manifest, {(row["repeat"], row["case_id"]): row for row in records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    left_manifest, left = load(args.left)
    right_manifest, right = load(args.right)
    for name in ("split", "repeats", "task_ids", "model", "base_url", "data_version", "timeout_seconds", "dataset_manifest_sha256"):
        if left_manifest["config"].get(name) != right_manifest["config"].get(name):
            raise ValueError("Paired runs have incompatible task or model configurations")
    for path, digest in left_manifest["source_hashes"].items():
        if path.startswith(("finevolab/", "requirements/", "configs/", "data/evaluation/")):
            if right_manifest["source_hashes"].get(path) != digest:
                raise ValueError("Paired runs have different scorer, tool, dependency, or data versions")
    shared = sorted(set(left) & set(right))
    pairs = [{"repeat": key[0], "case_id": key[1], "left_score": left[key]["score"],
              "right_score": right[key]["score"], "difference": right[key]["score"] - left[key]["score"],
              "both_completed": left[key]["execution_status"] == right[key]["execution_status"] == "completed"}
             for key in shared]
    planned = left_manifest["config"]["repeats"] * len(left_manifest["config"]["task_ids"])
    report = {"left_run": left_manifest["run_id"], "right_run": right_manifest["run_id"],
              "expected_pairs": planned, "observed_pairs": len(pairs), "complete_pairing": len(pairs) == planned,
              "pairs_without_execution_failures": sum(row["both_completed"] for row in pairs),
              "wins": sum(row["difference"] > 0 for row in pairs),
              "losses": sum(row["difference"] < 0 for row in pairs),
              "ties": sum(row["difference"] == 0 for row in pairs),
              "mean_paired_difference": statistics.mean(row["difference"] for row in pairs) if pairs else None,
              "repeat_differences": {str(index): statistics.mean(row["difference"] for row in pairs if row["repeat"] == index)
                                     for index in sorted({row["repeat"] for row in pairs})},
              "pairs": pairs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "pairs"}))


if __name__ == "__main__":
    main()
