from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from uuid import uuid4

from fastapi import Cookie, FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_deepseek import ChatDeepSeek
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .agent import Agent
from .config import Config
from .models import (
    ChatResponse,
    ConversationCreate,
    ConversationList,
    ErrorBody,
    MessageCreate,
    MessageHistory,
)
from .persistence import ConversationStore

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.body = ErrorBody(code=code, message=message)


def create_app(settings: Config | None = None) -> FastAPI:
    settings = settings or Config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved = settings.model_copy(
            update={
                "database_path": _project_path(settings.database_path),
                "agent_state_path": _project_path(settings.agent_state_path),
            }
        )
        resolved.agent_state_path.parent.mkdir(parents=True, exist_ok=True)
        store = ConversationStore(resolved.agent_state_path)
        await store.setup()
        async with AsyncSqliteSaver.from_conn_string(
            str(resolved.agent_state_path)
        ) as checkpointer:
            await checkpointer.setup()
            if resolved.deepseek_api_key is None:
                raise ValueError("COMMERCE_TRACE_DEEPSEEK_API_KEY is required")
            model = ChatDeepSeek(
                model=resolved.deepseek_model,
                api_key=resolved.deepseek_api_key,
                base_url=resolved.deepseek_base_url,
                temperature=0,
                timeout=resolved.model_timeout_seconds,
                max_retries=1,
                streaming=False,
            )
            app.state.settings = resolved
            app.state.store = store
            app.state.agent = Agent(
                config=resolved,
                model=model,
                checkpointer=checkpointer,
            )
            app.state.checkpointer = checkpointer
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
        resolved = _settings(request)
        user_id, is_new = _user_id(request, resolved)
        conversation = await _store(request).create(user_id)
        if is_new:
            _set_user_cookie(response, resolved, user_id)
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
        items = await _store(request).list_conversations(
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
        messages = await _store(request).messages(user_id, conversation_id)
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
        store = _store(request)
        if not await store.owns(user_id, conversation_id):
            raise _not_found()
        thread_id = f"{user_id}:{conversation_id}"
        await store.add_message(
            conversation_id,
            role="user",
            content=body.message,
        )
        try:
            result = await _agent(request).invoke(
                conversation_id=conversation_id,
                thread_id=thread_id,
                message=body.message,
            )
        except Exception as exc:
            logger.exception("Agent request failed for conversation %s", conversation_id)
            raise ApiError(502, "agent_failed", "Agent 暂时无法完成分析") from exc
        await store.add_message(
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
        store = _store(request)
        if not await store.owns(user_id, conversation_id):
            raise _not_found()
        thread_id = f"{user_id}:{conversation_id}"
        await request.app.state.checkpointer.adelete_thread(thread_id)
        if not await store.delete(user_id, conversation_id):
            raise _not_found()
        return Response(status_code=204)

    return app


def _project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _settings(request: Request) -> Config:
    return cast(Config, request.app.state.settings)


def _store(request: Request) -> ConversationStore:
    return cast(ConversationStore, request.app.state.store)


def _agent(request: Request) -> Agent:
    return cast(Agent, request.app.state.agent)


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
