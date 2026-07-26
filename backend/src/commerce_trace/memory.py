from __future__ import annotations

import hashlib
import re
from contextlib import suppress
from datetime import datetime
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlglot import exp, parse_one

from .contracts import Evidence, utc_now


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"
    TRUSTED = "trusted"
    STALE = "stale"
    REJECTED = "rejected"


class MemoryRecord(BaseModel):
    memory_id: str = Field(default_factory=lambda: f"mem_{uuid4().hex[:16]}")
    question: str
    analysis_step: str
    normalized_sql: str
    tables_and_columns: list[str]
    schema_fingerprint: str
    metric_versions: dict[str, str]
    execution_time_ms: float = 0
    row_count: int = 0
    column_names: list[str] = Field(default_factory=list)
    limited_summary: str = ""
    result_hash: str
    status: MemoryStatus
    source: str = "runtime"
    created_at: datetime = Field(default_factory=utc_now)
    last_verified_at: datetime | None = None

    @property
    def dedupe_key(self) -> str:
        body = "|".join(
            [
                self.question.strip().lower(),
                self.analysis_step.strip().lower(),
                self.normalized_sql,
                self.schema_fingerprint,
                repr(sorted(self.metric_versions.items())),
            ]
        )
        return hashlib.sha256(body.encode()).hexdigest()


class MemorySearchResult(BaseModel):
    record: MemoryRecord
    score: float
    label: str


ALLOWED_MEMORY_TRANSITIONS: dict[MemoryStatus, set[MemoryStatus]] = {
    MemoryStatus.CANDIDATE: {
        MemoryStatus.TRUSTED,
        MemoryStatus.STALE,
        MemoryStatus.REJECTED,
    },
    MemoryStatus.TRUSTED: {MemoryStatus.STALE, MemoryStatus.REJECTED},
    MemoryStatus.STALE: set(),
    MemoryStatus.REJECTED: set(),
}


def transition_memory(record: MemoryRecord, target: MemoryStatus) -> MemoryRecord:
    if target is record.status:
        return record
    if target not in ALLOWED_MEMORY_TRANSITIONS[record.status]:
        raise ValueError(f"invalid memory transition: {record.status.value} -> {target.value}")
    record.status = target
    return record


class MemoryStore(Protocol):
    async def upsert_memory(self, record: MemoryRecord) -> MemoryRecord: ...

    async def list_memories(
        self, statuses: set[MemoryStatus] | None = None
    ) -> list[MemoryRecord]: ...


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).strip()


def _tokens(value: str) -> set[str]:
    compact = re.sub(r"\s+", "", value.lower())
    latin = set(re.findall(r"[a-z0-9_]+", compact))
    chinese = {compact[index : index + 2] for index in range(max(0, len(compact) - 1))}
    return latin | chinese


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0
    return len(a & b) / len(a | b)


class MemoryService:
    def __init__(
        self,
        store: MemoryStore,
        schema_fingerprint: str,
        metric_versions: dict[str, str],
        index: DerivedMemoryIndex | None = None,
        allow_candidates: bool = True,
    ) -> None:
        self.store = store
        self.schema_fingerprint = schema_fingerprint
        self.metric_versions = metric_versions
        self.index = index
        self.allow_candidates = allow_candidates

    async def record_candidate(self, question: str, evidence: Evidence) -> MemoryRecord:
        tables_and_columns: set[str] = set()
        try:
            expression = parse_one(evidence.sql, read="postgres")
            aliases = {table.alias_or_name: table.name for table in expression.find_all(exp.Table)}
            tables_and_columns.update(aliases.values())
            for column in expression.find_all(exp.Column):
                table = aliases.get(column.table, column.table)
                tables_and_columns.add(f"{table}.{column.name}" if table else column.name)
        except Exception:
            tables_and_columns = set()
        record = MemoryRecord(
            question=question,
            analysis_step=evidence.analysis_step,
            normalized_sql=normalize_sql(evidence.sql),
            tables_and_columns=sorted(tables_and_columns),
            schema_fingerprint=self.schema_fingerprint,
            metric_versions=self.metric_versions,
            row_count=evidence.row_count,
            execution_time_ms=evidence.execution_time_ms,
            column_names=evidence.columns,
            limited_summary=evidence.claim[:500],
            result_hash=evidence.result_hash,
            status=MemoryStatus.CANDIDATE,
        )
        saved = await self.store.upsert_memory(record)
        if self.index is not None:
            # PostgreSQL remains authoritative if the derived index is unavailable.
            with suppress(Exception):
                await self.index.rebuild(await self.store.list_memories())
        return saved

    async def search(self, query: str, *, limit_candidates: int = 2) -> list[MemorySearchResult]:
        records = await self.store.list_memories({MemoryStatus.TRUSTED, MemoryStatus.CANDIDATE})
        index_rank: dict[str, int] = {}
        if self.index is not None:
            ranked_ids = await self.index.search(query, limit=7)
            index_rank = {memory_id: index for index, memory_id in enumerate(ranked_ids)}

        def score(record: MemoryRecord) -> float:
            if record.memory_id in index_rank:
                return 1 / (1 + index_rank[record.memory_id])
            return _similarity(query, record.question)

        applicable = [
            record
            for record in records
            if record.schema_fingerprint == self.schema_fingerprint
            and all(
                self.metric_versions.get(key) == value
                for key, value in record.metric_versions.items()
            )
        ]
        trusted = sorted(
            (record for record in applicable if record.status is MemoryStatus.TRUSTED),
            key=score,
            reverse=True,
        )
        candidates = (
            sorted(
                (record for record in applicable if record.status is MemoryStatus.CANDIDATE),
                key=score,
                reverse=True,
            )[:limit_candidates]
            if self.allow_candidates
            else []
        )
        return [
            MemorySearchResult(
                record=record,
                score=score(record),
                label="trusted",
            )
            for record in trusted[:5]
        ] + [
            MemorySearchResult(
                record=record,
                score=score(record),
                label="unverified_candidate",
            )
            for record in candidates
        ]

    async def invalidate_versions(
        self, schema_fingerprint: str, metric_versions: dict[str, str]
    ) -> int:
        changed = 0
        for record in await self.store.list_memories(
            {MemoryStatus.CANDIDATE, MemoryStatus.TRUSTED}
        ):
            if record.schema_fingerprint != schema_fingerprint or any(
                metric_versions.get(key) != value for key, value in record.metric_versions.items()
            ):
                transition_memory(record, MemoryStatus.STALE)
                await self.store.upsert_memory(record)
                changed += 1
        return changed

    async def replay(
        self,
        record: MemoryRecord,
        *,
        actual_result_hash: str | None,
        expected_result_hash: str | None,
        safe: bool,
    ) -> MemoryRecord:
        if record.status not in {MemoryStatus.CANDIDATE, MemoryStatus.TRUSTED}:
            return record
        if not safe:
            transition_memory(record, MemoryStatus.REJECTED)
        elif expected_result_hash is None:
            return record
        elif actual_result_hash != expected_result_hash:
            transition_memory(record, MemoryStatus.REJECTED)
        else:
            transition_memory(record, MemoryStatus.TRUSTED)
            record.last_verified_at = utc_now()
        return await self.store.upsert_memory(record)


class DerivedMemoryIndex(Protocol):
    async def rebuild(self, records: list[MemoryRecord]) -> int: ...

    async def search(self, query: str, limit: int) -> list[str]: ...


class InMemoryDerivedIndex:
    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}

    async def rebuild(self, records: list[MemoryRecord]) -> int:
        self._records = {
            record.memory_id: record
            for record in records
            if record.status in {MemoryStatus.CANDIDATE, MemoryStatus.TRUSTED}
        }
        return len(self._records)

    async def search(self, query: str, limit: int) -> list[str]:
        ranked = sorted(
            self._records.values(),
            key=lambda item: _similarity(query, item.question),
            reverse=True,
        )
        return [item.memory_id for item in ranked[:limit]]


def memory_report(records: list[MemoryRecord]) -> dict[str, Any]:
    counts = {status.value: 0 for status in MemoryStatus}
    for record in records:
        counts[record.status.value] += 1
    return {"total": len(records), "by_status": counts}
