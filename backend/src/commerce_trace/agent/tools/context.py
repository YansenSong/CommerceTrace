from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...models import Chart, QueryTrace
from ...query_engine import QueryEngine


@dataclass
class RunArtifacts:
    queries: list[QueryTrace] = field(default_factory=list)
    charts: list[Chart] = field(default_factory=list)
    rows_by_query: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class AgentContext:
    artifacts: RunArtifacts
    query_engine: QueryEngine
    few_shot: list[dict[str, Any]] = field(default_factory=list)
