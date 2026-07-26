from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent import Agent
from .agent.tools import build_default_registry
from .config import Settings
from .context import ContextAssembler, KnowledgeLoader, schema_fingerprint
from .llm import LlmService, OpenAICompatibleLlm
from .memory import ChromaMemoryIndex, MemoryService
from .persistence import SQLiteResources, SQLiteSchemaProvider, SQLiteSqlExecutor, SQLiteStore
from .sql_safety import SqlSafetyPolicy


@dataclass
class Runtime:
    store: SQLiteStore
    agent: Agent
    resources: list[SQLiteResources]


@dataclass(frozen=True)
class FeatureConfiguration:
    include_knowledge: bool = True
    include_memory: bool = True
    include_candidates: bool = True
    enable_sql_retries: bool = True
    record_candidates: bool = True


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
    try:
        import chromadb  # type: ignore[import-not-found]  # noqa: F401

        derived_index = ChromaMemoryIndex(
            _project_path(settings.chroma_path), settings.embedding_model
        )
    except ImportError:
        derived_index = None
    store = SQLiteStore(resources, index_health=derived_index)
    memory = MemoryService(
        store=store,
        schema_fingerprint=schema_fingerprint(),
        metric_versions={"revenue": "1", "refund_rate": "1", "aov": "1"},
        index=derived_index,
        allow_candidates=features.include_candidates,
    )
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
        registry=build_default_registry(executor=executor, memory=memory, policy=policy),
        context_assembler=ContextAssembler(
            memory=memory,
            knowledge_loader=KnowledgeLoader(_project_path(settings.knowledge_path)),
            include_knowledge=features.include_knowledge,
            include_memory=features.include_memory,
            schema_provider=SQLiteSchemaProvider(resources),
        ),
        store=store,
        memory=memory,
        max_tool_iterations=settings.max_tool_iterations,
        max_business_sql_calls=settings.max_business_sql_calls,
        max_sql_retries_per_purpose=settings.max_sql_retries_per_purpose,
        enable_sql_retries=features.enable_sql_retries,
        record_candidates=features.record_candidates,
    )
    return Runtime(
        store=store,
        agent=agent,
        resources=[resources],
    )
