from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from ..contracts import Evidence, LlmMessage
from .tools import ToolContext


class RequestPhase(str, Enum):
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
        if target not in _ALLOWED_TRANSITIONS[self.phase]:
            raise ValueError(
                f"invalid request phase transition: {self.phase.value} -> {target.value}"
            )
        self.phase = target

    def mark_context_ready(self) -> None:
        self.transition_to(RequestPhase.CONTEXT_READY)

    def prepare_execution(self) -> None:
        self.transition_to(RequestPhase.EXECUTING)
        self.messages = [LlmMessage(role="user", content=self.question)]
        self.tool_context = ToolContext(
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            request_id=self.request_id,
        )

    def record_llm_usage(self, usage: dict[str, int]) -> None:
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
        if kind == "business_sql":
            self.retry_counts[purpose] += 1

    def add_evidence(self, evidence: Evidence) -> None:
        self.evidence.append(evidence)

    def begin_synthesis(self) -> None:
        self.transition_to(RequestPhase.SYNTHESIZING)

    def finish(self, terminal_phase: RequestPhase) -> None:
        if terminal_phase not in {
            RequestPhase.COMPLETED,
            RequestPhase.REFUSED,
            RequestPhase.FAILED,
            RequestPhase.INCOMPLETE,
        }:
            raise ValueError(f"{terminal_phase.value} is not a terminal request phase")
        self.transition_to(terminal_phase)

    def usage(self) -> dict[str, int]:
        return {
            "tool_iterations": self.tool_iterations,
            "business_sql_calls": self.sql_calls,
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
