"""Verify live HTTP reports, a controlled repair, and completed-phase recovery."""

import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.config import ModelConfig
from finevolab.experiments import manifest, sha256
from finevolab.provider import async_client
from finevolab.runtime import write_json
from finevolab.workflow import run_question


class CorruptingClient:
    def __init__(self, settings):
        self.client = async_client(settings)
        self.calls = 0
        self.chat = self
        self.completions = self

    async def __aenter__(self):
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.client.__aexit__(*args)

    async def create(self, **kwargs):
        response = await self.client.chat.completions.create(**kwargs)
        self.calls += 1
        if self.calls == 1:
            response.choices[0].message.content = "{"
        return response


async def repair_probe(settings, candidate):
    task_id = uuid4().hex
    question = "Compare MSFT and AAPL FY2023 revenue and state the actual fiscal periods."
    client = CorruptingClient(settings)
    with patch("finevolab.grounded.async_client", return_value=client):
        state = await run_question(task_id, question, settings, candidate)
    resumed = await run_question(task_id, question, settings, candidate)
    output = state["phases"]["analyze"]["result"]
    assert output["repair"]["count"] == 1 and client.calls == 2
    assert output["answer"]["status"] == "complete"
    assert state["phases"] == resumed["phases"] and client.calls == 2
    return {"task_id": task_id, "status": state["status"], "usage": output["usage"],
            "repair_count": 1, "injection": "corrupted_first_real_response_after_usage_capture",
            "resumed_without_model_call": True}


def main():
    settings = ModelConfig.load()
    candidate = json.loads((ROOT / "prompts/benchmark.json").read_text())
    directory = ROOT / "output/pdf"
    directory.mkdir(parents=True, exist_ok=True)
    scenarios = [
        ("comparison", "Compare MSFT, AAPL and META FY2023 revenue, including actual fiscal dates.", "complete"),
        ("explanation", "Using MSFT's FY2024 annual report, explain revenue changes and their disclosed drivers. Cite source passages.", "complete"),
        ("missing", "Give GOOGL FY2019 annual revenue from the source snapshot.", "insufficient_data"),
    ]
    results = {}
    with httpx.Client(base_url="http://127.0.0.1:8003", timeout=240) as client:
        assert client.get("/finevolab").status_code == 200
        for name, question, expected in scenarios:
            response = client.post("/api/questions", json={"question": question})
            response.raise_for_status()
            state = response.json()
            output = state["phases"]["analyze"]["result"]
            assert state["status"] == "complete" and output["answer"]["status"] == expected
            report = client.get(f"/api/tasks/{state['task_id']}/report/pdf")
            report.raise_for_status()
            path = directory / ("closure_" + name + ".pdf")
            path.write_bytes(report.content)
            assert report.content.startswith(b"%PDF")
            results[name] = {"task_id": state["task_id"], "status": output["answer"]["status"],
                             "usage": output["usage"], "report": str(path.relative_to(ROOT)),
                             "report_sha256": sha256(path), "validation": state["phases"]["validate"]["result"]}
    results["repair_and_resume"] = asyncio.run(repair_probe(settings, candidate))
    write_json(ROOT / "project_notes/run_logs/closure_delivery.json", {
        **manifest(ROOT, "closure-delivery", {"model": settings.model, "thinking": settings.extra_body}),
        "status": "passed", "url": "http://127.0.0.1:8003/finevolab", "scenarios": results})
    print(json.dumps({"status": "passed", "scenarios": list(results)}))


if __name__ == "__main__":
    main()
