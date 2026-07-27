"""Assembles the LLM system prompt context from the prompt/ catalogue."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field

from .prompt import METRICS, RULES, SCHEMA_CATALOG, schema_fingerprint


class AgentContext(BaseModel):
    schema_catalog: dict[str, Any]
    schema_fingerprint: str
    schema_version: str
    knowledge_version: str
    rules: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    degraded: bool = False

    def prompt_section(self) -> str:
        return json.dumps(
            {
                "schema": self.schema_catalog,
                "schema_fingerprint": self.schema_fingerprint,
                "business_rules": self.rules,
                "metrics": self.metrics,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


class ContextAssembler:
    """Gathers schema, rules, and metrics into an ``AgentContext``.

    By default loads everything from the in-repo prompt catalogue.  Set
    ``include_knowledge=False`` to skip rules and metrics (used in ablation
    tests).
    """

    def __init__(self, *, include_knowledge: bool = True) -> None:
        self.include_knowledge = include_knowledge

    async def assemble(self) -> AgentContext:
        catalog = deepcopy(SCHEMA_CATALOG)
        if self.include_knowledge:
            rules = deepcopy(RULES)
            metrics = deepcopy(METRICS)
        else:
            rules, metrics = [], []
        return AgentContext(
            schema_catalog=catalog,
            schema_fingerprint=schema_fingerprint(catalog),
            schema_version=str(catalog["version"]),
            knowledge_version="1",
            rules=rules,
            metrics=metrics,
        )
