"""从 prompt 目录中的目录数据组装大模型系统提示上下文。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field

from .prompt import METRICS, RULES, SCHEMA_CATALOG, schema_fingerprint


class AgentContext(BaseModel):
    """保存提交给大模型的 Schema、业务知识及其版本信息。"""

    schema_catalog: dict[str, Any]
    schema_fingerprint: str
    schema_version: str
    knowledge_version: str
    rules: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    degraded: bool = False

    def prompt_section(self) -> str:
        """将上下文压缩序列化为可拼接到系统提示词的 JSON。"""

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
    """把 Schema、业务规则和指标口径汇总为 Agent 上下文。

    默认加载仓库内提示词目录的全部内容；将 ``include_knowledge`` 设为
    ``False`` 时会跳过规则和指标，便于进行消融实验。
    """

    def __init__(self, *, include_knowledge: bool = True) -> None:
        """设置组装上下文时是否包含业务知识。"""

        self.include_knowledge = include_knowledge

    async def assemble(self) -> AgentContext:
        """复制提示词目录数据并生成相互一致的版本化上下文。"""

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
