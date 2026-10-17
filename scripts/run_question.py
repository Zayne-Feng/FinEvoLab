"""Run or resume a source-validated financial question workflow."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.config import ModelConfig
from finevolab.workflow import run_question


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--config", type=Path, default=ROOT / "finrobot_equity/core/config/config.ini")
    parser.add_argument("--prompt", type=Path, default=ROOT / "prompts/benchmark.json")
    parser.add_argument("--task-id", default=None)
    args = parser.parse_args()
    task_id = args.task_id or uuid4().hex
    try:
        state = asyncio.run(run_question(task_id, args.question, ModelConfig.load(args.config),
                                         json.loads(args.prompt.read_text(encoding="utf-8"))))
    except Exception as error:
        print(json.dumps({"task_id": task_id, "status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    print(json.dumps({"task_id": task_id, "status": state["status"], "report": state["phases"]["report"]["result"]}))


if __name__ == "__main__":
    main()
