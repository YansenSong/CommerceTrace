import { applyEvent, initialState } from './reducer'
import type { StreamEvent } from './types'

function event(eventName: StreamEvent['event'], payload: Record<string, unknown>): StreamEvent {
  return {
    event_id: 'event-1',
    event: eventName,
    conversation_id: 'conv-1',
    request_id: 'req-1',
    timestamp: '2026-07-26T00:00:00Z',
    payload,
  }
}

describe('SSE reducer', () => {
  it('deduplicates repeated evidence events by event_id', () => {
    const item = event('evidence.created', {
      evidence_id: 'ev-1',
      analysis_step: '统计销售额',
      claim: '销售额为 100',
      sql: 'SELECT 100',
      columns: ['revenue'],
      row_count: 1,
      result_hash: 'hash',
      executed_at: '2026-07-26T00:00:00Z',
      preview: [{ revenue: 100 }],
    })
    const once = applyEvent(initialState, item)
    const twice = applyEvent(once, item)
    expect(twice.evidence).toHaveLength(1)
    expect(twice).toBe(once)
  })

  it('tracks a tool from running to completed', () => {
    const started = applyEvent(
      initialState,
      event('tool.started', { tool_call_id: 'tool-1', tool_name: 'run_sql' }),
    )
    const completed = applyEvent(started, {
      ...event('tool.completed', {
        tool_call_id: 'tool-1',
        tool_name: 'run_sql',
        data: { row_count: 1 },
      }),
      event_id: 'event-2',
    })
    expect(completed.tools[0]).toMatchObject({
      name: 'run_sql',
      status: 'completed',
    })
  })

  it('keeps messages, evidence, and charts grouped by request id', () => {
    const started = applyEvent(
      initialState,
      event('conversation.started', { question: '按地区展示销售额' }),
    )
    const withEvidence = applyEvent(started, {
      ...event('evidence.created', {
        evidence_id: 'ev-1',
        analysis_step: '按地区统计',
        claim: '西南销售额最高',
        sql: 'SELECT region, SUM(total_amount) FROM ecommerce.orders GROUP BY region',
        columns: ['region', 'revenue'],
        row_count: 5,
        result_hash: 'hash',
        execution_time_ms: 1,
        executed_at: '2026-07-26T00:00:00Z',
        preview: [{ region: '西南', revenue: 100 }],
      }),
      event_id: 'event-2',
    })
    const withChart = applyEvent(withEvidence, {
      ...event('chart.created', {
        chart_id: 'chart-1',
        evidence_id: 'ev-1',
        chart_type: 'bar',
        title: '各地区销售额',
        figure: { data: [] },
      }),
      event_id: 'event-3',
    })
    const answered = applyEvent(withChart, {
      ...event('answer.delta', { delta: '西南销售额最高。' }),
      event_id: 'event-4',
    })

    expect(answered.messages).toHaveLength(2)
    expect(answered.messages[0].requestId).toBe('req-1')
    expect(answered.messages[1].requestId).toBe('req-1')
    expect(answered.evidence[0].requestId).toBe('req-1')
    expect(answered.charts[0].requestId).toBe('req-1')
  })

  it('does not append an answer delta to an assistant message from another request', () => {
    const firstAnswer = applyEvent(
      {
        ...initialState,
        messages: [
          {
            id: 'assistant-first',
            requestId: 'req-1',
            role: 'assistant',
            content: '第一轮回答',
          },
        ],
      },
      {
        ...event('answer.delta', { delta: '第二轮回答' }),
        event_id: 'event-second-answer',
        request_id: 'req-2',
      },
    )

    expect(firstAnswer.messages).toHaveLength(2)
    expect(firstAnswer.messages[0].content).toBe('第一轮回答')
    expect(firstAnswer.messages[1]).toMatchObject({
      requestId: 'req-2',
      content: '第二轮回答',
    })
  })
})
