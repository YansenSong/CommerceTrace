"""Schema projections derived from the governed business semantic model."""

from __future__ import annotations

from typing import Any

from ...semantic import COMMERCE_SEMANTIC_MODEL

SCHEMA_CATALOG: dict[str, Any] = COMMERCE_SEMANTIC_MODEL.schema_catalog()


def schema_fingerprint(catalog: dict[str, Any] | None = None) -> str:
    """Return the semantic fingerprint; custom catalogs are no longer accepted."""

    if catalog is not None and catalog != SCHEMA_CATALOG:
        raise ValueError("schema catalogs must be derived from the business semantic model")
    return COMMERCE_SEMANTIC_MODEL.fingerprint()


def compact_catalog() -> dict[str, Any]:
    """返回常驻提示词的紧凑表目录：表名 + 一句话描述 + 关系，不含列级细节。"""

    return COMMERCE_SEMANTIC_MODEL.compact_catalog()
