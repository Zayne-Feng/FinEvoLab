"""Bootstrap paired task differences while keeping repeated answers together."""

import argparse
from collections import defaultdict
import json
from pathlib import Path

import numpy as np


def estimate(report, draws=10000, seed=17):
    if not report["complete_pairing"] or report["observed_pairs"] != report["expected_pairs"]:
        raise ValueError("A complete paired comparison is required")
    groups = defaultdict(list)
    for pair in report["pairs"]:
        groups[pair["case_id"]].append(pair["difference"])
    if not groups or len({len(values) for values in groups.values()}) != 1:
        raise ValueError("Each task must have the same number of paired repetitions")
    means = np.array([np.mean(groups[key]) for key in sorted(groups)])
    generator = np.random.default_rng(seed)
    samples = generator.choice(means, size=(draws, len(means)), replace=True).mean(axis=1)
    lower, upper = np.quantile(samples, [0.025, 0.975])
    return {"left_run": report["left_run"], "right_run": report["right_run"],
            "method": "paired_case_cluster_percentile_bootstrap", "seed": seed, "draws": draws,
            "case_clusters": len(means), "repeats_per_case": len(next(iter(groups.values()))),
            "mean_difference": float(means.mean()), "confidence_level": 0.95,
            "interval": [float(lower), float(upper)], "repeats_treated_as_independent_cases": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("comparison", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = estimate(json.loads(args.comparison.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
