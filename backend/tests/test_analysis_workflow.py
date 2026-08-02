from __future__ import annotations

import unittest

from commerce_trace.analysis import (
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
from commerce_trace.models import QueryTrace


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
        prior_queries: list[QueryTrace],
    ) -> StepExecution:
        self.executed.append(step.title)
        query = QueryTrace(
            query_id=f"query_{len(self.executed)}",
            purpose=step.objective,
            sql=f"SELECT {len(self.executed)} AS value",
            columns=["value"],
            row_count=1,
            preview=[{"value": len(self.executed)}],
        )
        return StepExecution(
            queries=[query],
            condition_results=[
                CompletionConditionResult(
                    condition=condition,
                    satisfied=True,
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
        queries: list[QueryTrace],
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
        queries: list[QueryTrace],
    ) -> str:
        return f"基于{len(queries)}条查询完成分析"


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
        self.assertEqual(machine.run.answer, "基于2条查询完成分析")
        self.assertEqual(persisted_sequences, list(range(1, len(persisted_sequences) + 1)))

    async def test_unmet_completion_condition_finishes_partial_with_facts(self) -> None:
        machine = AnalysisRunMachine.create(
            conversation_id="conv_1",
            user_id="user_1",
            question="为什么销售额下降？",
        )
        agent = FakeAnalysisAgent()

        async def unsatisfied_execute(**kwargs: object) -> StepExecution:
            step = kwargs["step"]
            assert isinstance(step, AnalysisStep)
            return StepExecution(
                queries=[
                    QueryTrace(
                        query_id="query_partial",
                        prepared_query_id="prepared_partial",
                        purpose="取得当期销售额",
                        sql="SELECT 96 AS current_revenue",
                        plan=["SCAN CONSTANT ROW"],
                        semantic_fingerprint="semantic-v1",
                        columns=["current_revenue"],
                        row_count=1,
                        preview=[{"current_revenue": 96}],
                    )
                ],
                condition_results=[
                    CompletionConditionResult(
                        condition=step.completion_conditions[0],
                        satisfied=False,
                        explanation="缺少上一周期销售额",
                    )
                ],
            )

        agent.execute_step = unsatisfied_execute  # type: ignore[method-assign]

        async def persist(current: AnalysisRunMachine) -> None:
            current.events.clear()

        await AnalysisWorkflow(agent=agent, persist=persist).execute(machine)

        self.assertEqual(machine.run.status, AnalysisRunStatus.PARTIAL)
        self.assertEqual(machine.run.plan.steps[0].status, "failed")
        self.assertEqual(
            machine.run.queries[0].preview,
            [{"current_revenue": 96}],
        )
        self.assertEqual(machine.run.queries[0].plan, ["SCAN CONSTANT ROW"])
        self.assertIn("缺少上一周期", machine.run.plan.steps[0].error)


if __name__ == "__main__":
    unittest.main()
