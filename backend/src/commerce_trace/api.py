from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import Cookie, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .agent import Agent
from .config import Settings
from .models import ChatRequest
from .persistence import ConversationLedger, InMemoryStore
from .agent.testing import build_test_agent


def create_app(
    *,
    settings: Settings | None = None,
    store: ConversationLedger | None = None,
    agent: Agent | None = None,
    resources: list[Any] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if store is None:
        fallback_store = InMemoryStore()
        store = fallback_store
        agent = agent or build_test_agent(fallback_store)
    if agent is None:
        raise ValueError("agent is required when a custom store is provided")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        for resource in resources or []:
            await resource.open()
        try:
            yield
        finally:
            for resource in reversed(resources or []):
                await resource.close()

    app = FastAPI(
        title="CommerceTrace API",
        version="0.1.0",
        description="Evidence-backed Chinese ecommerce data agent",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.agent = agent
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Accept"],
    )

    @app.get("/health")
    async def health() -> dict[str, object]:
        dependencies = await store.health()
        ready = all(value == "ready" for value in dependencies.values())
        return {"status": "ready" if ready else "degraded", "dependencies": dependencies}

    @app.post("/api/chat")
    async def chat(
        body: ChatRequest,
        request: Request,
    ) -> StreamingResponse:
        cookie_value = request.cookies.get(settings.cookie_name)
        user_id = cookie_value or f"anon_{uuid4().hex}"
        conversation_id = body.conversation_id or f"conv_{uuid4().hex}"
        request_id = f"req_{uuid4().hex}"

        async def stream() -> AsyncGenerator[str, None]:
            try:
                async for event in agent.run(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    question=body.question,
                ):
                    yield event.to_sse()
            except PermissionError:
                return

        response = StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
        if cookie_value is None:
            response.set_cookie(
                settings.cookie_name,
                user_id,
                httponly=True,
                secure=settings.cookie_secure,
                samesite="lax",
                max_age=60 * 60 * 24 * 365,
            )
        return response

    @app.get("/api/conversations")
    async def conversations(
        user_id: str | None = Cookie(default=None, alias=settings.cookie_name),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        if user_id is None:
            return {"items": [], "limit": limit, "offset": offset}
        items = await store.list_conversations(user_id, limit, offset)
        return {"items": items, "limit": limit, "offset": offset}

    @app.get("/api/conversations/{conversation_id}")
    async def replay(
        conversation_id: str,
        user_id: str | None = Cookie(default=None, alias=settings.cookie_name),
    ) -> dict[str, object]:
        if user_id is None:
            raise HTTPException(status_code=404, detail="conversation_not_found")
        result = await store.replay_conversation(user_id, conversation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="conversation_not_found")
        return result

    return app


_settings = Settings()
if _settings.environment == "test":
    app = create_app(settings=_settings)
else:
    from .runtime import build_runtime

    _runtime = build_runtime(_settings)
    app = create_app(
        settings=_settings,
        store=_runtime.store,
        agent=_runtime.agent,
        resources=_runtime.resources,
    )
