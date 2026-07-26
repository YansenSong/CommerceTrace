import type {
  Chart,
  ChatState,
  Evidence,
  Message,
  StreamEvent,
  ToolState,
} from './types'

export const initialState: ChatState = {
  messages: [],
  tools: [],
  evidence: [],
  charts: [],
  seenEventIds: new Set(),
  status: 'idle',
  statusMessage: '准备就绪',
}

export type ChatAction =
  | StreamEvent
  | { type: 'hydrate'; state: ChatState }

function replaceTool(
  tools: ToolState[],
  toolCallId: string,
  update: Partial<ToolState>,
): ToolState[] {
  const index = tools.findIndex((tool) => tool.toolCallId === toolCallId)
  if (index < 0) return tools
  return tools.map((tool, toolIndex) =>
    toolIndex === index ? { ...tool, ...update } : tool,
  )
}

export function applyEvent(state: ChatState, event: StreamEvent): ChatState {
  if (state.seenEventIds.has(event.event_id)) return state
  const seenEventIds = new Set(state.seenEventIds)
  seenEventIds.add(event.event_id)
  const base = {
    ...state,
    conversationId: event.conversation_id,
    requestId: event.request_id,
    seenEventIds,
  }
  switch (event.event) {
    case 'conversation.started':
      {
        const question = String(event.payload.question ?? '')
        const last = state.messages.at(-1)
        const messages: Message[] =
          question && last?.role === 'user' && last.content === question
            ? state.messages.map((message, index) =>
                index === state.messages.length - 1
                  ? { ...message, requestId: event.request_id }
                  : message,
              )
            : question
              ? [
                  ...state.messages,
                  {
                    id: `user-${event.event_id}`,
                    requestId: event.request_id,
                    role: 'user',
                    content: question,
                  },
                ]
              : state.messages
      return {
        ...base,
        messages,
        status: 'working',
        statusMessage: '正在分析问题',
      }
      }
    case 'context.retrieved':
      return {
        ...base,
        statusMessage: event.payload.degraded
          ? '核心 Schema 已加载，记忆检索已降级'
          : 'Schema 与业务上下文已加载',
      }
    case 'tool.started':
      return {
        ...base,
        tools: [
          ...state.tools,
          {
            toolCallId: String(event.payload.tool_call_id),
            name: String(event.payload.tool_name),
            status: 'running',
          },
        ],
        statusMessage: `正在运行 ${String(event.payload.tool_name)}`,
      }
    case 'tool.completed':
      return {
        ...base,
        tools: replaceTool(
          state.tools,
          String(event.payload.tool_call_id),
          {
            status: 'completed',
            data: event.payload.data as Record<string, unknown>,
          },
        ),
        statusMessage: `${String(event.payload.tool_name)} 已完成`,
      }
    case 'tool.failed':
      return {
        ...base,
        tools: replaceTool(
          state.tools,
          String(event.payload.tool_call_id),
          {
            status: 'failed',
            error: String(event.payload.safe_error_message ?? '工具失败'),
          },
        ),
        statusMessage: '查询失败，Agent 正在判断是否修正',
      }
    case 'evidence.created':
      return {
        ...base,
        evidence: [
          ...state.evidence,
          {
            ...(event.payload as unknown as Evidence),
            requestId: event.request_id,
          },
        ],
      }
    case 'chart.created':
      return {
        ...base,
        charts: [
          ...state.charts,
          {
            ...(event.payload as unknown as Chart),
            requestId: event.request_id,
          },
        ],
      }
    case 'answer.delta': {
      const last = state.messages.at(-1)
      const delta = String(event.payload.delta ?? '')
      const messages: Message[] =
        last?.role === 'assistant' && last.requestId === event.request_id
          ? state.messages.map((message, index) =>
              index === state.messages.length - 1
                ? { ...message, content: message.content + delta }
                : message,
            )
          : [
              ...state.messages,
              {
                id: `assistant-${event.event_id}`,
                requestId: event.request_id,
                role: 'assistant',
                content: delta,
              },
            ]
      return { ...base, messages }
    }
    case 'answer.completed':
      return {
        ...base,
        status:
          event.payload.status === 'partial'
            ? 'partial'
            : event.payload.status === 'completed'
              ? 'completed'
              : 'idle',
        statusMessage:
          event.payload.status === 'partial' ? '已返回部分结果' : '分析完成',
      }
    case 'request.failed':
      return {
        ...base,
        status: 'error',
        statusMessage: String(event.payload.message ?? '请求失败'),
      }
  }
}

export function addUserMessage(state: ChatState, content: string): ChatState {
  return {
    ...state,
    messages: [
      ...state.messages,
      {
        id: `user-${crypto.randomUUID()}`,
        requestId: '',
        role: 'user',
        content,
      },
    ],
    status: 'working',
    statusMessage: '正在发送',
  }
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  if ('type' in action && action.type === 'hydrate') return action.state
  return applyEvent(state, action as StreamEvent)
}

export function replayEvents(events: StreamEvent[]): ChatState {
  return events.reduce(applyEvent, {
    ...initialState,
    seenEventIds: new Set<string>(),
  })
}
