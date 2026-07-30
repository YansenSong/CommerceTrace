from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ...models import Chart, QueryTrace
from ...persistence import SQLiteSqlExecutor
from ..sql_safety import SqlSafetyPolicy


@dataclass
class RunArtifacts:
    queries: list[QueryTrace] = field(default_factory=list)
    charts: list[Chart] = field(default_factory=list)
    rows_by_query: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(3))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class AgentContext:
    artifacts: RunArtifacts
    executor: SQLiteSqlExecutor
    sql_policy: SqlSafetyPolicy
