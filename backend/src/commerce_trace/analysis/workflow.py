from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from pydantic import BaseModel, Field

from ..models import Chart, QueryTrace, Usage
from .models import (
    AnalysisStep,
    AnalysisStepDraft,
    AnalysisStepStatus,
    CompletionConditionResult,
)
from .state_machine import AnalysisRunError, AnalysisRunMachine


class StepExecution(BaseModel):
    condition_results: list[CompletionConditionResult] = Field(default_factory=list)
    queries: list[QueryTrace] = Field(default_factory=list)
    charts: list[Chart] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)


class PlanRevision(BaseModel):
    reason: str
    steps: list[AnalysisStepDraft]


class AnalysisAgent(Protocol):
    async def plan(self, question: str) -> list[AnalysisStepDraft]: ...

    async def execute_step(
        self,
        *,
        question: str,
        step: AnalysisStep,
        prior_queries: list[QueryTrace],
    ) -> StepExecution: ...

    async def review_plan(
        self,
        *,
        question: str,
        completed_step: AnalysisStep,
        pending_steps: list[AnalysisStep],
        queries: list[QueryTrace],
        revisions_remaining: int,
    ) -> PlanRevision | None: ...

    async def synthesize(
        self,
        *,
        question: str,
        queries: list[QueryTrace],
    ) -> str: ...


PersistAnalysisRun = Callable[[AnalysisRunMachine], Awaitable[None]]


class AnalysisWorkflow:
    """Execute a visible plan sequentially and persist every state transition."""

    def __init__(self, *, agent: AnalysisAgent, persist: PersistAnalysisRun) -> None:
        self._agent = agent
        self._persist = persist

    async def execute(self, machine: AnalysisRunMachine) -> None:
        await self._persist(machine)
        try:
            if machine.run.plan is None:
                machine.mark_planning()
                await self._persist(machine)
                machine.publish_plan(await self._agent.plan(machine.run.question))
                await self._persist(machine)

            while self._pending_steps(machine):
                step = machine.start_next_step()
                await self._persist(machine)
                execution = await self._agent.execute_step(
                    question=machine.run.question,
                    step=step,
                    prior_queries=list(machine.run.queries),
                )
                machine.record_step_artifacts(
                    step.step_id,
                    queries=execution.queries,
                    charts=execution.charts,
                    usage=execution.usage,
                )
                completed_step = machine.complete_step(
                    step.step_id,
                    condition_results=execution.condition_results,
                )
                await self._persist(machine)

                if completed_step.status == AnalysisStepStatus.FAILED:
                    answer = await self._agent.synthesize(
                        question=machine.run.question,
                        queries=list(machine.run.queries),
                    )
                    machine.finish_partial(answer)
                    await self._persist(machine)
                    return

                pending = self._pending_steps(machine)
                revisions_remaining = (
                    machine.max_plan_revisions - machine.run.plan_revision_count
                )
                if pending and revisions_remaining > 0:
                    revision = await self._agent.review_plan(
                        question=machine.run.question,
                        completed_step=step,
                        pending_steps=pending,
                        queries=list(machine.run.queries),
                        revisions_remaining=revisions_remaining,
                    )
                    if revision is not None:
                        machine.revise_pending_steps(
                            revision.steps,
                            reason=revision.reason,
                        )
                        await self._persist(machine)

            answer = await self._agent.synthesize(
                question=machine.run.question,
                queries=list(machine.run.queries),
            )
            machine.finish(answer)
            await self._persist(machine)
        except Exception as exc:
            active = self._active_step(machine)
            if active is not None:
                machine.fail_step(active.step_id, error=str(exc))
            machine.fail_run(str(exc))
            await self._persist(machine)
            if isinstance(exc, AnalysisRunError) and str(exc) in {
                "step_condition_results_invalid",
            }:
                return
            raise

    @staticmethod
    def _pending_steps(machine: AnalysisRunMachine) -> list[AnalysisStep]:
        if machine.run.plan is None:
            return []
        return [
            step
            for step in machine.run.plan.steps
            if step.status == AnalysisStepStatus.PENDING
        ]

    @staticmethod
    def _active_step(machine: AnalysisRunMachine) -> AnalysisStep | None:
        if machine.run.plan is None:
            return None
        return next(
            (
                step
                for step in machine.run.plan.steps
                if step.status == AnalysisStepStatus.IN_PROGRESS
            ),
            None,
        )
