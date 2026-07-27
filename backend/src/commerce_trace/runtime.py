from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent import Agent
from .agent.context import ContextAssembler
from .agent.llm import LlmService, OpenAICompatibleLlm
from .agent.sql_safety import SqlSafetyPolicy
from .agent.tool import build_default_registry
from .config import Settings
from .persistence import SQLiteResources, SQLiteSqlExecutor, SQLiteStore


@dataclass
class Runtime:
    store: SQLiteStore
    agent: Agent
    resources: list[SQLiteResources]


@dataclass(frozen=True)
class FeatureConfiguration:
    include_knowledge: bool = True
    enable_sql_retries: bool = True


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def build_runtime(
    settings: Settings,
    features: FeatureConfiguration | None = None,
    llm: LlmService | None = None,
) -> Runtime:
    features = features or FeatureConfiguration()
    database_path = _project_path(settings.database_path)
    resources = SQLiteResources(database_path)
    store = SQLiteStore(resources)
    executor = SQLiteSqlExecutor(
        database_path,
        statement_timeout_ms=settings.statement_timeout_ms,
    )
    policy = SqlSafetyPolicy(
        max_rows=settings.max_result_rows,
        max_distinct_values=settings.max_distinct_values,
    )
    if llm is None:
        if settings.deepseek_api_key is None:
            raise ValueError("COMMERCE_TRACE_DEEPSEEK_API_KEY is required")
        llm = OpenAICompatibleLlm(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key.get_secret_value(),
            model=settings.deepseek_model,
        )
    agent = Agent(
        llm=llm,
        registry=build_default_registry(executor=executor, policy=policy),
        context_assembler=ContextAssembler(
            include_knowledge=features.include_knowledge,
        ),
        store=store,
        max_tool_iterations=settings.max_tool_iterations,
        max_business_sql_calls=settings.max_business_sql_calls,
        max_sql_retries_per_purpose=settings.max_sql_retries_per_purpose,
        enable_sql_retries=features.enable_sql_retries,
    )
    return Runtime(
        store=store,
        agent=agent,
        resources=[resources],
    )
