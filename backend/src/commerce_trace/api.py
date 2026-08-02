from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import Cookie, FastAPI, Header, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_deepseek import ChatDeepSeek
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .agent import Agent
from .analysis import (
    AnalysisAgentFactory,
    AnalysisCoordinator,
    AnalysisRun,
    AnalysisRunError,
)
from .config import Config
from .memory import KnowledgeEntry, MemoryStore
from .models import (
    ChatResponse,
    ConversationCreate,
    ConversationList,
    ErrorBody,
    KnowledgeConfirm,
    MessageCreate,
    MessageHistory,
)
from .persistence import AnalysisRunStore, ConversationStore

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
AgentFactory = Callable[[Config, BaseCheckpointSaver[Any], MemoryStore], Agent]


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.body = ErrorBody(code=code, message=message)


def create_app(
    config: Config | None = None,
    *,
    analysis_agent_factory: AnalysisAgentFactory | None = None,
    agent_factory: AgentFactory | None = None,
) -> FastAPI:
    config = config or Config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configs = config.model_copy(
            update={
                "database_path": _project_path(config.database_path),
                "agent_state_path": _project_path(config.agent_state_path),
                "knowledge_dir": _project_path(config.knowledge_dir),
            }
        )
        configs.agent_state_path.parent.mkdir(parents=True, exist_ok=True)
        store = ConversationStore(configs.agent_state_path)
        await store.setup()
        analysis_store = AnalysisRunStore(configs.agent_state_path)
        await analysis_store.setup()
        memory = MemoryStore(configs.knowledge_dir)
        memory.setup()
        async with AsyncSqliteSaver.from_conn_string(
            str(configs.agent_state_path)
        ) as checkpointer:
            await checkpointer.setup()
            if configs.model_api_key is None:
                raise ValueError("COMMERCE_TRACE_MODEL_API_KEY is required")

            app.state.config = configs
            app.state.store = store
            app.state.memory = memory

            if agent_factory is not None:
                agent = agent_factory(configs, checkpointer, memory)
            else:
                model = ChatDeepSeek(
                    model=configs.model,
                    api_key=configs.model_api_key,
                    base_url=configs.model_base_url,
                    extra_body={"thinking": {"type": "disabled"}},
                    temperature=0,
                    timeout=configs.model_timeout_seconds,
                    max_retries=1,
                    streaming=False,
                )
                agent = Agent(
                    config=configs,
                    model=model,
                    checkpointer=checkpointer,
                    memory=memory,
                )
            app.state.agent = agent
            app.state.checkpointer = checkpointer
            app.state.analysis_store = analysis_store

            async def save_finished_run(run: AnalysisRun) -> None:
                if run.answer is None:
                    return
                await store.add_message(
                    run.conversation_id,
                    role="assistant",
                    content=run.answer,
                    queries=run.queries,
                    charts=run.charts,
                    usage=run.usage,
                )

            factory = analysis_agent_factory or agent.analysis_session
            coordinator = AnalysisCoordinator(
                store=analysis_store,
                agent_factory=factory,
                on_finished=save_finished_run,
            )
            app.state.analysis_coordinator = coordinator
            try:
                yield
            finally:
                await coordinator.shutdown()

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
        configs = _config(request)
        user_id, is_new = _user_id(request, configs)
        conversation = await _store(request).create(user_id)
        if is_new:
            _set_user_cookie(response, configs, user_id)
        return conversation

    @app.get("/api/conversations", response_model=ConversationList)
    async def list_conversations(
        request: Request,
        user_id: str | None = Cookie(default=None, alias=config.cookie_name),
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
        user_id: str | None = Cookie(default=None, alias=config.cookie_name),
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
        user_id: str | None = Cookie(default=None, alias=config.cookie_name),
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

    @app.post(
        "/api/conversations/{conversation_id}/analysis-runs",
        response_model=AnalysisRun,
        status_code=202,
    )
    async def create_analysis_run(
        conversation_id: str,
        body: MessageCreate,
        request: Request,
        user_id: str | None = Cookie(default=None, alias=config.cookie_name),
    ) -> AnalysisRun:
        if user_id is None:
            raise _not_found()
        store = _store(request)
        if not await store.owns(user_id, conversation_id):
            raise _not_found()
        await store.add_message(conversation_id, role="user", content=body.message)
        return await _analysis_coordinator(request).start(
            conversation_id=conversation_id,
            user_id=user_id,
            question=body.message,
            thread_id=f"{user_id}:{conversation_id}",
        )

    @app.get("/api/analysis-runs/{run_id}", response_model=AnalysisRun)
    async def get_analysis_run(
        run_id: str,
        request: Request,
        user_id: str | None = Cookie(default=None, alias=config.cookie_name),
    ) -> AnalysisRun:
        if user_id is None:
            raise _not_found()
        run = await _analysis_store(request).get(user_id, run_id)
        if run is None:
            raise _not_found()
        return run

    @app.post(
        "/api/analysis-runs/{run_id}/retry",
        response_model=AnalysisRun,
        status_code=202,
    )
    async def retry_analysis_run(
        run_id: str,
        request: Request,
        user_id: str | None = Cookie(default=None, alias=config.cookie_name),
    ) -> AnalysisRun:
        if user_id is None:
            raise _not_found()
        try:
            run = await _analysis_coordinator(request).retry(
                user_id=user_id,
                run_id=run_id,
            )
        except AnalysisRunError as exc:
            raise ApiError(409, str(exc), "该分析运行当前不能重试") from exc
        if run is None:
            raise _not_found()
        return run

    @app.get(
        "/api/conversations/{conversation_id}/analysis-runs/latest",
        response_model=AnalysisRun,
    )
    async def latest_analysis_run(
        conversation_id: str,
        request: Request,
        user_id: str | None = Cookie(default=None, alias=config.cookie_name),
    ) -> AnalysisRun:
        if user_id is None:
            raise _not_found()
        run = await _analysis_store(request).latest_for_conversation(
            user_id,
            conversation_id,
        )
        if run is None:
            raise _not_found()
        return run

    @app.get("/api/analysis-runs/{run_id}/events")
    async def analysis_run_events(
        run_id: str,
        request: Request,
        user_id: str | None = Cookie(default=None, alias=config.cookie_name),
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        if user_id is None or await _analysis_store(request).get(user_id, run_id) is None:
            raise _not_found()
        try:
            cursor = max(after, int(last_event_id or 0))
        except ValueError:
            cursor = after

        async def stream() -> AsyncIterator[str]:
            nonlocal cursor
            while True:
                events = await _analysis_store(request).events_after(
                    user_id,
                    run_id,
                    after=cursor,
                )
                if events is None:
                    return
                for event in events:
                    cursor = event.sequence
                    payload = json.dumps(event.data, ensure_ascii=False, default=str)
                    yield (
                        f"id: {event.sequence}\n"
                        f"event: {event.event_type}\n"
                        f"data: {payload}\n\n"
                    )
                run = await _analysis_store(request).get(user_id, run_id)
                if run is None or run.status in {"completed", "partial", "failed"}:
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.delete("/api/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(
        conversation_id: str,
        request: Request,
        user_id: str | None = Cookie(default=None, alias=config.cookie_name),
    ) -> Response:
        if user_id is None:
            raise _not_found()
        store = _store(request)
        if not await store.owns(user_id, conversation_id):
            raise _not_found()
        thread_id = f"{user_id}:{conversation_id}"
        await _analysis_coordinator(request).cancel_conversation(conversation_id)
        await request.app.state.checkpointer.adelete_thread(thread_id)
        await _analysis_store(request).delete_for_conversation(user_id, conversation_id)
        if not await store.delete(user_id, conversation_id):
            raise _not_found()
        return Response(status_code=204)

    @app.post(
        "/api/knowledge",
        response_model=KnowledgeEntry,
        status_code=201,
    )
    async def confirm_knowledge(body: KnowledgeConfirm, request: Request) -> KnowledgeEntry:
        return _memory(request).save(body.question, body.sqls, note=body.note)

    @app.get("/api/knowledge", response_model=list[KnowledgeEntry])
    async def list_knowledge(request: Request) -> list[KnowledgeEntry]:
        return _memory(request).list_entries()

    @app.delete("/api/knowledge/{slug}", status_code=204)
    async def delete_knowledge(slug: str, request: Request) -> Response:
        try:
            deleted = _memory(request).delete(slug)
        except ValueError:
            raise _not_found() from None
        if not deleted:
            raise _not_found()
        return Response(status_code=204)

    return app


def _project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _config(request: Request) -> Config:
    return cast(Config, request.app.state.config)


def _store(request: Request) -> ConversationStore:
    return cast(ConversationStore, request.app.state.store)


def _memory(request: Request) -> MemoryStore:
    return cast(MemoryStore, request.app.state.memory)


def _analysis_store(request: Request) -> AnalysisRunStore:
    return cast(AnalysisRunStore, request.app.state.analysis_store)


def _analysis_coordinator(request: Request) -> AnalysisCoordinator:
    return cast(AnalysisCoordinator, request.app.state.analysis_coordinator)


def _agent(request: Request) -> Agent:
    return cast(Agent, request.app.state.agent)


def _user_id(request: Request, config: Config) -> tuple[str, bool]:
    current = request.cookies.get(config.cookie_name)
    return (current, False) if current else (f"anon_{uuid4().hex}", True)


def _set_user_cookie(response: Response, config: Config, user_id: str) -> None:
    response.set_cookie(
        config.cookie_name,
        user_id,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )


def _not_found() -> ApiError:
    return ApiError(404, "conversation_not_found", "会话不存在")


app = create_app()
