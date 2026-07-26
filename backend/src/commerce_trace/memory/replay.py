from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from ..agent.tools import SqlExecutor
from ..sql_safety import SqlSafetyError, SqlSafetyPolicy
from .core import MemoryService, MemoryStatus, normalize_sql, transition_memory


class GoldenCase(BaseModel):
    case_id: str
    question: str
    sql: str
    expected_result_hash: str | None
    schema_fingerprint: str
    metric_versions: dict[str, str] = Field(default_factory=dict)


class ReplayItem(BaseModel):
    memory_id: str
    golden_case_id: str | None = None
    before: MemoryStatus
    after: MemoryStatus
    outcome: str
    reason: str
    actual_result_hash: str | None = None
    expected_result_hash: str | None = None


class ReplayReport(BaseModel):
    run_id: str = Field(default_factory=lambda: f"memory_replay_{uuid4().hex[:12]}")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_fingerprint: str
    metric_versions: dict[str, str]
    counts: dict[str, int]
    items: list[ReplayItem]

    def markdown(self) -> str:
        lines = [
            "# CommerceTrace Memory Replay Report",
            "",
            f"- Run: `{self.run_id}`",
            f"- Schema fingerprint: `{self.schema_fingerprint}`",
            f"- Passed: {self.counts.get('passed', 0)}",
            f"- Rejected: {self.counts.get('rejected', 0)}",
            f"- Stale: {self.counts.get('stale', 0)}",
            f"- Skipped: {self.counts.get('skipped', 0)}",
            "",
            "| Memory | Golden case | Before | After | Outcome | Reason |",
            "|---|---|---|---|---|---|",
        ]
        lines.extend(
            "| {memory} | {golden} | {before} | {after} | {outcome} | {reason} |".format(
                memory=item.memory_id,
                golden=item.golden_case_id or "—",
                before=item.before.value,
                after=item.after.value,
                outcome=item.outcome,
                reason=item.reason.replace("|", "\\|"),
            )
            for item in self.items
        )
        return "\n".join(lines) + "\n"


def load_golden_cases(
    root: Path,
    *,
    schema_fingerprint: str,
) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    policy = SqlSafetyPolicy()
    for path in sorted((root / "golden_sql").glob("*.yaml")):
        payload: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        expected = payload.get("expected", {})
        validated = policy.validate(str(payload["sql"]))
        cases.append(
            GoldenCase(
                case_id=str(payload["id"]),
                question=str(payload["question"]),
                sql=validated.normalized_sql,
                expected_result_hash=(
                    str(expected["value"]) if expected.get("value") is not None else None
                ),
                schema_fingerprint=schema_fingerprint,
                metric_versions={
                    str(key): str(value)
                    for key, value in payload.get("metric_versions", {}).items()
                },
            )
        )
    return cases


def _result_hash(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def replay_memories(
    *,
    service: MemoryService,
    executor: SqlExecutor,
    policy: SqlSafetyPolicy,
    golden_cases: list[GoldenCase],
) -> ReplayReport:
    cases_by_sql = {normalize_sql(case.sql): case for case in golden_cases}
    items: list[ReplayItem] = []
    records = await service.store.list_memories({MemoryStatus.CANDIDATE, MemoryStatus.TRUSTED})
    for record in records:
        before = record.status
        if record.schema_fingerprint != service.schema_fingerprint or any(
            service.metric_versions.get(key) != value
            for key, value in record.metric_versions.items()
        ):
            transition_memory(record, MemoryStatus.STALE)
            saved = await service.store.upsert_memory(record)
            items.append(
                ReplayItem(
                    memory_id=record.memory_id,
                    before=before,
                    after=saved.status,
                    outcome="stale",
                    reason="schema_or_metric_version_mismatch",
                )
            )
            continue

        case = cases_by_sql.get(normalize_sql(record.normalized_sql))
        if case is None:
            items.append(
                ReplayItem(
                    memory_id=record.memory_id,
                    before=before,
                    after=record.status,
                    outcome="skipped",
                    reason="no_matching_golden_case",
                )
            )
            continue
        if case.expected_result_hash is None:
            items.append(
                ReplayItem(
                    memory_id=record.memory_id,
                    golden_case_id=case.case_id,
                    before=before,
                    after=record.status,
                    outcome="skipped",
                    reason="golden_expected_hash_missing",
                )
            )
            continue

        try:
            validated = policy.validate(record.normalized_sql)
        except SqlSafetyError as exc:
            saved = await service.replay(
                record,
                actual_result_hash=None,
                expected_result_hash=case.expected_result_hash,
                safe=False,
            )
            items.append(
                ReplayItem(
                    memory_id=record.memory_id,
                    golden_case_id=case.case_id,
                    before=before,
                    after=saved.status,
                    outcome="rejected",
                    reason=f"safety:{exc.code}",
                    expected_result_hash=case.expected_result_hash,
                )
            )
            continue

        try:
            rows = await executor.execute(validated.normalized_sql, validated.row_limit)
            actual = _result_hash(rows)
        except Exception:
            items.append(
                ReplayItem(
                    memory_id=record.memory_id,
                    golden_case_id=case.case_id,
                    before=before,
                    after=record.status,
                    outcome="skipped",
                    reason="execution_unavailable",
                    expected_result_hash=case.expected_result_hash,
                )
            )
            continue
        saved = await service.replay(
            record,
            actual_result_hash=actual,
            expected_result_hash=case.expected_result_hash,
            safe=True,
        )
        passed = saved.status is MemoryStatus.TRUSTED
        items.append(
            ReplayItem(
                memory_id=record.memory_id,
                golden_case_id=case.case_id,
                before=before,
                after=saved.status,
                outcome="passed" if passed else "rejected",
                reason="result_hash_match" if passed else "result_hash_mismatch",
                actual_result_hash=actual,
                expected_result_hash=case.expected_result_hash,
            )
        )

    if service.index is not None:
        await service.index.rebuild(await service.store.list_memories())
    counts = Counter(item.outcome for item in items)
    return ReplayReport(
        schema_fingerprint=service.schema_fingerprint,
        metric_versions=service.metric_versions,
        counts=dict(counts),
        items=items,
    )


def write_replay_report(report: ReplayReport, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{report.run_id}.json"
    markdown_path = directory / f"{report.run_id}.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(report.markdown(), encoding="utf-8")
    return json_path, markdown_path
