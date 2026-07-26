import pytest

from commerce_trace.agent.state import RequestPhase, RequestState
from commerce_trace.context import RetrievedContext
from commerce_trace.contracts import Evidence, PlanStep


def build_state() -> RequestState:
    return RequestState(
        user_id="user-1",
        conversation_id="conv-1",
        request_id="req-1",
        question="按地区展示销售额",
    )


def build_context() -> RetrievedContext:
    return RetrievedContext(
        schema_catalog={"version": "1"},
        schema_fingerprint="schema-v1",
        schema_version="1",
        knowledge_version="1",
    )


def test_request_state_enforces_phase_order() -> None:
    state = build_state()

    with pytest.raises(
        ValueError,
        match="invalid request phase transition: started -> executing",
    ):
        state.begin_execution()

    state.set_context(build_context())
    state.set_plan([PlanStep(id="step-1", title="执行经营指标查询")])
    state.begin_execution()
    state.begin_synthesis()
    state.finish(RequestPhase.COMPLETED)

    assert state.phase is RequestPhase.COMPLETED


def test_request_state_owns_budget_and_evidence_progress() -> None:
    state = build_state()
    state.set_context(build_context())
    state.set_plan([PlanStep(id="step-1", title="执行经营指标查询")])
    state.begin_execution()

    assert state.begin_current_step() is not None
    assert state.begin_tool(
        name="run_sql",
        purpose="按地区统计销售额",
        max_tool_iterations=3,
        max_business_sql_calls=1,
        max_sql_retries_per_purpose=2,
    )
    state.add_evidence(
        Evidence(
            evidence_id="ev_regions",
            analysis_step="执行经营指标查询",
            tool_call_id="call-1",
            claim="按地区统计销售额",
            sql="SELECT region FROM ecommerce.customers",
            columns=["region"],
            row_count=1,
            result_hash="hash-1",
        )
    )
    state.complete_current_step()

    assert not state.begin_tool(
        name="run_sql",
        purpose="补充地区统计",
        max_tool_iterations=3,
        max_business_sql_calls=1,
        max_sql_retries_per_purpose=2,
    )
    assert state.incomplete_reason == "business_sql_limit"
    assert state.tool_iterations == 1
    assert state.sql_calls == 1
    assert [item.evidence_id for item in state.evidence] == ["ev_regions"]
    assert state.plan[0].status == "completed"
