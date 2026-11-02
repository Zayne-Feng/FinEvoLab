"""Hash local inputs and verify published artifacts against immutable Git records."""

from functools import lru_cache
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


@lru_cache(maxsize=128)
def _digest(path: str, modified: int, size: int) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_digest(path: Path) -> str:
    stat = path.stat()
    return _digest(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def same_json(left, right):
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def git_bytes(root, reference):
    path = PurePosixPath(reference["path"])
    if (not re.fullmatch(r"[0-9a-f]{40}", reference["commit"])
            or path.is_absolute() or ".." in path.parts or "\\" in str(path)):
        raise ValueError("Invalid original artifact reference")
    raw = subprocess.check_output(["git", "show", f"{reference['commit']}:{path}"], cwd=root)
    if digest(raw) != reference["sha256"]:
        raise ValueError("The original Git artifact hash differs")
    return raw


def portable_path(value, directories):
    normalized = value.replace("\\", "/")
    if not re.match(r"^[A-Za-z]:/", normalized):
        return value
    for directory in directories:
        marker = "/" + directory + "/"
        if marker in normalized:
            suffix = directory + "/" + normalized.split(marker, 1)[1]
            if ".." not in PurePosixPath(suffix).parts:
                return suffix
    raise ValueError("An absolute path is outside the published artifact directories")


def source_paths(value):
    if isinstance(value, dict):
        return {key: portable_path(item, ("data/raw", "data/uploads"))
                if key == "source_path" and isinstance(item, str) else source_paths(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [source_paths(item) for item in value]
    return value


def run_paths(value, key):
    result = dict(value)
    original = value[key]
    result[key] = {portable_path(name, ("project_notes/experiments",)): item
                   for name, item in original.items()}
    if len(result[key]) != len(original):
        raise ValueError("Published run paths collide")
    return result


def verify_manifest(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    current = json.loads(path.read_bytes())
    publication = current["publication"]
    if publication["source"]["path"] != path.relative_to(root).as_posix():
        raise ValueError("The original manifest path differs")
    original = json.loads(git_bytes(root, publication["source"]))
    encodings = (json.dumps(original["config"], sort_keys=True).encode(),
                 json.dumps(original["config"], sort_keys=True, separators=(",", ":")).encode())
    if original["config_sha256"] not in {digest(value) for value in encodings}:
        raise ValueError("The original configuration hash differs")
    actual = {key: item for key, item in current.items() if key != "publication"}
    if "published_config" in actual:
        if not same_json(actual.pop("published_config"), run_paths(original["config"], "runs")):
            raise ValueError("The published replay configuration differs")
        if "config" in actual:
            raise ValueError("A published replay contains two configurations")
        actual["config"] = original["config"]
    if not same_json(actual, original):
        raise ValueError("The historical manifest identity changed")
    artifacts = publication.get("artifacts", [])
    required_keys = {key for key in ("tasks_sha256", "selection_sha256", "source_sha256")
                     if key in original["config"]}
    if len(artifacts) != len(required_keys) or {item["config_key"] for item in artifacts} != required_keys:
        raise ValueError("The publication lacks an original input identity")
    for artifact in artifacts:
        if artifact["source"]["sha256"] != original["config"][artifact["config_key"]]:
            raise ValueError("An original artifact differs from the experiment input")
        raw = git_bytes(root, artifact["source"])
        relative = PurePosixPath(artifact["source"]["path"])
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ValueError("An artifact is outside the repository")
        published = target.read_bytes()
        if digest(published) != artifact["published_sha256"]:
            raise ValueError("The published artifact hash differs")
        if artifact["transform"] == "source_paths_v1":
            expected = [source_paths(json.loads(line)) for line in raw.splitlines()]
            observed = [json.loads(line) for line in published.splitlines()]
        elif artifact["transform"] == "validation_run_paths_v1":
            expected = run_paths(json.loads(raw), "validation_runs")
            observed = json.loads(published)
        else:
            raise ValueError("Unknown publication transformation")
        if not same_json(expected, observed):
            raise ValueError("The publication changed fields outside the allowed paths")
    return original["config"]


def verify_task_identity(root, manifest_path, tasks_path):
    document = json.loads(Path(manifest_path).read_bytes())
    actual = digest(Path(tasks_path).read_bytes())
    if "publication" not in document:
        if document["config"]["tasks_sha256"] != actual:
            raise ValueError("A blind review refers to a different task ledger")
        return
    verify_manifest(root, manifest_path)
    relative = Path(tasks_path).resolve().relative_to(Path(root).resolve()).as_posix()
    if not any(item["config_key"] == "tasks_sha256" and item["source"]["path"] == relative
               and item["published_sha256"] == actual
               for item in document["publication"].get("artifacts", [])):
        raise ValueError("The blind-review publication lacks its task ledger identity")
