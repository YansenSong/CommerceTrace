from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Cookie, FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Config
from .models import (
    ChatResponse,
    ConversationCreate,
    ConversationList,
    ErrorBody,
    MessageCreate,
    MessageHistory,
)
from .runtime import Runtime, build_runtime

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.body = ErrorBody(code=code, message=message)


def create_app(settings: Config | None = None) -> FastAPI:
    settings = settings or Config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with build_runtime(settings) as runtime:
            app.state.runtime = runtime
            yield

    app = FastAPI(
        title="CommerceTrace API",
        version="1.0.0",
        description="LangChain-powered Chinese ecommerce data agent",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Accept"],
    )

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.body.model_dump(mode="json"),
        )

    @app.post(
        "/api/conversations",
        response_model=ConversationCreate,
        status_code=201,
    )
    async def create_conversation(request: Request, response: Response) -> ConversationCreate:
        runtime = _runtime(request)
        user_id, is_new = _user_id(request, runtime.settings)
        conversation = await runtime.store.create(user_id)
        if is_new:
            _set_user_cookie(response, runtime.settings, user_id)
        return conversation

    @app.get("/api/conversations", response_model=ConversationList)
    async def list_conversations(
        request: Request,
        user_id: str | None = Cookie(default=None, alias=settings.cookie_name),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> ConversationList:
        if user_id is None:
            return ConversationList(items=[], limit=limit, offset=offset)
        items = await _runtime(request).store.list_conversations(
            user_id,
            limit=limit,
            offset=offset,
        )
        return ConversationList(items=items, limit=limit, offset=offset)

    @app.get(
        "/api/conversations/{conversation_id}/messages",
        response_model=MessageHistory,
    )
    async def conversation_messages(
        conversation_id: str,
        request: Request,
        user_id: str | None = Cookie(default=None, alias=settings.cookie_name),
    ) -> MessageHistory:
        if user_id is None:
            raise _not_found()
        messages = await _runtime(request).store.messages(user_id, conversation_id)
        if messages is None:
            raise _not_found()
        return MessageHistory(conversation_id=conversation_id, messages=messages)

    @app.post(
        "/api/conversations/{conversation_id}/messages",
        response_model=ChatResponse,
    )
    async def send_message(
        conversation_id: str,
        body: MessageCreate,
        request: Request,
        user_id: str | None = Cookie(default=None, alias=settings.cookie_name),
    ) -> ChatResponse:
        if user_id is None:
            raise _not_found()
        runtime = _runtime(request)
        if not await runtime.store.owns(user_id, conversation_id):
            raise _not_found()
        thread_id = runtime.thread_id(user_id, conversation_id)
        await runtime.store.add_message(
            conversation_id,
            role="user",
            content=body.message,
        )
        try:
            result = await runtime.agent.invoke(
                conversation_id=conversation_id,
                thread_id=thread_id,
                message=body.message,
            )
        except Exception as exc:
            logger.exception("Agent request failed for conversation %s", conversation_id)
            raise ApiError(502, "agent_failed", "Agent 暂时无法完成分析") from exc
        await runtime.store.add_message(
            conversation_id,
            role="assistant",
            content=result.answer,
            queries=result.queries,
            charts=result.charts,
            usage=result.usage,
        )
        return result

    @app.delete("/api/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(
        conversation_id: str,
        request: Request,
        user_id: str | None = Cookie(default=None, alias=settings.cookie_name),
    ) -> Response:
        if user_id is None:
            raise _not_found()
        runtime = _runtime(request)
        if not await runtime.store.owns(user_id, conversation_id):
            raise _not_found()
        thread_id = runtime.thread_id(user_id, conversation_id)
        await runtime.checkpointer.adelete_thread(thread_id)
        if not await runtime.store.delete(user_id, conversation_id):
            raise _not_found()
        return Response(status_code=204)

    return app


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime  # type: ignore[no-any-return]


def _user_id(request: Request, settings: Config) -> tuple[str, bool]:
    current = request.cookies.get(settings.cookie_name)
    return (current, False) if current else (f"anon_{uuid4().hex}", True)


def _set_user_cookie(response: Response, settings: Config, user_id: str) -> None:
    response.set_cookie(
        settings.cookie_name,
        user_id,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )


def _not_found() -> ApiError:
    return ApiError(404, "conversation_not_found", "会话不存在")


app = create_app()
