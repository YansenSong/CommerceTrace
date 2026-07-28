import pytest

from commerce_trace.agent.state import RequestPhase, RequestState, ToolBudget
from commerce_trace.models import Evidence


def build_state() -> RequestState:
    return RequestState(
        user_id="user-1",
        conversation_id="conv-1",
        request_id="req-1",
        question="按地区展示销售额",
    )


def test_request_state_enforces_phase_order() -> None:
    state = build_state()

    with pytest.raises(
        ValueError,
        match="invalid request phase transition: started -> executing",
    ):
        state.prepare_execution()

    state.mark_context_ready()
    state.prepare_execution()
    state.begin_synthesis()
    state.finish(RequestPhase.COMPLETED)

    assert state.phase is RequestPhase.COMPLETED


def test_request_state_owns_budget_and_evidence_progress() -> None:
    state = build_state()
    state.mark_context_ready()
    state.prepare_execution()

    budgets = {
        "run_sql": ToolBudget(max_calls=1, max_retries_per_purpose=2),
    }
    assert state.begin_tool(
        name="run_sql",
        purpose="按地区统计销售额",
        max_tool_iterations=3,
        budgets=budgets,
    )
    state.add_evidence(
        Evidence(
            evidence_id="ev_regions",
            analysis_step="按地区统计销售额",
            tool_call_id="call-1",
            claim="按地区统计销售额",
            sql="SELECT region FROM ecommerce.customers",
            columns=["region"],
            row_count=1,
            result_hash="hash-1",
        )
    )

    assert not state.begin_tool(
        name="run_sql",
        purpose="补充地区统计",
        max_tool_iterations=3,
        budgets=budgets,
    )
    assert state.incomplete_reason == "run_sql_limit"
    assert state.tool_iterations == 1
    assert state.tool_call_counts["run_sql"] == 1
    assert [item.evidence_id for item in state.evidence] == ["ev_regions"]
