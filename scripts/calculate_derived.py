"""Calculate audited annual margins and optionally export an HTML/PDF report."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finevolab.derived import DERIVED_METRICS, validate_derived
from finevolab.evaluation import TaskAnswer
from finevolab.reporting import build_analysis_report
from finevolab.tools import FinancialStore
from finevolab.validation import validate_answer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", required=True)
    parser.add_argument("--fiscal-year", type=int, required=True)
    parser.add_argument("--metric", choices=sorted(DERIVED_METRICS), required=True)
    parser.add_argument("--upload-id", action="append", default=[])
    parser.add_argument("--report-dir", type=Path)
    args = parser.parse_args()
    path = ROOT / "data/processed/reports.json"
    store = (FinancialStore.from_uploads(path, args.upload_id, ROOT / "data/uploads")
             if args.upload_id else FinancialStore(path))
    result = validate_derived(store, store.derive(args.company, args.metric, args.fiscal_year))
    description = f"{result.company} FY{result.fiscal_year} {result.metric}: {result.value} {result.unit}."
    output = {"answer": TaskAnswer(status="complete", answer=description, metrics=[], calculations=[result],
              periods_aligned=None, evidence_ids=result.evidence_ids).model_dump(mode="json")}
    validation = validate_answer(store, output)
    artifacts = (build_analysis_report(store, description, output, args.report_dir) if args.report_dir else {})
    print(json.dumps({"calculation": result.model_dump(mode="json"), "validation": validation,
                      "artifacts": artifacts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
