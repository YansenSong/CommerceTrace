from __future__ import annotations

import unittest

from commerce_trace.analysis import (
    AnalysisEvidence,
    AnalysisRunMachine,
    AnalysisRunStatus,
    AnalysisStep,
    AnalysisStepDraft,
    CompletionConditionResult,
)
from commerce_trace.analysis.workflow import (
    AnalysisWorkflow,
    PlanRevision,
    StepExecution,
)


class FakeAnalysisAgent:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.reviewed = False

    async def plan(self, question: str) -> list[AnalysisStepDraft]:
        return [
            AnalysisStepDraft(
                title="核实变化",
                objective="比较两个周期",
                completion_conditions=["取得两个周期指标及变化率"],
            ),
            AnalysisStepDraft(
                title="拆解渠道",
                objective="按渠道拆解",
                completion_conditions=["取得渠道贡献"],
            ),
        ]

    async def execute_step(
        self,
        *,
        question: str,
        step: AnalysisStep,
        prior_evidence: list[AnalysisEvidence],
    ) -> StepExecution:
        self.executed.append(step.title)
        evidence = AnalysisEvidence.from_query(
            step_id=step.step_id,
            query_id=f"query_{len(self.executed)}",
            summary=f"{step.title}得到事实",
            facts={"value": len(self.executed)},
        )
        return StepExecution(
            summary=f"{step.title}已完成",
            evidence=[evidence],
            condition_results=[
                CompletionConditionResult(
                    condition=condition,
                    satisfied=True,
                    evidence_ids=[evidence.evidence_id],
                    explanation="查询结果满足数据需求",
                )
                for condition in step.completion_conditions
            ],
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
        if self.reviewed:
            return None
        self.reviewed = True
        return PlanRevision(
            reason="渠道不足以解释变化，改看品类",
            steps=[
                AnalysisStepDraft(
                    title="拆解品类",
                    objective="按品类拆解",
                    completion_conditions=["取得品类贡献"],
                )
            ],
        )

    async def synthesize(
        self,
        *,
        question: str,
        evidence: list[AnalysisEvidence],
    ) -> str:
        return f"基于{len(evidence)}条证据完成分析"


class AnalysisWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_executes_visible_plan_and_applies_explained_revision(self) -> None:
        machine = AnalysisRunMachine.create(
            conversation_id="conv_1",
            user_id="user_1",
            question="为什么销售额下降？",
        )
        agent = FakeAnalysisAgent()
        persisted_sequences: list[int] = []

        async def persist(current: AnalysisRunMachine) -> None:
            persisted_sequences.extend(event.sequence for event in current.events)
            current.events.clear()

        workflow = AnalysisWorkflow(agent=agent, persist=persist)
        await workflow.execute(machine)

        self.assertEqual(machine.run.status, AnalysisRunStatus.COMPLETED)
        self.assertEqual(agent.executed, ["核实变化", "拆解品类"])
        self.assertEqual(machine.run.plan.revision, 2)
        self.assertEqual(machine.run.answer, "基于2条证据完成分析")
        self.assertEqual(persisted_sequences, list(range(1, len(persisted_sequences) + 1)))


if __name__ == "__main__":
    unittest.main()
