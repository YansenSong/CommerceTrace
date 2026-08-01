from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...models import Chart, QueryTrace
from ..sql_safety import SqlSafetyPolicy


@dataclass
class RunArtifacts:
    queries: list[QueryTrace] = field(default_factory=list)
    charts: list[Chart] = field(default_factory=list)
    rows_by_query: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class AgentContext:
    artifacts: RunArtifacts
    database_path: Path
    statement_timeout_ms: int
    sql_policy: SqlSafetyPolicy
    few_shot: list[dict[str, Any]] = field(default_factory=list)
