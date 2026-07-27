from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from ..models import Evidence, LlmMessage
from .tool import ToolContext


class RequestPhase(str, Enum):
    """枚举一次 Agent 请求从开始到结束的生命周期阶段。"""

    STARTED = "started"
    CONTEXT_READY = "context_ready"
    EXECUTING = "executing"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    REFUSED = "refused"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


_ALLOWED_TRANSITIONS: dict[RequestPhase, set[RequestPhase]] = {
    RequestPhase.STARTED: {
        RequestPhase.CONTEXT_READY,
        RequestPhase.COMPLETED,
        RequestPhase.REFUSED,
        RequestPhase.FAILED,
    },
    RequestPhase.CONTEXT_READY: {RequestPhase.EXECUTING, RequestPhase.FAILED},
    RequestPhase.EXECUTING: {RequestPhase.SYNTHESIZING, RequestPhase.FAILED},
    RequestPhase.SYNTHESIZING: {
        RequestPhase.COMPLETED,
        RequestPhase.INCOMPLETE,
        RequestPhase.FAILED,
    },
    RequestPhase.COMPLETED: set(),
    RequestPhase.REFUSED: set(),
    RequestPhase.FAILED: set(),
    RequestPhase.INCOMPLETE: set(),
}


@dataclass
class RequestState:
    """集中维护单次 Agent 请求的阶段、预算、消息和证据。"""

    user_id: str
    conversation_id: str
    request_id: str
    question: str
    phase: RequestPhase = RequestPhase.STARTED
    messages: list[LlmMessage] = field(default_factory=list)
    tool_context: ToolContext | None = None
    evidence: list[Evidence] = field(default_factory=list)
    retry_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    incomplete_reason: str | None = None
    llm_content: str = ""
    tool_iterations: int = 0
    sql_calls: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def transition_to(self, target: RequestPhase) -> None:
        """校验状态迁移是否合法，并切换到目标阶段。"""

        if target not in _ALLOWED_TRANSITIONS[self.phase]:
            raise ValueError(
                f"invalid request phase transition: {self.phase.value} -> {target.value}"
            )
        self.phase = target

    def mark_context_ready(self) -> None:
        """标记本次请求所需上下文已经准备完成。"""

        self.transition_to(RequestPhase.CONTEXT_READY)

    def prepare_execution(self) -> None:
        """进入执行阶段并初始化模型消息和工具上下文。"""

        self.transition_to(RequestPhase.EXECUTING)
        self.messages = [LlmMessage(role="user", content=self.question)]
        self.tool_context = ToolContext(
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            request_id=self.request_id,
        )

    def record_llm_usage(self, usage: dict[str, int]) -> None:
        """累计一次大模型调用及其输入、输出令牌用量。"""

        self.llm_calls += 1
        self.input_tokens += int(usage.get("prompt_tokens", 0))
        self.output_tokens += int(usage.get("completion_tokens", 0))

    def begin_tool(
        self,
        *,
        name: str,
        kind: str,
        purpose: str,
        max_tool_iterations: int,
        max_business_sql_calls: int,
        max_sql_retries_per_purpose: int,
    ) -> bool:
        """在预算允许时登记一次工具调用，否则记录停止原因。"""

        if self.tool_iterations >= max_tool_iterations:
            self.incomplete_reason = "tool_iteration_limit"
            return False
        if kind == "business_sql":
            if self.sql_calls >= max_business_sql_calls:
                self.incomplete_reason = "business_sql_limit"
                return False
            if self.retry_counts[purpose] > max_sql_retries_per_purpose:
                self.incomplete_reason = "sql_retry_limit"
                return False
            self.sql_calls += 1
        self.tool_iterations += 1
        return True

    def record_tool_failure(self, kind: str, purpose: str) -> None:
        """记录指定用途的业务 SQL 工具失败次数。"""

        if kind == "business_sql":
            self.retry_counts[purpose] += 1

    def add_evidence(self, evidence: Evidence) -> None:
        """将新生成的证据加入本次请求。"""

        self.evidence.append(evidence)

    def begin_synthesis(self) -> None:
        """进入基于证据合成最终答案的阶段。"""

        self.transition_to(RequestPhase.SYNTHESIZING)

    def finish(self, terminal_phase: RequestPhase) -> None:
        """校验并进入指定的终止阶段。"""

        if terminal_phase not in {
            RequestPhase.COMPLETED,
            RequestPhase.REFUSED,
            RequestPhase.FAILED,
            RequestPhase.INCOMPLETE,
        }:
            raise ValueError(f"{terminal_phase.value} is not a terminal request phase")
        self.transition_to(terminal_phase)

    def usage(self) -> dict[str, int]:
        """汇总本次请求消耗的工具、SQL、模型和令牌预算。"""

        return {
            "tool_iterations": self.tool_iterations,
            "business_sql_calls": self.sql_calls,
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
