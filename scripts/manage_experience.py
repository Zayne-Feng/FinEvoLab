"""Create local invitations or serve the authenticated analysis application."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("invite", "serve"))
    parser.add_argument("--user", default="owner")
    parser.add_argument("--config", type=Path, default=ROOT / "output/experience/access.json")
    parser.add_argument("--port", type=int, default=8085)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--public-url")
    args = parser.parse_args()
    if args.action == "invite":
        policy = json.loads(args.config.read_text(encoding="utf-8")) if args.config.exists() else {
            "enabled": True, "secure_cookie": False, "daily_tasks": 20, "concurrent_tasks": 1,
            "budget": {"max_model_calls": 6, "max_tool_calls": 10, "max_total_tokens": 20000,
                       "max_context_chars": 60000, "timeout_seconds": 240}, "users": {}}
        if args.user in policy["users"]:
            raise ValueError("The invitation already exists")
        token = secrets.token_urlsafe(32)
        policy["users"][args.user] = {"invite_sha256": hashlib.sha256(token.encode()).hexdigest(), "daily_tasks": 5}
        args.config.parent.mkdir(parents=True, exist_ok=True)
        args.config.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        invitation = args.config.parent / ("invite-" + hashlib.sha256(args.user.encode()).hexdigest()[:12] + ".txt")
        invitation.write_text(token + "\n", encoding="utf-8")
        print(json.dumps({"status": "created", "user": args.user, "invitation_file": str(invitation)}))
    else:
        os.environ["FINEVOLAB_ACCESS_CONFIG"] = str(args.config.resolve())
        os.environ.setdefault("FINEVOLAB_TRACE_MODE", "batch")
        import uvicorn
        from finevolab.experience import create_app
        uvicorn.run(create_app(public_url=args.public_url), host=args.host, port=args.port, access_log=False,
                    proxy_headers=True, forwarded_allow_ips="127.0.0.1,::1")


if __name__ == "__main__":
    main()
