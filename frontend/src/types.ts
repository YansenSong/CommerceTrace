export type EventName =
  | 'conversation.started'
  | 'context.retrieved'
  | 'tool.started'
  | 'tool.completed'
  | 'tool.failed'
  | 'evidence.created'
  | 'chart.created'
  | 'answer.delta'
  | 'answer.completed'
  | 'request.failed'

export interface StreamEvent {
  event_id: string
  event: EventName
  conversation_id: string
  request_id: string
  timestamp: string
  payload: Record<string, unknown>
}

export interface Message {
  id: string
  requestId: string
  role: 'user' | 'assistant'
  content: string
}

export interface ToolState {
  toolCallId: string
  name: string
  status: 'running' | 'completed' | 'failed'
  data?: Record<string, unknown>
  error?: string
}

export interface Evidence {
  requestId: string
  evidence_id: string
  analysis_step: string
  claim: string
  sql: string
  columns: string[]
  row_count: number
  result_hash: string
  execution_time_ms: number
  executed_at: string
  preview: Array<Record<string, unknown>>
}

export interface Chart {
  requestId: string
  chart_id: string
  evidence_id: string
  chart_type: 'metric_card' | 'bar' | 'line' | 'pie'
  title: string
  figure: {
    data?: Plotly.Data[]
    layout?: Partial<Plotly.Layout>
  }
}

export interface ConversationSummary {
  conversation_id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ChatState {
  conversationId?: string
  requestId?: string
  messages: Message[]
  tools: ToolState[]
  evidence: Evidence[]
  charts: Chart[]
  seenEventIds: Set<string>
  status: 'idle' | 'working' | 'completed' | 'partial' | 'error'
  statusMessage: string
}
