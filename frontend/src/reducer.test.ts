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
})
