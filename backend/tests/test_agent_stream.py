from commerce_trace.agent import Agent
from commerce_trace.context import ContextAssembler
from commerce_trace.contracts import EventType, Evidence, LlmResponse, ToolCall
from commerce_trace.llm import LlmService, ScriptedLlm
from commerce_trace.memory import MemoryService
from commerce_trace.storage import InMemoryStore
from commerce_trace.tools import FakeSqlExecutor, build_default_registry


class CoverageGapLlm(LlmService):
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools, system_prompt):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            return LlmResponse(
                tool_calls=[
                    ToolCall(
                        name="run_sql",
                        arguments={
                            "sql": (
                                "SELECT COUNT(*) AS order_count, "
                                "MIN(ordered_at) AS min_date, "
                                "MAX(ordered_at) AS max_date "
                                "FROM ecommerce.orders"
                            ),
                            "purpose": "检查上个月订单量和数据覆盖范围",
                            "expected_columns": [
                                "order_count",
                                "min_date",
                                "max_date",
                                "last_month",
                            ],
                        },
                    )
                ]
            )
        return LlmResponse(content="结论：上个月的订单总量是 0。")


async def test_out_of_range_last_month_is_not_reported_as_real_zero() -> None:
    store = InMemoryStore()
    memory = MemoryService(store=store, schema_fingerprint="schema-v1", metric_versions={})
    agent = Agent(
        llm=CoverageGapLlm(),
        registry=build_default_registry(
            executor=FakeSqlExecutor(
                rows=[
                    {
                        "order_count": 0,
                        "min_date": "2025-01-01T17:33:27+00:00",
                        "max_date": "2025-09-30T04:07:35+00:00",
                        "last_month": "2026-06",
                    }
                ]
            ),
            memory=memory,
        ),
        context_assembler=ContextAssembler(memory=memory),
        store=store,
        memory=memory,
    )

    events = [
        event
        async for event in agent.run(
            user_id="coverage-user",
            conversation_id="coverage-conversation",
            request_id="coverage-request",
            question="上个月的订单总量是多少",
        )
    ]

    completed = events[-1]
    assert completed.payload["status"] == "partial"
    assert completed.payload["stop_reason"] == "data_coverage_gap"
    assert "当前数据无法回答上个月（2026年6月）的订单总量" in completed.payload["answer"]
    assert "不能说明实际订单总量为 0" in completed.payload["answer"]
    assert "数据表明当前问题可由" not in completed.payload["answer"]
    assert completed.payload["evidence_ids"]
    assert await store.list_memories() == []


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

    answer = Agent._synthesize(
        "哪个地区销售额最高？",
        [evidence],
        "华东销售额最高，为 1200。[ev_known] [ev_unknown]",
        None,
    )

    assert answer.startswith("结论：华东销售额最高，为 1200。[ev_known]")
    assert "[ev_unknown]" not in answer
    assert "数据表明当前问题可由" not in answer


def test_coverage_gap_omits_unadopted_exploration_evidence() -> None:
    coverage = Evidence(
        evidence_id="ev_coverage",
        analysis_step="检查数据范围",
        tool_call_id="call-coverage",
        claim=(
            "确认数据中的时间范围："
            "min_date=2025-01-01T00:00:00+00:00，"
            "max_date=2025-09-30T00:00:00+00:00"
        ),
        sql="SELECT MIN(ordered_at) AS min_date, MAX(ordered_at) AS max_date FROM ecommerce.orders",
        columns=["min_date", "max_date"],
        row_count=1,
        result_hash="coverage-hash",
        preview=[
            {
                "min_date": "2025-01-01T00:00:00+00:00",
                "max_date": "2025-09-30T00:00:00+00:00",
            }
        ],
    )
    exploration = Evidence(
        evidence_id="ev_exploration",
        analysis_step="探索订单月份",
        tool_call_id="call-exploration",
        claim="查询2025年9月（上个月）的订单总量：total_orders=35",
        sql="SELECT COUNT(*) AS total_orders FROM ecommerce.orders",
        columns=["total_orders"],
        row_count=1,
        result_hash="exploration-hash",
        preview=[{"total_orders": 35}],
    )

    answer = Agent._synthesize(
        "上个月的订单总量是多少",
        [coverage, exploration],
        "结论：上个月订单总量为 35。[ev_exploration]",
        "data_coverage_gap",
    )

    assert "当前数据无法回答上个月" in answer
    assert "确认数据中的时间范围" in answer
    assert "查询2025年9月（上个月）" not in answer
    assert "[ev_exploration]" not in answer


async def test_greeting_completes_without_llm_or_business_query() -> None:
    store = InMemoryStore()
    memory = MemoryService(store=store, schema_fingerprint="schema-v1", metric_versions={})
    executor = FakeSqlExecutor(rows=[{"revenue": 999_999.0}])
    agent = Agent(
        llm=ScriptedLlm(),
        registry=build_default_registry(executor=executor, memory=memory),
        context_assembler=ContextAssembler(memory=memory),
        store=store,
        memory=memory,
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
        EventType.ANSWER_DELTA,
        EventType.ANSWER_COMPLETED,
    ]
    completed = events[-1]
    assert completed.payload["status"] == "completed"
    assert completed.payload["intent"] == "greeting"
    assert completed.payload["evidence_ids"] == []
    assert completed.payload["usage"]["llm_calls"] == 0
    assert completed.payload["usage"]["business_sql_calls"] == 0
    assert "销售额：" not in completed.payload["answer"]
    assert await store.list_memories() == []


async def test_simple_question_streams_plan_tool_evidence_chart_answer_and_candidate() -> None:
    store = InMemoryStore()
    memory = MemoryService(
        store=store, schema_fingerprint="schema-v1", metric_versions={"revenue": "1"}
    )
    executor = FakeSqlExecutor(
        rows=[{"region": "华东", "revenue": 1200.0}, {"region": "华南", "revenue": 900.0}]
    )
    registry = build_default_registry(executor=executor, memory=memory)
    agent = Agent(
        llm=ScriptedLlm(),
        registry=registry,
        context_assembler=ContextAssembler(memory=memory),
        store=store,
        memory=memory,
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
    assert EventType.PLAN_CREATED in event_types
    assert EventType.TOOL_COMPLETED in event_types
    assert EventType.EVIDENCE_CREATED in event_types
    assert EventType.CHART_CREATED in event_types
    assert event_types[-1] is EventType.ANSWER_COMPLETED
    completed = events[-1]
    assert completed.payload["evidence_ids"]
    assert "[ev_" in completed.payload["answer"]
    assert len(await store.list_memories()) == 1


async def test_retryable_sql_failure_is_corrected_within_budget() -> None:
    store = InMemoryStore()
    memory = MemoryService(store=store, schema_fingerprint="schema-v1", metric_versions={})
    executor = FakeSqlExecutor(
        rows=[{"revenue": 1000.0}],
        failures=[RuntimeError("secret database detail")],
    )
    agent = Agent(
        llm=ScriptedLlm(),
        registry=build_default_registry(executor=executor, memory=memory),
        context_assembler=ContextAssembler(memory=memory),
        store=store,
        memory=memory,
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
    memory = MemoryService(store=store, schema_fingerprint="schema-v1", metric_versions={})
    agent = Agent(
        llm=RepeatingSqlLlm(),
        registry=build_default_registry(
            executor=FakeSqlExecutor(rows=[{"order_count": 3}]),
            memory=memory,
        ),
        context_assembler=ContextAssembler(memory=memory),
        store=store,
        memory=memory,
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
    assert completed.payload["unfinished_steps"]
