from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from ..context import RetrievedContext
from ..contracts import Chart, Evidence, LlmMessage, PlanStep
from .tools import ToolExecutionContext


class RequestPhase(str, Enum):
    STARTED = "started"
    CONTEXT_READY = "context_ready"
    PLANNED = "planned"
    EXECUTING = "executing"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    REFUSED = "refused"
    CLARIFICATION_REQUIRED = "clarification_required"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


_ALLOWED_TRANSITIONS: dict[RequestPhase, set[RequestPhase]] = {
    RequestPhase.STARTED: {
        RequestPhase.CONTEXT_READY,
        RequestPhase.COMPLETED,
        RequestPhase.REFUSED,
        RequestPhase.CLARIFICATION_REQUIRED,
        RequestPhase.FAILED,
    },
    RequestPhase.CONTEXT_READY: {RequestPhase.PLANNED, RequestPhase.FAILED},
    RequestPhase.PLANNED: {RequestPhase.EXECUTING, RequestPhase.FAILED},
    RequestPhase.EXECUTING: {RequestPhase.SYNTHESIZING, RequestPhase.FAILED},
    RequestPhase.SYNTHESIZING: {
        RequestPhase.COMPLETED,
        RequestPhase.INCOMPLETE,
        RequestPhase.FAILED,
    },
    RequestPhase.COMPLETED: set(),
    RequestPhase.REFUSED: set(),
    RequestPhase.CLARIFICATION_REQUIRED: set(),
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
    retrieved: RetrievedContext | None = None
    plan: list[PlanStep] = field(default_factory=list)
    messages: list[LlmMessage] = field(default_factory=list)
    tool_context: ToolExecutionContext | None = None
    evidence: list[Evidence] = field(default_factory=list)
    charts: list[Chart] = field(default_factory=list)
    retry_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    incomplete_reason: str | None = None
    llm_content: str = ""
    current_step_index: int = 0
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

    def set_context(self, retrieved: RetrievedContext) -> None:
        self.transition_to(RequestPhase.CONTEXT_READY)
        self.retrieved = retrieved

    def set_plan(self, plan: list[PlanStep]) -> None:
        self.transition_to(RequestPhase.PLANNED)
        self.plan = plan
        self.messages = [LlmMessage(role="user", content=self.question)]
        self.tool_context = ToolExecutionContext(
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            request_id=self.request_id,
        )

    def begin_execution(self) -> None:
        self.transition_to(RequestPhase.EXECUTING)

    def begin_current_step(self) -> PlanStep | None:
        if self.current_step_index >= len(self.plan):
            return None
        step = self.plan[self.current_step_index]
        step.status = "in_progress"
        return step

    def complete_current_step(self) -> None:
        if self.current_step_index >= len(self.plan):
            return
        self.plan[self.current_step_index].status = "completed"
        self.current_step_index += 1

    def record_llm_usage(self, usage: dict[str, int]) -> None:
        self.llm_calls += 1
        self.input_tokens += int(usage.get("prompt_tokens", 0))
        self.output_tokens += int(usage.get("completion_tokens", 0))

    def begin_tool(
        self,
        *,
        name: str,
        purpose: str,
        max_tool_iterations: int,
        max_business_sql_calls: int,
        max_sql_retries_per_purpose: int,
    ) -> bool:
        if self.tool_iterations >= max_tool_iterations:
            self.incomplete_reason = "tool_iteration_limit"
            return False
        if name == "run_sql":
            if self.sql_calls >= max_business_sql_calls:
                self.incomplete_reason = "business_sql_limit"
                return False
            if self.retry_counts[purpose] > max_sql_retries_per_purpose:
                self.incomplete_reason = "sql_retry_limit"
                return False
            self.sql_calls += 1
        self.tool_iterations += 1
        return True

    def record_tool_failure(self, name: str, purpose: str) -> None:
        if name == "run_sql":
            self.retry_counts[purpose] += 1

    def add_evidence(self, evidence: Evidence) -> None:
        self.evidence.append(evidence)

    def add_chart(self, chart: Chart) -> None:
        self.charts.append(chart)

    def begin_synthesis(self) -> None:
        self.transition_to(RequestPhase.SYNTHESIZING)

    def finish(self, terminal_phase: RequestPhase) -> None:
        if terminal_phase not in {
            RequestPhase.COMPLETED,
            RequestPhase.REFUSED,
            RequestPhase.CLARIFICATION_REQUIRED,
            RequestPhase.FAILED,
            RequestPhase.INCOMPLETE,
        }:
            raise ValueError(f"{terminal_phase.value} is not a terminal request phase")
        self.transition_to(terminal_phase)

    def unfinished_steps(self) -> list[dict[str, object]]:
        unfinished = [
            step.model_dump() for step in self.plan if step.status not in {"completed"}
        ]
        if self.incomplete_reason and not unfinished:
            if self.incomplete_reason == "insufficient_evidence":
                title = "补充可执行证据"
            elif self.incomplete_reason == "data_coverage_gap":
                title = "补充目标时间范围的数据"
            else:
                title = "预算之外的后续探索"
            unfinished = [{"id": "budget-stop", "title": title, "status": "pending"}]
        return unfinished

    def usage(self, candidate_adopted: int = 0) -> dict[str, int]:
        memories = self.retrieved.memories if self.retrieved is not None else []
        return {
            "tool_iterations": self.tool_iterations,
            "business_sql_calls": self.sql_calls,
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "trusted_recalled": sum(item.label == "trusted" for item in memories),
            "candidate_recalled": sum(
                item.label == "unverified_candidate" for item in memories
            ),
            "candidate_adopted": candidate_adopted,
        }
