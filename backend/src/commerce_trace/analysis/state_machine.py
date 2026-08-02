from __future__ import annotations

from typing import Any

from ..models import utc_now
from .models import (
    AnalysisEvent,
    AnalysisEvidence,
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
        machine._emit("run_created", {"status": machine.run.status})
        return machine

    def mark_planning(self) -> None:
        self._require_status(AnalysisRunStatus.QUEUED)
        self.run.status = AnalysisRunStatus.PLANNING
        self._touch()
        self._emit("planning_started", {"status": self.run.status})

    def publish_plan(self, drafts: list[AnalysisStepDraft]) -> None:
        if not drafts:
            raise AnalysisRunError("plan_steps_required")
        if len(drafts) > self.max_plan_steps:
            raise AnalysisRunError("plan_step_limit_exceeded")
        if self.run.plan is not None:
            raise AnalysisRunError("plan_already_published")
        if self.run.status not in {AnalysisRunStatus.QUEUED, AnalysisRunStatus.PLANNING}:
            raise AnalysisRunError("run_not_plannable")
        self.run.plan = AnalysisPlan(steps=[AnalysisStep(**draft.model_dump()) for draft in drafts])
        self.run.status = AnalysisRunStatus.RUNNING
        self._touch()
        self._emit("plan_published", {"plan": self.run.plan.model_dump(mode="json")})

    def start_next_step(self) -> AnalysisStep:
        plan = self._plan()
        if any(step.status == AnalysisStepStatus.IN_PROGRESS for step in plan.steps):
            raise AnalysisRunError("step_already_in_progress")
        step = next(
            (step for step in plan.steps if step.status == AnalysisStepStatus.PENDING),
            None,
        )
        if step is None:
            raise AnalysisRunError("no_pending_step")
        step.status = AnalysisStepStatus.IN_PROGRESS
        self._touch()
        self._emit("step_started", {"step": step.model_dump(mode="json")})
        return step

    def complete_step(
        self,
        step_id: str,
        *,
        evidence: list[AnalysisEvidence],
        condition_results: list[CompletionConditionResult],
    ) -> AnalysisStep:
        if not evidence:
            raise AnalysisRunError("step_evidence_required")
        step = self._step(step_id)
        if step.status != AnalysisStepStatus.IN_PROGRESS:
            raise AnalysisRunError("step_not_in_progress")
        if any(item.step_id != step_id for item in evidence):
            raise AnalysisRunError("evidence_step_mismatch")
        if (
            len(condition_results) != len(step.completion_conditions)
            or {item.condition for item in condition_results}
            != set(step.completion_conditions)
        ):
            raise AnalysisRunError("step_condition_results_invalid")
        evidence_ids = {item.evidence_id for item in evidence}
        if any(
            not item.satisfied
            or not item.evidence_ids
            or not set(item.evidence_ids).issubset(evidence_ids)
            for item in condition_results
        ):
            raise AnalysisRunError("step_conditions_not_met")
        step.status = AnalysisStepStatus.COMPLETED
        step.evidence_ids.extend(item.evidence_id for item in evidence)
        step.completion_results = condition_results
        self.run.evidence.extend(evidence)
        self._touch()
        self._emit(
            "step_completed",
            {
                "step": step.model_dump(mode="json"),
                "evidence": [item.model_dump(mode="json") for item in evidence],
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
        self._emit("step_failed", {"step": step.model_dump(mode="json")})
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
        plan.steps = [
            *preserved,
            *(AnalysisStep(**draft.model_dump()) for draft in replacements),
        ]
        plan.revision += 1
        plan.revision_reason = reason
        self.run.plan_revision_count += 1
        self._touch()
        self._emit(
            "plan_revised",
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
        self._emit("run_completed", {"answer": answer, "status": self.run.status})

    def finish_partial(self, answer: str) -> None:
        plan = self._plan()
        if any(step.status == AnalysisStepStatus.IN_PROGRESS for step in plan.steps):
            raise AnalysisRunError("step_still_in_progress")
        self.run.answer = answer
        self.run.status = AnalysisRunStatus.PARTIAL
        self._touch()
        self._emit("run_partial", {"answer": answer, "status": self.run.status})

    def fail_run(self, error: str) -> None:
        self.run.error = error
        self.run.status = AnalysisRunStatus.FAILED
        self._touch()
        self._emit("run_failed", {"error": error, "status": self.run.status})

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
        failed.evidence_ids = []
        failed.completion_results = []
        self.run.status = AnalysisRunStatus.RUNNING
        self.run.error = None
        self._touch()
        self._emit(
            "run_retried",
            {"step": failed.model_dump(mode="json"), "status": self.run.status},
        )
        return failed

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

    def _touch(self) -> None:
        self.run.updated_at = utc_now()

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        self.run.event_sequence += 1
        self.events.append(
            AnalysisEvent(
                run_id=self.run.run_id,
                sequence=self.run.event_sequence,
                event_type=event_type,
                data=data,
            )
        )
