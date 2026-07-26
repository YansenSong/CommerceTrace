from __future__ import annotations

from .agent import Agent
from .agent.tools import FakeSqlExecutor, build_default_registry
from .context import ContextAssembler, schema_fingerprint
from .llm import ScriptedLlm
from .memory import MemoryService
from .persistence import InMemoryStore


def build_test_agent(store: InMemoryStore) -> Agent:
    memory = MemoryService(
        store=store,
        schema_fingerprint=schema_fingerprint(),
        metric_versions={"revenue": "1"},
    )
    executor = FakeSqlExecutor(
        rows=[
            {"region": "华东", "revenue": 1200.0},
            {"region": "华南", "revenue": 900.0},
        ]
    )
    return Agent(
        llm=ScriptedLlm(),
        registry=build_default_registry(executor=executor, memory=memory),
        context_assembler=ContextAssembler(memory=memory),
        store=store,
        memory=memory,
    )
