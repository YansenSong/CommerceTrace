import type { ConversationSummary, StreamEvent } from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export async function* streamQuestion(
  question: string,
  conversationId?: string,
): AsyncGenerator<StreamEvent> {
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      question,
      conversation_id: conversationId,
    }),
  })
  if (!response.ok || !response.body) {
    throw new Error(`请求失败：HTTP ${response.status}`)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const data = frame
        .split('\n')
        .find((line) => line.startsWith('data: '))
        ?.slice(6)
      if (data) {
        yield JSON.parse(data) as StreamEvent
      }
    }
    if (done) break
  }
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const response = await fetch(`${API_BASE}/api/conversations`, {
    credentials: 'include',
  })
  if (!response.ok) throw new Error('无法加载对话历史')
  const body = (await response.json()) as { items: ConversationSummary[] }
  return body.items
}

export async function replayConversation(
  conversationId: string,
): Promise<{ events: StreamEvent[] }> {
  const response = await fetch(`${API_BASE}/api/conversations/${conversationId}`, {
    credentials: 'include',
  })
  if (!response.ok) throw new Error('无法回放该对话')
  return response.json() as Promise<{ events: StreamEvent[] }>
}
