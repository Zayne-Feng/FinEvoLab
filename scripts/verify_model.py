"""Verify a configured Chat Completions provider with bounded requests."""

from uuid import uuid4
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import re
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict
from finevolab.config import ModelConfig


class CalculationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    value: int


async def verify(stage: str, settings: ModelConfig) -> dict:
    async with AsyncOpenAI(
        api_key=settings.api_key, base_url=settings.base_url, timeout=45, max_retries=2
    ) as client:
        if stage == "minimal":
            response = await client.chat.completions.create(
                model=settings.model,
                messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                max_tokens=256,
                extra_body=settings.extra_body,
            )
            content = response.choices[0].message.content or ""
            return {
                "passed": content.strip() == "OK",
                "output": content,
                "finish_reason": response.choices[0].finish_reason,
                "usage": response.usage.model_dump() if response.usage else None,
            }

        from agents import (
            Agent, ModelSettings, OpenAIChatCompletionsModel, RunConfig,
            Runner, function_tool, set_tracing_disabled,
        )
        set_tracing_disabled(True)
        calls = []

        @function_tool
        def add_integers(left: int, right: int) -> int:
            """Add two integers exactly."""
            calls.append({"left": left, "right": right})
            return left + right

        model = OpenAIChatCompletionsModel(model=settings.model, openai_client=client)
        if stage == "tool":
            agent = Agent(
                name="ArithmeticVerifier", model=model, tools=[add_integers],
                instructions="Call add_integers to add 17 and 25 exactly once. Then reply 42.",
                model_settings=ModelSettings(max_tokens=1024, parallel_tool_calls=False,
                                             extra_body=settings.extra_body),
            )
        elif stage == "structured":
            agent = Agent(
                name="SchemaVerifier", model=model, output_type=CalculationResult,
                instructions='Return exactly this JSON object: {"operation":"addition","value":42}. No Markdown, prose, or code fences.',
                model_settings=ModelSettings(max_tokens=1024,
                                             extra_body={**settings.extra_body, "response_format": {"type": "json_object"}}),
            )
        else:
            from finrobot_equity.core.src.modules.equity_agents.tagline_agent import tagline_agent
            agent = tagline_agent.clone(
                model=model,
                instructions=tagline_agent.instructions +
                ' Return only a JSON object containing the field "tagline", without Markdown. '
                "Microsoft FY2024 revenue was USD 245122 million and FY2023 revenue was USD 211915 million.",
                model_settings=ModelSettings(max_tokens=1024, extra_body={
                    **settings.extra_body, "response_format": {"type": "json_object"}}),
            )
        result = await asyncio.wait_for(
            Runner.run(agent, "Perform the verification.", max_turns=3,
                       run_config=RunConfig(tracing_disabled=True)),
            timeout=100,
        )
        output = result.final_output
        passed = (
            calls == [{"left": 17, "right": 25}]
            and re.search(r"(?<!\d)42(?!\d)", str(output)) is not None
            if stage == "tool" else
            bool(getattr(output, "tagline", "").strip()) if stage == "upstream" else
            isinstance(output, CalculationResult)
            and output.operation == "addition" and output.value == 42
        )
        usage = result.context_wrapper.usage
        return {
            "passed": passed,
            "output": output.model_dump() if isinstance(output, BaseModel) else str(output),
            "tool_calls": calls,
            "requests": usage.requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("minimal", "tool", "structured", "upstream"), required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "finrobot_equity/core/config/config.ini")
    args = parser.parse_args()
    record = {
        "run_id": uuid4().hex[:12] + "-" + args.stage,
        "stage": args.stage,
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "versions": {name: importlib.metadata.version(name) for name in ("openai", "openai-agents", "pydantic")},
    }
    started = time.monotonic()
    try:
        settings = ModelConfig.load(args.config)
        record.update(model=settings.model, base_url=settings.base_url, request_options=settings.extra_body)
        if args.stage == "structured":
            record["structured_output_mode"] = "json_object_with_local_pydantic_validation"
        record["result"] = asyncio.run(verify(args.stage, settings))
    except Exception as error:
        record["result"] = {
            "passed": False, "error_type": type(error).__name__,
            "status_code": getattr(error, "status_code", None),
        }
        code = str(getattr(error, "code", ""))
        if code.isdecimal():
            record["result"]["provider_error_code"] = code
        if getattr(error, "status_code", None) == 429:
            record["result"]["provider_message"] = str(error).replace(settings.api_key, "[REDACTED]")[:400]
        if type(error).__name__ == "ModelBehaviorError":
            record["result"]["schema_error"] = str(error).replace(settings.api_key, "[REDACTED]")[:1600]
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    target = ROOT / "project_notes/run_logs/model_verification.json"
    records = json.loads(target.read_text(encoding="utf-8")) if target.exists() else []
    records.append(record)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    if not record["result"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
