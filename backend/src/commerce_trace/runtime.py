from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from langchain_deepseek import ChatDeepSeek
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .agent import AgentService
from .config import Config
from .persistence import BusinessDatabase, ConversationStore

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass
class Runtime:
    settings: Config
    store: ConversationStore
    business: BusinessDatabase
    agent: AgentService
    checkpointer: AsyncSqliteSaver

    @staticmethod
    def thread_id(user_id: str, conversation_id: str) -> str:
        return f"{user_id}:{conversation_id}"


@asynccontextmanager
async def build_runtime(settings: Config) -> AsyncIterator[Runtime]:
    resolved = settings.model_copy(
        update={
            "database_path": project_path(settings.database_path),
            "agent_state_path": project_path(settings.agent_state_path),
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
        yield Runtime(
            settings=resolved,
            store=store,
            business=BusinessDatabase(resolved.database_path),
            agent=AgentService(
                config=resolved,
                model=model,
                checkpointer=checkpointer,
            ),
            checkpointer=checkpointer,
        )
