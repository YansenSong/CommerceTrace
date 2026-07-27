import json
from typing import Any

import httpx

from commerce_trace.contracts import LlmMessage, ToolSchema
from commerce_trace.agent.llm import OpenAICompatibleLlm


async def test_deepseek_request_disables_thinking_and_parses_tool_call() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "run_sql",
                                        "arguments": json.dumps(
                                            {
                                                "sql": "SELECT 1",
                                                "purpose": "测试",
                                                "expected_columns": ["value"],
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    llm = OpenAICompatibleLlm(
        base_url="https://api.deepseek.com",
        api_key="test-key",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )
    response = await llm.complete(
        messages=[LlmMessage(role="user", content="销售额是多少？")],
        tools=[
            ToolSchema(
                name="run_sql",
                description="执行只读 SQL",
                parameters={"type": "object", "properties": {}},
            )
        ],
        system_prompt="只使用工具。",
    )

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "deepseek-v4-flash"
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["tool_choice"] == "auto"
    assert response.tool_calls[0].name == "run_sql"
    assert response.tool_calls[0].arguments["sql"] == "SELECT 1"
    assert response.usage == {"prompt_tokens": 12, "completion_tokens": 7}
