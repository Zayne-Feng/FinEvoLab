"""Hash immutable inputs and cache digests by filesystem identity."""

from functools import lru_cache
import hashlib
from pathlib import Path


@lru_cache(maxsize=128)
def _digest(path: str, modified: int, size: int) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_digest(path: Path) -> str:
    stat = path.stat()
    return _digest(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
