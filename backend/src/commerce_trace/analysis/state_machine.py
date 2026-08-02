from __future__ import annotations

from typing import Any

from ..models import Chart, QueryTrace, Usage, utc_now
from .models import (
    AnalysisEvent,
    AnalysisEventType,
    AnalysisPlan,
    AnalysisRun,
    AnalysisRunStatus,
    AnalysisStep,
    AnalysisStepDraft,
    AnalysisStepStatus,
    CompletionConditionResult,
)


class AnalysisRunError(ValueError):
    pass


class AnalysisRunMachine:
    """Enforce sequential progress and append-only plan history."""

    def __init__(
        self,
        run: AnalysisRun,
        *,
        max_plan_revisions: int = 2,
        max_plan_steps: int = 8,
    ) -> None:
        self.run = run
        self.max_plan_revisions = max_plan_revisions
        self.max_plan_steps = max_plan_steps
        self.events: list[AnalysisEvent] = []

    @classmethod
    def create(
        cls,
        *,
        conversation_id: str,
        user_id: str,
        question: str,
        max_plan_revisions: int = 2,
        max_plan_steps: int = 8,
    ) -> AnalysisRunMachine:
        machine = cls(
            AnalysisRun(
                conversation_id=conversation_id,
                user_id=user_id,
                question=question,
            ),
            max_plan_revisions=max_plan_revisions,
            max_plan_steps=max_plan_steps,
        )
        machine._emit(AnalysisEventType.RUN_CREATED, {"status": machine.run.status})
        return machine

    def mark_planning(self) -> None:
        self._require_status(AnalysisRunStatus.QUEUED)
        self.run.status = AnalysisRunStatus.PLANNING
        self._touch()
        self._emit(AnalysisEventType.PLANNING_STARTED, {"status": self.run.status})

    def publish_plan(self, drafts: list[AnalysisStepDraft]) -> None:
        if not drafts:
            raise AnalysisRunError("plan_steps_required")
        if len(drafts) > self.max_plan_steps:
            raise AnalysisRunError("plan_step_limit_exceeded")
        self._validate_dependencies(drafts)
        if self.run.plan is not None:
            raise AnalysisRunError("plan_already_published")
        if self.run.status not in {AnalysisRunStatus.QUEUED, AnalysisRunStatus.PLANNING}:
            raise AnalysisRunError("run_not_plannable")
        self.run.plan = AnalysisPlan(steps=[AnalysisStep(**draft.model_dump()) for draft in drafts])
        self.run.status = AnalysisRunStatus.RUNNING
        self._touch()
        self._emit(
            AnalysisEventType.PLAN_PUBLISHED,
            {"plan": self.run.plan.model_dump(mode="json")},
        )

    def start_next_step(self) -> AnalysisStep:
        plan = self._plan()
        if any(step.status == AnalysisStepStatus.IN_PROGRESS for step in plan.steps):
            raise AnalysisRunError("step_already_in_progress")
        completed_keys = {
            step.step_key
            for step in plan.steps
            if step.status == AnalysisStepStatus.COMPLETED
        }
        pending = [
            step for step in plan.steps if step.status == AnalysisStepStatus.PENDING
        ]
        step = next(
            (item for item in pending if set(item.depends_on) <= completed_keys),
            None,
        )
        if step is None:
            if pending:
                raise AnalysisRunError("step_dependencies_blocked")
            raise AnalysisRunError("no_pending_step")
        step.status = AnalysisStepStatus.IN_PROGRESS
        self._touch()
        self._emit(
            AnalysisEventType.STEP_STARTED,
            {"step": step.model_dump(mode="json")},
        )
        return step

    def record_step_artifacts(
        self,
        step_id: str,
        *,
        queries: list[QueryTrace],
        charts: list[Chart],
        usage: Usage,
    ) -> None:
        """Attach execution artifacts through the state-machine boundary."""

        step = self._step(step_id)
        if step.status != AnalysisStepStatus.IN_PROGRESS:
            raise AnalysisRunError("step_not_in_progress")
        query_ids = {item.query_id for item in self.run.queries}
        self.run.queries.extend(item for item in queries if item.query_id not in query_ids)
        chart_ids = {item.chart_id for item in self.run.charts}
        self.run.charts.extend(item for item in charts if item.chart_id not in chart_ids)
        self.run.usage.input_tokens += usage.input_tokens
        self.run.usage.output_tokens += usage.output_tokens
        self._touch()
        self._emit(
            AnalysisEventType.STEP_ARTIFACTS_RECORDED,
            {
                "step_id": step_id,
                "query_ids": [item.query_id for item in queries],
                "chart_ids": [item.chart_id for item in charts],
                "usage": usage.model_dump(mode="json"),
            },
        )

    def complete_step(
        self,
        step_id: str,
        *,
        condition_results: list[CompletionConditionResult],
    ) -> AnalysisStep:
        step = self._step(step_id)
        if step.status != AnalysisStepStatus.IN_PROGRESS:
            raise AnalysisRunError("step_not_in_progress")
        if (
            len(condition_results) != len(step.completion_conditions)
            or {item.condition for item in condition_results}
            != set(step.completion_conditions)
        ):
            raise AnalysisRunError("step_condition_results_invalid")
        step.completion_results = condition_results
        unmet = [item for item in condition_results if not item.satisfied]
        if unmet:
            step.status = AnalysisStepStatus.FAILED
            step.error = "; ".join(item.explanation for item in unmet)
            self._touch()
            self._emit(
                AnalysisEventType.STEP_FAILED,
                {
                    "step": step.model_dump(mode="json"),
                    "completion_results": [
                        item.model_dump(mode="json") for item in condition_results
                    ],
                },
            )
            return step
        step.status = AnalysisStepStatus.COMPLETED
        step.error = None
        self._touch()
        self._emit(
            AnalysisEventType.STEP_COMPLETED,
            {
                "step": step.model_dump(mode="json"),
                "completion_results": [
                    item.model_dump(mode="json") for item in condition_results
                ],
            },
        )
        return step

    def fail_step(self, step_id: str, *, error: str) -> AnalysisStep:
        step = self._step(step_id)
        if step.status != AnalysisStepStatus.IN_PROGRESS:
            raise AnalysisRunError("step_not_in_progress")
        step.status = AnalysisStepStatus.FAILED
        step.error = error
        self._touch()
        self._emit(
            AnalysisEventType.STEP_FAILED,
            {"step": step.model_dump(mode="json")},
        )
        return step

    def revise_pending_steps(
        self,
        replacements: list[AnalysisStepDraft],
        *,
        reason: str,
    ) -> None:
        plan = self._plan()
        if any(step.status == AnalysisStepStatus.IN_PROGRESS for step in plan.steps):
            raise AnalysisRunError("cannot_revise_active_step")
        if not reason.strip():
            raise AnalysisRunError("plan_revision_reason_required")
        if self.run.plan_revision_count >= self.max_plan_revisions:
            raise AnalysisRunError("plan_revision_limit_exceeded")
        preserved = [step for step in plan.steps if step.status != AnalysisStepStatus.PENDING]
        if len(preserved) + len(replacements) > self.max_plan_steps:
            raise AnalysisRunError("plan_step_limit_exceeded")
        candidate = [
            *(AnalysisStepDraft(**step.model_dump()) for step in preserved),
            *replacements,
        ]
        self._validate_dependencies(candidate)
        plan.steps = [
            *preserved,
            *(AnalysisStep(**draft.model_dump()) for draft in replacements),
        ]
        plan.revision += 1
        plan.revision_reason = reason
        self.run.plan_revision_count += 1
        self._touch()
        self._emit(
            AnalysisEventType.PLAN_REVISED,
            {"plan": plan.model_dump(mode="json"), "reason": reason},
        )

    def finish(self, answer: str) -> None:
        plan = self._plan()
        if not plan.steps or any(
            step.status != AnalysisStepStatus.COMPLETED for step in plan.steps
        ):
            raise AnalysisRunError("run_not_complete")
        self.run.answer = answer
        self.run.status = AnalysisRunStatus.COMPLETED
        self._touch()
        self._emit(
            AnalysisEventType.RUN_COMPLETED,
            {"answer": answer, "status": self.run.status},
        )

    def finish_partial(self, answer: str) -> None:
        plan = self._plan()
        if any(step.status == AnalysisStepStatus.IN_PROGRESS for step in plan.steps):
            raise AnalysisRunError("step_still_in_progress")
        self.run.answer = answer
        self.run.status = AnalysisRunStatus.PARTIAL
        self._touch()
        self._emit(
            AnalysisEventType.RUN_PARTIAL,
            {"answer": answer, "status": self.run.status},
        )

    def fail_run(self, error: str) -> None:
        self.run.error = error
        self.run.status = AnalysisRunStatus.FAILED
        self._touch()
        self._emit(
            AnalysisEventType.RUN_FAILED,
            {"error": error, "status": self.run.status},
        )

    def retry_failed_step(self, step_id: str | None = None) -> AnalysisStep:
        if self.run.status not in {AnalysisRunStatus.FAILED, AnalysisRunStatus.PARTIAL}:
            raise AnalysisRunError("run_not_retryable")
        plan = self._plan()
        failed = next(
            (
                step
                for step in plan.steps
                if step.status == AnalysisStepStatus.FAILED
                and (step_id is None or step.step_id == step_id)
            ),
            None,
        )
        if failed is None:
            raise AnalysisRunError("failed_step_not_found")
        failed.status = AnalysisStepStatus.PENDING
        failed.error = None
        failed.completion_results = []
        self.run.status = AnalysisRunStatus.RUNNING
        self.run.error = None
        self.run.answer = None
        self._touch()
        self._emit(
            AnalysisEventType.RUN_RETRIED,
            {"step": failed.model_dump(mode="json"), "status": self.run.status},
        )
        return failed

    def retry_planning(self) -> None:
        """Reset a run that failed before its initial plan was published."""

        if self.run.status != AnalysisRunStatus.FAILED or self.run.plan is not None:
            raise AnalysisRunError("run_not_retryable")
        self.run.status = AnalysisRunStatus.QUEUED
        self.run.error = None
        self.run.answer = None
        self._touch()
        self._emit(
            AnalysisEventType.RUN_RETRIED,
            {"phase": "planning", "status": self.run.status},
        )

    def _plan(self) -> AnalysisPlan:
        if self.run.plan is None:
            raise AnalysisRunError("plan_not_published")
        return self.run.plan

    def _step(self, step_id: str) -> AnalysisStep:
        step = next((step for step in self._plan().steps if step.step_id == step_id), None)
        if step is None:
            raise AnalysisRunError("step_not_found")
        return step

    def _require_status(self, status: AnalysisRunStatus) -> None:
        if self.run.status != status:
            raise AnalysisRunError("invalid_run_status")

    @staticmethod
    def _validate_dependencies(drafts: list[AnalysisStepDraft]) -> None:
        seen: set[str] = set()
        for draft in drafts:
            if draft.step_key in seen or not set(draft.depends_on) <= seen:
                raise AnalysisRunError("plan_dependencies_invalid")
            if len(draft.depends_on) != len(set(draft.depends_on)):
                raise AnalysisRunError("plan_dependencies_invalid")
            seen.add(draft.step_key)

    def _touch(self) -> None:
        self.run.updated_at = utc_now()

    def _emit(self, event_type: AnalysisEventType, data: dict[str, Any]) -> None:
        self.run.event_sequence += 1
        self.events.append(
            AnalysisEvent(
                run_id=self.run.run_id,
                sequence=self.run.event_sequence,
                event_type=event_type,
                data=data,
            )
        )
