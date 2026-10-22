"""Export an allowlisted, hash-verified planner workspace without credentials."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.planner_benchmark import load_bundle


def package(bundle: Path, output: Path):
    load_bundle(bundle, "development")
    if not bundle.resolve().is_relative_to(ROOT):
        raise ValueError("A workspace-local dataset is required")
    paths = list((ROOT / "finevolab").glob("*.py"))
    paths += [ROOT / name for name in ("scripts/planner_benchmark.py", "scripts/train_planner.py", "scripts/preflight_planner.py",
        "scripts/estimate_paired_uncertainty.py", "configs/planner_training.json", "configs/company_aliases.json",
        "prompts/benchmark.json", "requirements/planner_runtime.in", "requirements/planner_training.in",
        "configs/planner_model.json")]
    paths += list((ROOT / "configs").glob("planner_formal_*.json"))
    paths += [ROOT / name for name in ("scripts/serve_planner.py", "scripts/run_quantization_comparison.py",
                                      "configs/planner_service.json", "requirements/planner_serving.in") if (ROOT / name).exists()]
    frozen = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    paths += [bundle / name for name in (*frozen["files"], "manifest.json")]
    files = {p.resolve().relative_to(ROOT).as_posix(): sha256(p) for p in paths}
    if output.exists():
        with ZipFile(output) as archive:
            source = json.loads(archive.read("source_revision.json"))
            if source["files"] != files or set(archive.namelist()) != {*files, "source_revision.json"}:
                raise ValueError("The archive uses an older source snapshot; choose a new archive path")
            if archive.testzip() is not None or any(hashlib.sha256(archive.read(name)).hexdigest() != digest
                                                   for name, digest in files.items()):
                raise ValueError("The existing archive failed verification")
        return {"archive": str(output), "sha256": sha256(output), "files": len(files) + 1,
                "credentials_included": False, "financial_raw_files_included": False, "reused": True}
    revision = {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "worktree_status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).splitlines(),
        "files": files}
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.write(ROOT / name, name)
        archive.writestr("source_revision.json", json.dumps(revision, ensure_ascii=False, indent=2))
    with ZipFile(output) as archive:
        if archive.testzip() is not None or any(hashlib.sha256(archive.read(name)).hexdigest() != digest for name, digest in files.items()):
            raise ValueError("The exported archive failed verification")
    return {"archive": str(output), "sha256": sha256(output), "files": len(files) + 1,
            "credentials_included": False, "financial_raw_files_included": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.bundle, args.output)))
