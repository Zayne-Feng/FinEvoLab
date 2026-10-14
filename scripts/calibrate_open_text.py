"""Measure agreement between independent open-text rubric ratings."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.rubric import calibrate
from finevolab.runtime import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args()
    rubric_path = ROOT / "configs/open_text_rubric.json"
    controls = json.loads((ROOT / "data/evaluation/calibration/controls.json").read_text())
    if controls["rubric_sha256"] != sha256(rubric_path):
        raise ValueError("Calibration controls use a different rubric")
    result = calibrate(json.loads(rubric_path.read_text()), controls,
                       json.loads(args.first.read_text(encoding="utf-8-sig")), json.loads(args.second.read_text(encoding="utf-8-sig")))
    result["rating_file_sha256"] = [sha256(args.first), sha256(args.second)]
    write_json(ROOT / "project_notes/evaluations/open_text_rater_agreement.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
