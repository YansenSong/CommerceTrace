from __future__ import annotations

import unittest

from commerce_trace.analysis import (
    AnalysisEvidence,
    AnalysisRunError,
    AnalysisRunMachine,
    AnalysisRunStatus,
    AnalysisStepDraft,
    AnalysisStepStatus,
    CompletionConditionResult,
)


class AnalysisRunMachineTests(unittest.TestCase):
    def test_plan_runs_sequentially_and_completed_history_is_immutable(self) -> None:
        machine = AnalysisRunMachine.create(
            conversation_id="conv_1",
            user_id="user_1",
            question="为什么销售额下降？",
            max_plan_revisions=2,
            max_plan_steps=5,
        )
        machine.publish_plan(
            [
                AnalysisStepDraft(
                    title="核实销售额变化",
                    objective="比较两个周期的销售额",
                    completion_conditions=["取得两个周期的销售额、差额和变化率"],
                ),
                AnalysisStepDraft(
                    title="拆解渠道贡献",
                    objective="比较各渠道变化",
                    completion_conditions=["取得各渠道变化金额和贡献率"],
                ),
            ]
        )

        first = machine.start_next_step()
        self.assertEqual(first.status, AnalysisStepStatus.IN_PROGRESS)
        with self.assertRaisesRegex(AnalysisRunError, "step_already_in_progress"):
            machine.start_next_step()

        evidence = AnalysisEvidence.from_query(
            step_id=first.step_id,
            query_id="query_1",
            summary="7月销售额较6月下降20%",
            facts={"june": 120, "july": 96, "change_rate": -0.2},
        )
        machine.complete_step(
            first.step_id,
            evidence=[evidence],
            condition_results=[
                CompletionConditionResult(
                    condition=first.completion_conditions[0],
                    satisfied=True,
                    evidence_ids=[evidence.evidence_id],
                    explanation="查询返回两个周期及变化率",
                )
            ],
        )

        machine.revise_pending_steps(
            [
                AnalysisStepDraft(
                    title="拆解品类贡献",
                    objective="按品类定位变化",
                    completion_conditions=["取得各品类变化金额和贡献率"],
                )
            ],
            reason="渠道变化不足以解释总降幅",
        )

        self.assertEqual(machine.run.plan.revision, 2)
        self.assertEqual(machine.run.plan.steps[0].title, "核实销售额变化")
        self.assertEqual(machine.run.plan.steps[0].status, AnalysisStepStatus.COMPLETED)
        self.assertEqual(machine.run.plan.steps[1].title, "拆解品类贡献")
        self.assertEqual(machine.run.plan.steps[1].status, AnalysisStepStatus.PENDING)
        self.assertEqual(
            [event.sequence for event in machine.events],
            list(range(1, len(machine.events) + 1)),
        )

    def test_run_completes_only_after_every_step_has_actual_evidence(self) -> None:
        machine = AnalysisRunMachine.create(
            conversation_id="conv_1",
            user_id="user_1",
            question="销售额是多少？",
        )
        machine.publish_plan(
            [
                AnalysisStepDraft(
                    title="计算销售额",
                    objective="取得销售额",
                    completion_conditions=["取得使用标准口径计算的销售额"],
                )
            ]
        )
        step = machine.start_next_step()

        with self.assertRaisesRegex(AnalysisRunError, "step_evidence_required"):
            machine.complete_step(step.step_id, evidence=[], condition_results=[])
        with self.assertRaisesRegex(AnalysisRunError, "run_not_complete"):
            machine.finish("销售额为200元")

        evidence = AnalysisEvidence.from_query(
            step_id=step.step_id,
            query_id="query_1",
            summary="销售额为200元",
            facts={"revenue": 200},
        )
        with self.assertRaisesRegex(AnalysisRunError, "step_conditions_not_met"):
            machine.complete_step(
                step.step_id,
                evidence=[evidence],
                condition_results=[
                    CompletionConditionResult(
                        condition=step.completion_conditions[0],
                        satisfied=False,
                        evidence_ids=[],
                        explanation="查询没有使用标准销售额口径",
                    )
                ],
            )

        machine.complete_step(
            step.step_id,
            evidence=[evidence],
            condition_results=[
                CompletionConditionResult(
                    condition=step.completion_conditions[0],
                    satisfied=True,
                    evidence_ids=[evidence.evidence_id],
                    explanation="查询使用标准销售额口径并返回聚合值",
                )
            ],
        )
        machine.finish("销售额为200元")

        self.assertEqual(machine.run.status, AnalysisRunStatus.COMPLETED)
        self.assertEqual(machine.run.answer, "销售额为200元")
        self.assertEqual(machine.run.evidence[0].facts, {"revenue": 200})

    def test_failed_step_can_retry_without_rewriting_completed_history(self) -> None:
        machine = AnalysisRunMachine.create(
            conversation_id="conv_1",
            user_id="user_1",
            question="分析销售变化",
        )
        machine.publish_plan(
            [
                AnalysisStepDraft(
                    title="核实变化",
                    objective="核实变化",
                    completion_conditions=["取得变化率"],
                ),
                AnalysisStepDraft(
                    title="拆解原因",
                    objective="拆解原因",
                    completion_conditions=["取得贡献率"],
                ),
            ]
        )
        first = machine.start_next_step()
        first_evidence = AnalysisEvidence.from_query(
            step_id=first.step_id,
            query_id="query_1",
            summary="下降20%",
            facts={"change_rate": -0.2},
        )
        machine.complete_step(
            first.step_id,
            evidence=[first_evidence],
            condition_results=[
                CompletionConditionResult(
                    condition="取得变化率",
                    satisfied=True,
                    evidence_ids=[first_evidence.evidence_id],
                    explanation="已取得变化率",
                )
            ],
        )
        second = machine.start_next_step()
        machine.fail_step(second.step_id, error="查询超时")
        machine.fail_run("查询超时")

        retried = machine.retry_failed_step()

        self.assertEqual(machine.run.status, AnalysisRunStatus.RUNNING)
        self.assertEqual(machine.run.plan.steps[0].status, AnalysisStepStatus.COMPLETED)
        self.assertEqual(retried.step_id, second.step_id)
        self.assertEqual(retried.status, AnalysisStepStatus.PENDING)
        self.assertIsNone(retried.error)
        self.assertEqual(machine.events[-1].event_type, "run_retried")


if __name__ == "__main__":
    unittest.main()
