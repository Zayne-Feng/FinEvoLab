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
    args = parser.parse_args()
    create_server(ROOT, args.policy).run(transport="stdio")


if __name__ == "__main__":
    main()
