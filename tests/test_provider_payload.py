"""Verify SDK request options and local JSON validation without network access."""

import json
import unittest
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

from finevolab.config import ModelConfig
from scripts.verify_model import verify


class ProviderPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def check_stage(self, stage):
        bodies = []
        async def respond(request):
            body = json.loads(request.content)
            bodies.append(body)
            message = {"role": "assistant", "content": "OK"}
            finish = "stop"
            if stage == "structured":
                message["content"] = '{"operation":"addition","value":42}'
            elif stage == "tool":
                if any(item["role"] == "tool" for item in body["messages"]):
                    message["content"] = "42"
                else:
                    message.update(content=None, tool_calls=[{"id": "call_add", "type": "function",
                        "function": {"name": "add_integers", "arguments": '{"left":17,"right":25}'}}])
                    finish = "tool_calls"
            return httpx.Response(200, json={"id": "chatcmpl-local", "object": "chat.completion", "created": 0,
                "model": "deepseek-flash", "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
        def client(**kwargs):
            return AsyncOpenAI(api_key=kwargs["api_key"], base_url=kwargs["base_url"], max_retries=0,
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        settings = ModelConfig("deepseek-flash", "https://api.deepseek.com/", "unused")
        with patch("scripts.verify_model.AsyncOpenAI", side_effect=client):
            result = await verify(stage, settings)
        self.assertTrue(result["passed"])
        self.assertTrue(bodies)
        self.assertTrue(all(body["thinking"] == {"type": "disabled"} for body in bodies))
        return bodies

    async def test_minimal_request_explicitly_disables_thinking(self):
        await self.check_stage("minimal")

    async def test_tool_roundtrip_preserves_nonthinking_mode(self):
        bodies = await self.check_stage("tool")
        self.assertEqual(len(bodies), 2)

    async def test_structured_request_uses_json_object_and_local_schema(self):
        bodies = await self.check_stage("structured")
        self.assertEqual(bodies[0]["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
