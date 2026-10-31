"""Serve policy-scoped financial tools over the local MCP stdio transport."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from finevolab.mcp_server import create_server
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=ROOT / "configs/mcp.json")
    parser.add_argument("--preload-retrieval", action="store_true")
    args = parser.parse_args()
    from finevolab.tracing import flush_exports
    try:
        create_server(ROOT, args.policy, args.preload_retrieval).run(transport="stdio")
    finally:
        flush_exports()


if __name__ == "__main__":
    main()
