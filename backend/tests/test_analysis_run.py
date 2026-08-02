from __future__ import annotations

import unittest

from commerce_trace.analysis import (
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
                    step_key="baseline",
                    title="核实销售额变化",
                    objective="比较两个周期的销售额",
                    completion_conditions=["取得两个周期的销售额、差额和变化率"],
                ),
                AnalysisStepDraft(
                    step_key="channel_breakdown",
                    depends_on=["baseline"],
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

        machine.complete_step(
            first.step_id,
            condition_results=[
                CompletionConditionResult(
                    condition=first.completion_conditions[0],
                    satisfied=True,
                    explanation="查询返回两个周期及变化率",
                )
            ],
        )

        machine.revise_pending_steps(
            [
                AnalysisStepDraft(
                    step_key="category_breakdown",
                    depends_on=["baseline"],
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
        self.assertEqual(machine.run.plan.steps[1].depends_on, ["baseline"])
        self.assertEqual(
            [event.sequence for event in machine.events],
            list(range(1, len(machine.events) + 1)),
        )

    def test_run_completes_only_after_every_step_meets_its_conditions(self) -> None:
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

        with self.assertRaisesRegex(AnalysisRunError, "step_condition_results_invalid"):
            machine.complete_step(step.step_id, condition_results=[])
        with self.assertRaisesRegex(AnalysisRunError, "run_not_complete"):
            machine.finish("销售额为200元")

        failed = machine.complete_step(
            step.step_id,
            condition_results=[
                CompletionConditionResult(
                    condition=step.completion_conditions[0],
                    satisfied=False,
                    explanation="查询没有使用标准销售额口径",
                )
            ],
        )
        self.assertEqual(failed.status, AnalysisStepStatus.FAILED)
        self.assertFalse(failed.completion_results[0].satisfied)
        self.assertIn("查询没有使用", failed.error)

        machine.finish_partial("已取得销售额，但口径未验证")
        retried = machine.retry_failed_step()
        self.assertIsNone(machine.run.answer)
        machine.start_next_step()
        machine.complete_step(
            retried.step_id,
            condition_results=[
                CompletionConditionResult(
                    condition=retried.completion_conditions[0],
                    satisfied=True,
                    explanation="查询使用标准销售额口径并返回聚合值",
                )
            ],
        )
        machine.finish("销售额为200元")

        self.assertEqual(machine.run.status, AnalysisRunStatus.COMPLETED)
        self.assertEqual(machine.run.answer, "销售额为200元")

    def test_step_dependencies_must_reference_prior_steps(self) -> None:
        machine = AnalysisRunMachine.create(
            conversation_id="conv_1",
            user_id="user_1",
            question="分析销售变化",
        )

        with self.assertRaisesRegex(AnalysisRunError, "plan_dependencies_invalid"):
            machine.publish_plan(
                [
                    AnalysisStepDraft(
                        step_key="breakdown",
                        depends_on=["baseline"],
                        title="先拆解",
                        objective="拆解变化",
                        completion_conditions=["取得贡献率"],
                    ),
                    AnalysisStepDraft(
                        step_key="baseline",
                        title="后核实",
                        objective="核实变化",
                        completion_conditions=["取得变化率"],
                    ),
                ]
            )

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
        machine.complete_step(
            first.step_id,
            condition_results=[
                CompletionConditionResult(
                    condition="取得变化率",
                    satisfied=True,
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
