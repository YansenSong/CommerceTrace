export interface QueryTrace {
  query_id: string
  purpose: string
  sql: string
  columns: string[]
  row_count: number
  preview: Array<Record<string, unknown>>
  execution_time_ms: number
}

export interface Chart {
  chart_id: string
  source_query_id: string
  chart_type: 'metric_card' | 'bar' | 'line' | 'pie'
  title: string
  figure: {
    data?: Plotly.Data[]
    layout?: Partial<Plotly.Layout>
  }
}

export interface Usage {
  input_tokens: number
  output_tokens: number
}

export interface Message {
  message_id: number | string
  role: 'user' | 'assistant'
  content: string
  queries: QueryTrace[]
  charts: Chart[]
  usage: Usage
  created_at: string
}

export interface ConversationSummary {
  conversation_id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ConversationCreate extends ConversationSummary {}

export interface MessageHistory {
  conversation_id: string
  messages: Message[]
}

export interface ChatResponse {
  conversation_id: string
  answer: string
  queries: QueryTrace[]
  charts: Chart[]
  usage: Usage
}

export interface ApiError {
  code: string
  message: string
}

export interface KnowledgeEntry {
  slug: string
  question: string
  sqls: string[]
  created_at: string
  updated_at: string
  revision: number
  note?: string | null
}

export interface ChatState {
  conversationId?: string
  messages: Message[]
  status: 'idle' | 'working' | 'completed' | 'error'
  statusMessage: string
}
