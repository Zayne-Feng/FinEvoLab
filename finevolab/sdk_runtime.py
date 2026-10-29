"""Execute bounded JSON model steps through the Agents SDK."""

import json

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, RunConfig, RunHooks, Runner


def json_messages(schema, instruction: str, payload: dict) -> list[dict]:
    return [{"role": "system", "content": instruction + "\nReturn a JSON data object. Schema: " + json.dumps(schema.model_json_schema())},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


class ResponseHooks(RunHooks):
    def __init__(self, callback):
        self.callback = callback

    async def on_llm_end(self, context, agent, response):
        self.callback(response)


async def generate_json(client, settings, messages: list[dict], on_response, max_tokens: int = 1600,
                        agent_name: str = "FinancialAnalyst") -> str:
    model = OpenAIChatCompletionsModel(model=settings.model, openai_client=client)
    extra_body = {**settings.extra_body, "response_format": {"type": "json_object"}}
    agent = Agent(name=agent_name, instructions=messages[0]["content"], model=model,
                  model_settings=ModelSettings(temperature=0, max_tokens=max_tokens, extra_body=extra_body))
    result = await Runner.run(agent, input=messages[1:], max_turns=1, hooks=ResponseHooks(on_response),
        run_config=RunConfig(model=model, tracing_disabled=True, trace_include_sensitive_data=False))
    return str(result.final_output or "")
