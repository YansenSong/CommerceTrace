"""Prompt projections derived from the governed business semantic model."""

from __future__ import annotations

from typing import Any

from ...semantic import COMMERCE_SEMANTIC_MODEL

RULES: list[dict[str, str]] = [
    rule.model_dump(mode="json") for rule in COMMERCE_SEMANTIC_MODEL.rules
]
METRICS: list[dict[str, Any]] = [
    metric.model_dump(mode="json") for metric in COMMERCE_SEMANTIC_MODEL.metrics
]
