import type {
  ApiError,
  ChatResponse,
  ConversationCreate,
  ConversationSummary,
  MessageHistory,
} from './types'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    let error: ApiError | undefined
    try {
      error = (await response.json()) as ApiError
    } catch {
      // A stable fallback keeps transport failures readable.
    }
    throw new Error(error?.message ?? `请求失败 (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function createConversation(): Promise<ConversationCreate> {
  return request('/api/conversations', { method: 'POST' })
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const payload = await request<{ items: ConversationSummary[] }>(
    '/api/conversations',
  )
  return payload.items
}

export function getConversationMessages(
  conversationId: string,
): Promise<MessageHistory> {
  return request(`/api/conversations/${conversationId}/messages`)
}

export function sendMessage(
  conversationId: string,
  message: string,
): Promise<ChatResponse> {
  return request(`/api/conversations/${conversationId}/messages`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  })
}

export function deleteConversation(conversationId: string): Promise<void> {
  return request(`/api/conversations/${conversationId}`, { method: 'DELETE' })
}
