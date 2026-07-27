from commerce_trace.agent import Agent
from commerce_trace.agent.synthesis import synthesize
from commerce_trace.agent.tools import FakeSqlExecutor, build_default_registry
from commerce_trace.context import ContextAssembler
from commerce_trace.contracts import EventType, Evidence, LlmResponse, ToolCall
from commerce_trace.llm import LlmService
from commerce_trace.persistence import InMemoryStore
from commerce_trace.testing import ScriptedLlm


def test_synthesis_keeps_concrete_model_answer_and_rejects_unknown_evidence() -> None:
    evidence = Evidence(
        evidence_id="ev_known",
        analysis_step="按地区分析",
        tool_call_id="call-1",
        claim="按地区统计销售额：region=华东，revenue=1200",
        sql="SELECT region, SUM(total_amount) AS revenue FROM ecommerce.orders GROUP BY region",
        columns=["region", "revenue"],
        row_count=1,
        result_hash="result-hash",
        preview=[{"region": "华东", "revenue": 1200}],
    )

    answer = synthesize(
        [evidence],
        "华东销售额最高，为 1200。[ev_known] [ev_unknown]",
        None,
    )

    assert answer.startswith("结论：华东销售额最高，为 1200。[ev_known]")
    assert "[ev_unknown]" not in answer
    assert "数据表明当前问题可由" not in answer


def test_synthesis_removes_chart_id_markdown_image_reference() -> None:
    evidence = Evidence(
        evidence_id="ev_regions",
        analysis_step="按地区分析",
        tool_call_id="call-regions",
        claim="按地区统计销售额：region=西南，revenue=292422.49",
        sql="SELECT region, SUM(total_amount) AS revenue FROM ecommerce.orders GROUP BY region",
        columns=["region", "revenue"],
        row_count=5,
        result_hash="regions-hash",
        preview=[{"region": "西南", "revenue": 292422.49}],
    )

    answer = synthesize(
        [evidence],
        (
            "各地区销售额排名如下。[ev_regions]\n\n"
            "![各地区销售额对比](chart_b07c43a1d42b)"
        ),
        None,
    )

    assert "各地区销售额排名如下" in answer
    assert "![各地区销售额对比]" not in answer
    assert "chart_b07c43a1d42b" not in answer


def test_synthesis_keeps_internal_incomplete_reason_out_of_answer() -> None:
    answer = synthesize(
        [],
        "",
        "insufficient_evidence",
    )

    assert "insufficient_evidence" not in answer
    assert "分析已停止" not in answer
    assert "当前没有获得足够的可执行查询证据" in answer
    assert "请补充时间范围或明确需要分析的指标" in answer


async def test_greeting_goes_through_normal_agent_flow() -> None:
    store = InMemoryStore()
    executor = FakeSqlExecutor(rows=[{"revenue": 999_999.0}])
    agent = Agent(
        llm=ScriptedLlm(),
        registry=build_default_registry(executor=executor),
        context_assembler=ContextAssembler(),
        store=store,

    )

    events = [
        event
        async for event in agent.run(
            user_id="greeting-user",
            conversation_id="greeting-conversation",
            request_id="greeting-request",
            question="hi",
        )
    ]

    assert [event.event for event in events] == [
        EventType.CONVERSATION_STARTED,
        EventType.CONTEXT_RETRIEVED,
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
        EventType.EVIDENCE_CREATED,
        EventType.ANSWER_DELTA,
        EventType.ANSWER_COMPLETED,
    ]
    completed = events[-1]
    assert completed.payload["status"] == "completed"
    assert len(completed.payload["evidence_ids"]) == 1
    assert completed.payload["usage"]["llm_calls"] > 0


async def test_simple_question_streams_plan_tool_evidence_chart_answer_and_candidate() -> None:
    store = InMemoryStore()
    executor = FakeSqlExecutor(
        rows=[{"region": "华东", "revenue": 1200.0}, {"region": "华南", "revenue": 900.0}]
    )
    registry = build_default_registry(executor=executor)
    agent = Agent(
        llm=ScriptedLlm(),
        registry=registry,
        context_assembler=ContextAssembler(),
        store=store,

    )

    events = [
        event
        async for event in agent.run(
            user_id="user-1",
            conversation_id="conv-1",
            request_id="req-1",
            question="按地区展示销售额",
        )
    ]

    event_types = [event.event for event in events]
    assert event_types[0] is EventType.CONVERSATION_STARTED
    assert EventType.CONTEXT_RETRIEVED in event_types
    assert EventType.TOOL_COMPLETED in event_types
    assert EventType.EVIDENCE_CREATED in event_types
    assert event_types[-1] is EventType.ANSWER_COMPLETED
    completed = events[-1]
    assert completed.payload["evidence_ids"]
    assert "[ev_" in completed.payload["answer"]


async def test_retryable_sql_failure_is_corrected_within_budget() -> None:
    store = InMemoryStore()
    executor = FakeSqlExecutor(
        rows=[{"revenue": 1000.0}],
        failures=[RuntimeError("secret database detail")],
    )
    agent = Agent(
        llm=ScriptedLlm(),
        registry=build_default_registry(executor=executor),
        context_assembler=ContextAssembler(),
        store=store,

    )

    events = [
        event
        async for event in agent.run(
            user_id="user-retry",
            conversation_id="conv-retry",
            request_id="req-retry",
            question="总销售额是多少？",
        )
    ]

    assert sum(event.event is EventType.TOOL_FAILED for event in events) == 1
    completed = events[-1]
    assert completed.payload["status"] == "completed"
    assert completed.payload["usage"]["business_sql_calls"] == 2
    assert "secret database detail" not in str([event.model_dump() for event in events])


class RepeatingSqlLlm(LlmService):
    async def complete(self, messages, tools, system_prompt):  # type: ignore[no-untyped-def]
        return LlmResponse(
            tool_calls=[
                ToolCall(
                    name="run_sql",
                    arguments={
                        "sql": "SELECT COUNT(*) AS order_count FROM ecommerce.orders",
                        "purpose": "持续探索",
                        "expected_columns": ["order_count"],
                    },
                )
            ]
        )


async def test_business_sql_budget_returns_partial_evidence_and_stop_reason() -> None:
    store = InMemoryStore()
    agent = Agent(
        llm=RepeatingSqlLlm(),
        registry=build_default_registry(
            executor=FakeSqlExecutor(rows=[{"order_count": 3}]),
    
        ),
        context_assembler=ContextAssembler(),
        store=store,

        max_business_sql_calls=1,
    )

    events = [
        event
        async for event in agent.run(
            user_id="user-budget",
            conversation_id="conv-budget",
            request_id="req-budget",
            question="继续分析订单量",
        )
    ]

    completed = events[-1]
    assert completed.payload["status"] == "partial"
    assert completed.payload["stop_reason"] == "business_sql_limit"
    assert completed.payload["evidence_ids"]
    assert "business_sql_limit" not in completed.payload["answer"]
    assert "已达到本轮查询次数上限" in completed.payload["answer"]
