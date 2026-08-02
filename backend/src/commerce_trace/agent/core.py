from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, Field

from ..analysis import (
    AnalysisEvidence,
    AnalysisStep,
    AnalysisStepDraft,
    CompletionConditionResult,
)
from ..analysis.workflow import PlanRevision, StepExecution
from ..config import Config
from ..memory import MemoryStore
from ..models import ChatResponse, Usage
from ..query_engine import QueryEngine
from ..semantic import COMMERCE_SEMANTIC_MODEL
from .memory_middleware import FewShotMemoryMiddleware
from .prompt import SYSTEM_PROMPT
from .sql_safety import SqlSafetyPolicy
from .tools import (
    AgentContext,
    RunArtifacts,
    get_schema,
    plan_query,
    run_sql,
    visualize_data,
)


class Agent:
    def __init__(
        self,
        *,
        config: Config,
        model: BaseChatModel,
        checkpointer: BaseCheckpointSaver[Any],
        memory: MemoryStore | None = None,
    ) -> None:
        self._model = model
        self._database_path = config.database_path.resolve()
        self._statement_timeout_ms = config.statement_timeout_ms
        self._memory = memory
        self._sql_policy = SqlSafetyPolicy(
            max_rows=config.max_result_rows,
            max_distinct_values=config.max_distinct_values,
            semantic_model=COMMERCE_SEMANTIC_MODEL,
        )

        middleware: list[Any] = [
            SummarizationMiddleware(
                model,
                trigger=("messages", 30),
                keep=("messages", 12),
            ),
            FewShotMemoryMiddleware(),
        ]

        self._agent = create_agent(
            model,
            tools=[get_schema, plan_query, run_sql, visualize_data],
            system_prompt=SYSTEM_PROMPT,
            middleware=middleware,
            context_schema=AgentContext,
            checkpointer=checkpointer,
            name="commerce_trace",
        )

    def analysis_session(self, thread_id: str) -> AnalysisAgentSession:
        return AnalysisAgentSession(owner=self, thread_id=thread_id)

    async def invoke(
        self,
        *,
        conversation_id: str,
        thread_id: str,
        message: str,
    ) -> ChatResponse:

        artifacts = RunArtifacts()
        usage_callback = UsageMetadataCallbackHandler()
        config: RunnableConfig = {
            "configurable": {"thread_id": thread_id},
            "callbacks": [usage_callback],
        }

        few_shot: list[dict[str, Any]] = []
        if self._memory is not None:
            few_shot = [
                entry.model_dump(mode="json") for entry in self._memory.recall(message)
            ]

        query_engine = self._query_engine()
        result = await self._agent.ainvoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
            context=AgentContext(
                artifacts=artifacts,
                query_engine=query_engine,
                few_shot=few_shot,
            ),
        )
        
        answer = _last_answer(result.get("messages", []))
        usage = _usage(usage_callback.usage_metadata)
        return ChatResponse(
            conversation_id=conversation_id,
            answer=answer,
            queries=artifacts.queries,
            charts=artifacts.charts,
            usage=usage,
        )

    def _query_engine(self) -> QueryEngine:
        return QueryEngine(
            database_path=self._database_path,
            statement_timeout_ms=self._statement_timeout_ms,
            sql_policy=self._sql_policy,
            semantic_model=COMMERCE_SEMANTIC_MODEL,
        )


class _PlanResponse(BaseModel):
    steps: list[AnalysisStepDraft] = Field(min_length=1, max_length=8)


class _PlanReview(BaseModel):
    revise: bool
    reason: str | None = None
    steps: list[AnalysisStepDraft] = Field(default_factory=list, max_length=8)


class _StepAssessment(BaseModel):
    results: list[CompletionConditionResult]


class AnalysisAgentSession:
    """Run-scoped adapter from LangChain to the deterministic analysis workflow."""

    def __init__(self, *, owner: Agent, thread_id: str) -> None:
        self._owner = owner
        self._thread_id = thread_id

    async def plan(self, question: str) -> list[AnalysisStepDraft]:
        planner = self._owner._model.with_structured_output(_PlanResponse)
        result = await planner.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你负责制定电商经营分析计划。计划项必须是业务分析目标，不得写成"
                        "get_schema、SQL 或工具调用。简单问题只生成一步；复杂问题最多六步。"
                        "每一步都要给出数据需求形式的完成条件，不得预设数据结论。"
                    )
                ),
                HumanMessage(content=question),
            ]
        )
        return _PlanResponse.model_validate(result).steps

    async def execute_step(
        self,
        *,
        question: str,
        step: AnalysisStep,
        prior_evidence: list[AnalysisEvidence],
    ) -> StepExecution:
        artifacts = RunArtifacts()
        usage_callback = UsageMetadataCallbackHandler()
        few_shot: list[dict[str, Any]] = []
        if self._owner._memory is not None:
            few_shot = [
                entry.model_dump(mode="json")
                for entry in self._owner._memory.recall(question)
            ]
        prompt = (
            f"原始分析问题：{question}\n\n"
            f"当前分析步骤：{step.title}\n"
            f"目标：{step.objective}\n"
            "步骤完成条件：\n- "
            + "\n- ".join(step.completion_conditions)
            + "\n\n已取得的事实：\n"
            + _evidence_context(prior_evidence)
            + "\n\n只执行当前步骤。必须先取得 Schema，再准备并执行查询。"
            "没有实际查询结果时不得声称步骤完成。"
        )
        result = await self._owner._agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config={
                "configurable": {"thread_id": self._thread_id},
                "callbacks": [usage_callback],
            },
            context=AgentContext(
                artifacts=artifacts,
                query_engine=self._owner._query_engine(),
                few_shot=few_shot,
            ),
        )
        summary = _last_answer(result.get("messages", []))
        evidence = [
            AnalysisEvidence.from_query(
                step_id=step.step_id,
                query_id=query.query_id,
                summary=summary,
                facts={
                    "columns": query.columns,
                    "row_count": query.row_count,
                    "rows": query.preview,
                    "truncated": query.truncated,
                },
            )
            for query in artifacts.queries
        ]
        condition_results: list[CompletionConditionResult] = []
        if evidence:
            assessor = self._owner._model.with_structured_output(_StepAssessment)
            assessment = await assessor.ainvoke(
                [
                    SystemMessage(
                        content=(
                            "逐条判断步骤完成条件是否被实际数据事实满足。"
                            "condition 必须原样返回；satisfied 只能依据给出的事实；"
                            "evidence_ids 必须引用给出的证据 ID。"
                        )
                    ),
                    HumanMessage(
                        content=(
                            f"完成条件：{step.completion_conditions}\n"
                            f"实际事实：{_evidence_context(evidence)}"
                        )
                    ),
                ]
            )
            condition_results = _StepAssessment.model_validate(assessment).results
        return StepExecution(
            summary=summary,
            evidence=evidence,
            condition_results=condition_results,
            queries=artifacts.queries,
            charts=artifacts.charts,
            usage=_usage(usage_callback.usage_metadata),
        )

    async def review_plan(
        self,
        *,
        question: str,
        completed_step: AnalysisStep,
        pending_steps: list[AnalysisStep],
        evidence: list[AnalysisEvidence],
        revisions_remaining: int,
    ) -> PlanRevision | None:
        reviewer = self._owner._model.with_structured_output(_PlanReview)
        result = await reviewer.ainvoke(
            [
                SystemMessage(
                    content=(
                        "判断新取得的数据事实是否要求调整尚未开始的分析步骤。"
                        "只有原计划无法回答问题时才修订；已完成步骤绝不能重写。"
                    )
                ),
                HumanMessage(
                    content=(
                        f"问题：{question}\n"
                        f"刚完成：{completed_step.title}\n"
                        f"剩余修订预算：{revisions_remaining}\n"
                        f"当前待执行步骤：{[step.model_dump() for step in pending_steps]}\n"
                        f"实际事实：{_evidence_context(evidence)}"
                    )
                ),
            ]
        )
        review = _PlanReview.model_validate(result)
        if not review.revise or not review.steps or not review.reason:
            return None
        return PlanRevision(reason=review.reason, steps=review.steps)

    async def synthesize(
        self,
        *,
        question: str,
        evidence: list[AnalysisEvidence],
    ) -> str:
        response = await self._owner._model.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你负责生成中文经营分析结论。只能使用提供的实际数据事实，"
                        "先给结论，再说明数据依据、统计口径和限制；不得补写因果推测。"
                    )
                ),
                HumanMessage(
                    content=f"问题：{question}\n\n实际数据事实：\n{_evidence_context(evidence)}"
                ),
            ]
        )
        return _message_text(response)


def _last_answer(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                parts = [
                    str(block.get("text", ""))
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                if any(part.strip() for part in parts):
                    return "\n".join(parts)
    return "本次分析未能生成最终回答，请调整问题后重试。"


def _usage(metadata: Mapping[str, Mapping[str, Any]]) -> Usage:
    input_tokens = 0
    output_tokens = 0
    for item in metadata.values():
        input_tokens += int(item.get("input_tokens", 0))
        output_tokens += int(item.get("output_tokens", 0))
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens)


def _evidence_context(evidence: list[AnalysisEvidence]) -> str:
    if not evidence:
        return "（暂无）"
    return "\n".join(
        f"- {item.summary}；query_id={item.query_id}；facts={item.facts}"
        for item in evidence
    )


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)
