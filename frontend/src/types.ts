export interface QueryTrace {
  query_id: string
  prepared_query_id?: string | null
  purpose: string
  sql: string
  plan: string[]
  semantic_fingerprint?: string | null
  columns: string[]
  row_count: number
  preview: Array<Record<string, unknown>>
  execution_time_ms: number
  truncated: boolean
}

export type AnalysisRunStatus =
  | 'queued'
  | 'planning'
  | 'running'
  | 'completed'
  | 'partial'
  | 'failed'

export type AnalysisStepStatus =
  | 'pending'
  | 'in_progress'
  | 'completed'
  | 'failed'
  | 'skipped'

export type AnalysisEventType =
  | 'run_created'
  | 'planning_started'
  | 'plan_published'
  | 'plan_revised'
  | 'step_started'
  | 'step_artifacts_recorded'
  | 'step_completed'
  | 'step_failed'
  | 'run_completed'
  | 'run_partial'
  | 'run_failed'
  | 'run_retried'

export interface AnalysisStep {
  step_id: string
  step_key: string
  depends_on: string[]
  title: string
  objective: string
  completion_conditions: string[]
  status: AnalysisStepStatus
  completion_results: Array<{
    condition: string
    satisfied: boolean
    explanation: string
  }>
  error?: string | null
}

export interface AnalysisPlan {
  revision: number
  revision_reason?: string | null
  steps: AnalysisStep[]
}

export interface AnalysisRun {
  run_id: string
  conversation_id: string
  question: string
  status: AnalysisRunStatus
  plan?: AnalysisPlan | null
  queries: QueryTrace[]
  charts: Chart[]
  answer?: string | null
  error?: string | null
  usage: Usage
  created_at: string
  updated_at: string
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
  status: 'idle' | 'working' | 'completed' | 'partial' | 'error'
  statusMessage: string
  activeRun?: AnalysisRun
}
