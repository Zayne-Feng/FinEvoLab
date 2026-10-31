"""Discover and call the local financial MCP server."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def run(args):
    from finevolab.mcp_client import financial_session, call_financial_tool
    from finevolab.tracing import trace_scope
    with trace_scope("mcp.client", ROOT / "output/mcp/client_traces"):
        async with financial_session(ROOT, args.policy) as session:
            if args.tool is None:
                return (await session.list_tools()).model_dump(mode="json")
            request = (json.loads(args.request_file.read_text(encoding="utf-8")) if args.request_file else
                       json.loads(args.request) if args.request else
                       {key: value for key, value in {"company": args.company, "metric": args.metric,
                        "fiscal_year": args.fiscal_year}.items() if value is not None})
            return await call_financial_tool(session, args.tool, request)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--tool")
    payload = parser.add_mutually_exclusive_group()
    payload.add_argument("--request")
    payload.add_argument("--request-file", type=Path)
    parser.add_argument("--company")
    parser.add_argument("--metric")
    parser.add_argument("--fiscal-year", type=int)
    print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
