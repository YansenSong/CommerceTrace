from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ..persistence.analysis_runs import AnalysisRunStore
from .models import AnalysisRun
from .state_machine import AnalysisRunError, AnalysisRunMachine
from .workflow import AnalysisAgent, AnalysisWorkflow

logger = logging.getLogger(__name__)

AnalysisAgentFactory = Callable[[str], AnalysisAgent]
AnalysisRunFinished = Callable[[AnalysisRun], Awaitable[None]]


class AnalysisCoordinator:
    """Own background analysis tasks while snapshots live in durable storage."""

    def __init__(
        self,
        *,
        store: AnalysisRunStore,
        agent_factory: AnalysisAgentFactory,
        on_finished: AnalysisRunFinished,
    ) -> None:
        self._store = store
        self._agent_factory = agent_factory
        self._on_finished = on_finished
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._task_conversations: dict[str, str] = {}

    async def start(
        self,
        *,
        conversation_id: str,
        user_id: str,
        question: str,
        thread_id: str,
    ) -> AnalysisRun:
        machine = AnalysisRunMachine.create(
            conversation_id=conversation_id,
            user_id=user_id,
            question=question,
        )
        await self._store.save(machine)
        workflow = AnalysisWorkflow(
            agent=self._agent_factory(thread_id),
            persist=self._store.save,
        )
        self._schedule(workflow, machine)
        return machine.run.model_copy(deep=True)

    async def retry(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> AnalysisRun | None:
        run = await self._store.get(user_id, run_id)
        if run is None:
            return None
        if run_id in self._tasks:
            raise AnalysisRunError("run_already_running")
        machine = AnalysisRunMachine(run)
        if run.plan is None:
            machine.retry_planning()
        else:
            machine.retry_failed_step()
        await self._store.save(machine)
        workflow = AnalysisWorkflow(
            agent=self._agent_factory(f"{user_id}:{run.conversation_id}"),
            persist=self._store.save,
        )
        self._schedule(workflow, machine)
        return machine.run.model_copy(deep=True)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel_conversation(self, conversation_id: str) -> None:
        tasks = [
            task
            for run_id, task in self._tasks.items()
            if self._task_conversations.get(run_id) == conversation_id
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(
        self,
        workflow: AnalysisWorkflow,
        machine: AnalysisRunMachine,
    ) -> None:
        try:
            await workflow.execute(machine)
            await self._on_finished(machine.run)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Analysis run %s failed", machine.run.run_id)

    def _schedule(
        self,
        workflow: AnalysisWorkflow,
        machine: AnalysisRunMachine,
    ) -> None:
        run_id = machine.run.run_id
        task = asyncio.create_task(
            self._execute(workflow, machine),
            name=f"analysis:{run_id}",
        )
        self._tasks[run_id] = task
        self._task_conversations[run_id] = machine.run.conversation_id

        def discard_finished(_task: asyncio.Task[None]) -> None:
            self._tasks.pop(run_id, None)
            self._task_conversations.pop(run_id, None)

        task.add_done_callback(discard_finished)
