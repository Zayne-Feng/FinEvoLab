"""Download missing public report snapshots and verify immutable hashes."""

import hashlib
import json
from pathlib import Path
import tempfile

import requests

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sources = json.loads((ROOT / "data/sources.json").read_text())
    for source in sources:
        target = ROOT / source["path"]
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != source["sha256"]:
                raise ValueError("Existing snapshot differs from the source manifest")
            print(f"Verified snapshot: {target.name}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(source["url"], stream=True, timeout=(10, 60),
                          headers={"User-Agent": "FinEvoLab/0.1 public-report-research"}) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                temporary = Path(handle.name)
                try:
                    digest, size = hashlib.sha256(), 0
                    for block in response.iter_content(chunk_size=65536):
                        size += len(block)
                        if size > 64 * 1024 * 1024:
                            raise ValueError("Report exceeds the snapshot size limit")
                        digest.update(block)
                        handle.write(block)
                    handle.close()
                    if digest.hexdigest() != source["sha256"]:
                        raise ValueError("Downloaded report differs from the pinned snapshot")
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
        print(f"Downloaded snapshot: {target.name}")


if __name__ == "__main__":
    main()
