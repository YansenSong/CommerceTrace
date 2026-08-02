from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from commerce_trace.analysis import (
    AnalysisStep,
    AnalysisStepDraft,
    CompletionConditionResult,
)
from commerce_trace.analysis.workflow import PlanRevision, StepExecution
from commerce_trace.api import create_app
from commerce_trace.config import Config
from commerce_trace.models import QueryTrace


class ApiAnalysisAgent:
    def __init__(self) -> None:
        self.fail_once = False
        self.plan_fail_once = False

    async def plan(self, question: str) -> list[AnalysisStepDraft]:
        if self.plan_fail_once:
            self.plan_fail_once = False
            raise RuntimeError("planning provider rejected the request")
        return [
            AnalysisStepDraft(
                title="计算销售额",
                objective="按标准口径计算销售额",
                completion_conditions=["取得销售额"],
            )
        ]

    async def execute_step(
        self,
        *,
        question: str,
        step: AnalysisStep,
        prior_queries: list[QueryTrace],
    ) -> StepExecution:
        query = QueryTrace(
            query_id="query_1",
            purpose="计算销售额",
            sql="SELECT 200 AS revenue",
            columns=["revenue"],
            row_count=1,
            preview=[{"revenue": 200}],
        )
        satisfied = not self.fail_once
        self.fail_once = False
        return StepExecution(
            queries=[query],
            condition_results=[
                CompletionConditionResult(
                    condition=step.completion_conditions[0],
                    satisfied=satisfied,
                    explanation=(
                        "标准口径查询返回销售额"
                        if satisfied
                        else "第一次执行未满足标准口径"
                    ),
                )
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
        return None

    async def synthesize(
        self,
        *,
        question: str,
        queries: list[QueryTrace],
    ) -> str:
        return "销售额为200元。"


class UnusedConversationAgent:
    async def invoke(self, **_: object) -> object:
        raise AssertionError("legacy message endpoint must not be invoked")


class AnalysisApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        root = Path(self._temporary_directory.name)
        config = Config(
            database_path=root / "business.db",
            agent_state_path=root / "state.db",
            knowledge_dir=root / "knowledge",
            model_api_key=SecretStr("test-key"),
        )
        self.analysis_agent = ApiAnalysisAgent()
        self.client_context = TestClient(
            create_app(
                config,
                analysis_agent_factory=lambda _thread_id: self.analysis_agent,
                agent_factory=lambda _config, _checkpointer, _memory: UnusedConversationAgent(),
            )
        )
        self.client = self.client_context.__enter__()
        self.addCleanup(self.client_context.__exit__, None, None, None)

    def test_create_reconnect_and_stream_a_durable_analysis_run(self) -> None:
        conversation = self.client.post("/api/conversations").json()
        response = self.client.post(
            f"/api/conversations/{conversation['conversation_id']}/analysis-runs",
            json={"message": "销售额是多少？"},
        )

        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run_id"]
        deadline = time.monotonic() + 2
        run: dict[str, object] = {}
        while time.monotonic() < deadline:
            state_response = self.client.get(f"/api/analysis-runs/{run_id}")
            self.assertEqual(state_response.status_code, 200)
            run = state_response.json()
            if run["status"] == "completed":
                break
            time.sleep(0.01)

        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["answer"], "销售额为200元。")
        self.assertEqual(run["plan"]["steps"][0]["status"], "completed")
        self.assertNotIn("evidence", run)
        self.assertNotIn("evidence_ids", run["plan"]["steps"][0])
        self.assertEqual(run["queries"][0]["preview"], [{"revenue": 200}])

        events = self.client.get(f"/api/analysis-runs/{run_id}/events")
        self.assertEqual(events.status_code, 200)
        self.assertTrue(events.headers["content-type"].startswith("text/event-stream"))
        self.assertIn("event: plan_published", events.text)
        self.assertIn("event: step_completed", events.text)
        self.assertIn("event: run_completed", events.text)

    def test_failed_step_can_be_retried_through_the_public_api(self) -> None:
        self.analysis_agent.fail_once = True
        conversation = self.client.post("/api/conversations").json()
        created = self.client.post(
            f"/api/conversations/{conversation['conversation_id']}/analysis-runs",
            json={"message": "销售额是多少？"},
        ).json()
        run_id = created["run_id"]

        failed = self._wait_for_status(run_id, "partial")
        self.assertEqual(failed["plan"]["steps"][0]["status"], "failed")
        self.assertEqual(failed["queries"][0]["preview"], [{"revenue": 200}])

        deadline = time.monotonic() + 2
        retry_response = self.client.post(f"/api/analysis-runs/{run_id}/retry")
        while retry_response.status_code == 409 and time.monotonic() < deadline:
            time.sleep(0.01)
            retry_response = self.client.post(f"/api/analysis-runs/{run_id}/retry")
        self.assertEqual(retry_response.status_code, 202)

        completed = self._wait_for_status(run_id, "completed")
        self.assertEqual(completed["answer"], "销售额为200元。")
        events = self.client.get(f"/api/analysis-runs/{run_id}/events").text
        self.assertIn("event: run_retried", events)

    def test_planning_failure_can_retry_before_a_plan_exists(self) -> None:
        self.analysis_agent.plan_fail_once = True
        conversation = self.client.post("/api/conversations").json()
        created = self.client.post(
            f"/api/conversations/{conversation['conversation_id']}/analysis-runs",
            json={"message": "销售额是多少？"},
        ).json()
        run_id = created["run_id"]

        failed = self._wait_for_status(run_id, "failed")
        self.assertIsNone(failed["plan"])

        deadline = time.monotonic() + 2
        retry_response = self.client.post(f"/api/analysis-runs/{run_id}/retry")
        while retry_response.status_code == 409 and time.monotonic() < deadline:
            time.sleep(0.01)
            retry_response = self.client.post(f"/api/analysis-runs/{run_id}/retry")
        self.assertEqual(retry_response.status_code, 202)

        completed = self._wait_for_status(run_id, "completed")
        self.assertEqual(completed["plan"]["steps"][0]["status"], "completed")
        events = self.client.get(f"/api/analysis-runs/{run_id}/events").text
        self.assertIn("event: run_retried", events)

    def _wait_for_status(self, run_id: str, expected: str) -> dict[str, object]:
        deadline = time.monotonic() + 2
        run: dict[str, object] = {}
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/analysis-runs/{run_id}")
            self.assertEqual(response.status_code, 200)
            run = response.json()
            if run["status"] == expected:
                return run
            time.sleep(0.01)
        self.fail(f"analysis run did not reach {expected}: {run}")


class DeepSeekConfigurationTests(unittest.TestCase):
    @patch("commerce_trace.api.Agent")
    @patch("commerce_trace.api.ChatDeepSeek")
    def test_thinking_is_disabled_for_structured_agent_workflows(
        self,
        chat_model: object,
        _agent: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = Config(
                database_path=root / "business.db",
                agent_state_path=root / "state.db",
                knowledge_dir=root / "knowledge",
                model_api_key=SecretStr("test-key"),
                model="deepseek-v4-flash",
            )
            with TestClient(
                create_app(
                    config,
                    analysis_agent_factory=lambda _thread_id: ApiAnalysisAgent(),
                )
            ):
                pass

        call = chat_model.call_args  # type: ignore[attr-defined]
        self.assertEqual(
            call.kwargs["extra_body"],
            {"thinking": {"type": "disabled"}},
        )


if __name__ == "__main__":
    unittest.main()
