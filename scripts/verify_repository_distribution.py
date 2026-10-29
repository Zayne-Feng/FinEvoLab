"""Validate publishable paths, frozen inputs, and legal notices."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finevolab.planner_benchmark import load_bundle
from scripts.check_readme_sync import check as check_readme_sync


def git(*arguments: str) -> bytes:
    return subprocess.check_output(["git", *arguments], cwd=ROOT)


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def publication_candidates() -> tuple[list[str], list[str]]:
    tracked = set(git("ls-files", "-z").decode().strip("\0").split("\0"))
    additional = set(git("ls-files", "--others", "--exclude-standard", "-z").decode().strip("\0").split("\0"))
    candidates = sorted(name for name in tracked | additional if name and (ROOT / name).is_file())
    ignored_tracked = [name for name in git("ls-files", "-ci", "--exclude-standard").decode().splitlines()
                       if (ROOT / name).exists()]
    if ignored_tracked:
        raise ValueError("Ignored paths remain tracked: " + ", ".join(ignored_tracked))
    if any(name.startswith("project_notes/") and name.endswith(".md") for name in candidates):
        raise ValueError("Local explanatory Markdown is selected for publication")
    if any(Path(name).name in {"config.ini", ".env", "id_rsa", "id_ed25519"} for name in candidates):
        raise ValueError("A private configuration path is selected for publication")
    if any(name.startswith(("data/raw/fmp/", "data/models/", "data/uploads/", "output/")) for name in candidates):
        raise ValueError("A local-only artifact is selected for publication")
    return candidates, ignored_tracked


def check() -> dict:
    candidates, ignored_tracked = publication_candidates()
    required = ["README.md", "README_CN.md", "AGENTS.md", ".gitignore", ".dockerignore", ".gitattributes", ".gitmodules",
                "LICENSE", "NOTICE", "TRADEMARK_POLICY.md", "COPYRIGHT",
                "configs/readme_sync.json", "scripts/check_readme_sync.py",
                "configs/retrieval.json", "configs/reranker.json", "configs/planner_model.json",
                "scripts/prepare_reranker.py", "scripts/benchmark_retrieval.py",
                "scripts/benchmark_runtime_refinement.py", "tests/frontend/check_timing_browser.cjs",
                "data/evaluation/retrieval_refinement/bilingual_queries.json",
                "data/evaluation/retrieval_refinement/runtime_cases.json",
                "project_notes/run_logs/runtime_refinement_checks.json", "configs/planner_formal_seed17.json",
                "configs/planner_formal_seed29.json", "configs/planner_service.json",
                "environments/development.yml", "requirements/development.lock.txt",
                "requirements/planner_training.in", "requirements/planner_serving.in",
                "data/sources.json", "data/filing_sources.json", "data/processed/reports.json",
                "data/indexes/structured.json", "data/indexes/contextual/structured.json",
                "data/evaluation/scope_v3/manifest.json", "data/training/planner_formal/frozen_v1/manifest.json",
                "project_notes/evaluations/planner_formal_results.json",
                "project_notes/evaluations/planner_quantization.json"]
    missing = sorted(set(required) - set(candidates))
    if missing:
        raise ValueError("Required reproducibility files are missing: " + ", ".join(missing))
    readme_sync = check_readme_sync(ROOT)
    baseline = "2717499b8e30f242640af08c4ad9afd1113c2d45"
    legal = {}
    for name in ("LICENSE", "NOTICE", "TRADEMARK_POLICY.md"):
        if git("show", baseline + ":" + name) != (ROOT / name).read_bytes():
            raise ValueError("An upstream legal file changed: " + name)
        legal[name] = digest(ROOT / name)
    ast.parse((ROOT / "scripts/finalize_planner_formal.py").read_text(encoding="utf-8"))
    counts = {}
    for split in ("development", "validation", "test"):
        cases, _ = load_bundle(ROOT / "data/training/planner_formal/frozen_v1", split)
        counts[split] = len(cases)
    raw_files = [name for name in candidates if name.startswith("data/raw/")]
    if not raw_files:
        raise ValueError("No raw source snapshots are selected")
    source_checks = 0
    for source_list in ("data/sources.json", "data/filing_sources.json", "data/acceptance_sources.json", "data/sec_sources.json"):
        for source in json.loads((ROOT / source_list).read_text(encoding="utf-8")):
            if source.get("status", "downloaded") != "downloaded":
                continue
            path = source["path"]
            if path not in candidates or digest(ROOT / path) != source["sha256"]:
                raise ValueError("A pinned source snapshot is missing or changed: " + path)
            source_checks += 1
    scope_path = ROOT / "data/evaluation/scope_v3"
    scope_manifest = json.loads((scope_path / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in scope_manifest["files"].items():
        if (scope_path / name).relative_to(ROOT).as_posix() not in candidates or digest(scope_path / name) != expected:
            raise ValueError("A frozen business input is missing or changed: " + name)
    links = []

    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", (ROOT / "README.md").read_text(encoding="utf-8-sig")):
        if target.startswith(("http://", "https://", "#")):
            continue
        path = target.split("#", 1)[0]
        if not (ROOT / path).exists() or (ROOT / path).is_file() and path not in candidates:
            raise ValueError("README links to an unpublished path: " + target)
        links.append(target)
    return {"status": "passed", "base_commit": git("rev-parse", "HEAD").decode().strip(),
            "readme_sync": readme_sync,
            "candidate_files": len(candidates), "required_files_checked": len(required),
            "ignored_tracked_files": ignored_tracked, "legal_sha256": legal,
            "planner_split_counts": counts,
            "raw_snapshot_files": len(raw_files), "source_snapshots_verified": source_checks,
            "business_frozen_files_verified": len(scope_manifest["files"]), "readme_local_links_checked": len(links),
            "functional_markdown": [name for name in candidates if name.endswith(".md")],
            "candidate_paths": candidates}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "project_notes/run_logs/repository_distribution.json")
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    result = check()
    if args.export is not None:
        target = args.export.resolve()
        output_root = (ROOT / "output").resolve()
        if target == output_root or not target.is_relative_to(output_root):
            raise ValueError("The export must be inside a dedicated output subdirectory")
        target.mkdir(parents=True, exist_ok=False)
        for name in result["candidate_paths"]:
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        result["export"] = {"path": str(target), "files": len(result["candidate_paths"]),
                            "model_cache_included": False, "private_markdown_included": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"candidate_paths", "functional_markdown"}}))
