"""Manage loopback Redis and Milvus Lite processes and import fixed vectors."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.retrieval_settings import load_settings
from finevolab.retrieval_cache import cache_client
from finevolab.vector_backends import import_vectors, milvus_client

DIRECTORY = ROOT / "output/retrieval_services"


def status():
    settings = load_settings(ROOT)
    result = {}
    try:
        result["redis"] = bool(cache_client(settings.redis).ping())
    except Exception:
        result["redis"] = False
    try:
        result["milvus"] = isinstance(milvus_client(settings.milvus.uri).list_collections(timeout=2), list)
    except Exception:
        result["milvus"] = False
    return result


def start():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    (DIRECTORY / "managed_redis.conf").write_text(
        'bind 127.0.0.1\nport 6379\nsave ""\nappendonly no\nmaxmemory 128mb\nmaxmemory-policy allkeys-lru\n', encoding="ascii")
    commands = {
        "redis": [shutil.which("redis-server") or "redis-server", "managed_redis.conf"],
        "milvus": [sys.executable, "-m", "milvus_lite.cmdline", "server", "--host", "127.0.0.1",
                   "--port", "19530", "--data-dir", str(DIRECTORY / "milvus"), "--max-workers", "4"]}
    current = status()
    for name, command in commands.items():
        if current[name]:
            continue
        with (DIRECTORY / (name + ".log")).open("ab") as log:
            process = subprocess.Popen(command, cwd=DIRECTORY if name == "redis" else ROOT, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        (DIRECTORY / (name + ".pid")).write_text(str(process.pid), encoding="ascii")
    for _ in range(12):
        result = status()
        if all(result.values()):
            return result
        time.sleep(0.5)
    raise RuntimeError("Retrieval services did not become ready; inspect local service logs")


def stop():
    import psutil
    for name in ("redis", "milvus"):
        path = DIRECTORY / (name + ".pid")
        if not path.exists():
            continue
        try:
            process = psutil.Process(int(path.read_text()))
            arguments = process.cmdline()
            expected = "redis-server" if name == "redis" else "milvus_lite.cmdline"
            owned_directory = (Path(process.cwd()).resolve() == DIRECTORY.resolve() if name == "redis" else
                               any(str(DIRECTORY) in item for item in arguments))
            if not any(expected in item for item in arguments) or not owned_directory:
                raise ValueError("The recorded PID does not identify a managed retrieval service")
            process.terminate()
            process.wait(timeout=15)
        except psutil.NoSuchProcess:
            pass
        path.unlink(missing_ok=True)
    return {"status": "stopped"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status", "import"])
    args = parser.parse_args()
    if args.action == "import":
        value = import_vectors(ROOT / "data/indexes/contextual/structured.json", load_settings(ROOT).milvus)
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        (DIRECTORY / "corpus.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    else:
        value = {"start": start, "stop": stop, "status": status}[args.action]()
    print(json.dumps(value))
