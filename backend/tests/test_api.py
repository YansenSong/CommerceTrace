from httpx import ASGITransport, AsyncClient

from commerce_trace.api import create_app
from commerce_trace.storage import InMemoryStore
from commerce_trace.testing import build_test_agent


async def test_chat_is_sse_and_cookie_restores_isolated_history() -> None:
    store = InMemoryStore()
    app = create_app(store=store, agent=build_test_agent(store))
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as first:
        response = await first.post("/api/chat", json={"question": "按地区展示销售额"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "commerce_trace_user" in response.cookies
        assert "event: evidence.created" in response.text
        assert "event: answer.completed" in response.text

        history = await first.get("/api/conversations")
        assert history.status_code == 200
        assert len(history.json()["items"]) == 1
        conversation_id = history.json()["items"][0]["conversation_id"]
        replay = await first.get(f"/api/conversations/{conversation_id}")
        assert replay.status_code == 200
        assert any(item["event"] == "evidence.created" for item in replay.json()["events"])
        assert replay.json()["tool_calls"]
        assert replay.json()["tool_results"]
        assert replay.json()["evidence"][0]["execution_time_ms"] >= 0

    async with AsyncClient(transport=transport, base_url="http://test") as second:
        forbidden = await second.get(f"/api/conversations/{conversation_id}")
        assert forbidden.status_code == 404
