from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from ..context import KnowledgeLoader, schema_fingerprint
from ..persistence import MemoryRepository
from ..sql_safety import SqlSafetyPolicy
from .core import MemoryRecord, MemoryStatus


def load_golden_records(root: Path) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    policy = SqlSafetyPolicy()
    for path in sorted((root / "golden_sql").glob("*.yaml")):
        item: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        if item.get("expected", {}).get("type") != "result_hash":
            raise ValueError(f"{path}: expected.type must be result_hash")
        validated = policy.validate(str(item["sql"]))
        records.append(
            MemoryRecord(
                memory_id=f"golden_{item['id']}",
                question=item["question"],
                analysis_step=item["analysis_step"],
                normalized_sql=validated.normalized_sql,
                tables_and_columns=[],
                schema_fingerprint=schema_fingerprint(),
                metric_versions={
                    str(key): str(value) for key, value in item.get("metric_versions", {}).items()
                },
                limited_summary=f"预置 Golden SQL：{item['id']}",
                result_hash=str(item.get("expected", {}).get("value") or f"golden:{item['id']}"),
                status=MemoryStatus.TRUSTED,
                source=f"knowledge:{path.name}",
            )
        )
    return records


async def bootstrap_memory(
    store: MemoryRepository, root: Path
) -> list[MemoryRecord]:
    records = load_golden_records(root)
    return [await store.upsert_memory(record) for record in records]


def load_business_documents(root: Path) -> list[dict[str, str]]:
    loader = KnowledgeLoader(root)
    rules, metrics, version = loader.load()
    documents = [
        {
            "id": f"rule:{item['id']}",
            "kind": "rule",
            "version": version,
            "content": f"{item['id']}\n{item['text']}",
        }
        for item in rules
    ]
    documents.extend(
        {
            "id": f"metric:{item['id']}",
            "kind": "metric",
            "version": str(item.get("version", version)),
            "content": yaml.safe_dump(item, allow_unicode=True, sort_keys=True),
        }
        for item in metrics
    )
    return documents
