import { FormEvent, lazy, Suspense, useEffect, useReducer, useState } from 'react'

import { listConversations, replayConversation, streamQuestion } from './api'
import { chatReducer, initialState, replayEvents } from './reducer'
import type { ConversationSummary } from './types'
import './styles.css'

const ChartView = lazy(async () => {
  const module = await import('./ChartView')
  return { default: module.ChartView }
})

function EvidenceTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (!rows.length) return <p className="muted">查询成功，未返回数据。</p>
  const columns = Object.keys(rows[0])
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, 50).map((row, index) => (
            <tr key={index}>
              {columns.map((column) => <td key={column}>{String(row[column] ?? '')}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function App() {
  const [state, dispatch] = useReducer(chatReducer, initialState)
  const [question, setQuestion] = useState('')
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [loadingHistory, setLoadingHistory] = useState(false)

  const refreshHistory = async () => {
    try {
      setConversations(await listConversations())
    } catch {
      // The chat remains usable if history is temporarily unavailable.
    }
  }

  useEffect(() => {
    void refreshHistory()
  }, [])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const value = question.trim()
    if (!value || state.status === 'working') return
    setQuestion('')
    dispatch({
      event_id: crypto.randomUUID(),
      event: 'conversation.started',
      conversation_id: state.conversationId ?? '',
      request_id: '',
      timestamp: new Date().toISOString(),
      payload: { question: value },
    })
    try {
      for await (const item of streamQuestion(value, state.conversationId)) {
        dispatch(item)
      }
      await refreshHistory()
    } catch (error) {
      dispatch({
        event_id: crypto.randomUUID(),
        event: 'request.failed',
        conversation_id: state.conversationId ?? '',
        request_id: '',
        timestamp: new Date().toISOString(),
        payload: {
          message: error instanceof Error ? error.message : '连接失败',
        },
      })
    }
  }

  const openConversation = async (conversationId: string) => {
    setLoadingHistory(true)
    try {
      const replay = await replayConversation(conversationId)
      const replayed = replayEvents(replay.events)
      dispatch({ type: 'hydrate', state: replayed })
    } finally {
      setLoadingHistory(false)
    }
  }

  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span className="brand-mark">商</span>
          <div><strong>CommerceTrace</strong><small>证据驱动的经营分析</small></div>
        </div>
        <button className="new-chat" onClick={() => window.location.reload()}>
          ＋ 新建分析
        </button>
        <p className="eyebrow">历史对话</p>
        <nav>
          {conversations.map((conversation) => (
            <button
              key={conversation.conversation_id}
              className="history-item"
              disabled={loadingHistory}
              onClick={() => void openConversation(conversation.conversation_id)}
            >
              <span>{conversation.title}</span>
              <time>{new Date(conversation.updated_at).toLocaleDateString('zh-CN')}</time>
            </button>
          ))}
        </nav>
      </aside>
      <main>
        <header>
          <div>
            <p className="eyebrow">LIVE ANALYSIS</p>
            <h1>中文电商经营分析</h1>
          </div>
          <div className={`status ${state.status}`}>
            <span />{state.statusMessage}
          </div>
        </header>
        <section className="workspace">
          <div className="conversation">
            {state.messages.length === 0 && (
              <div className="hero">
                <p className="eyebrow">TRACE EVERY NUMBER</p>
                <h2>让每个经营结论，都能回到证据。</h2>
                <p>试试“七月销售额为什么下降？”或“按地区展示销售额”。</p>
              </div>
            )}
            {state.messages.map((message) => (
              <article key={message.id} className={`message ${message.role}`}>
                <span>{message.role === 'user' ? '你' : '商迹'}</span>
                <div>{message.content}</div>
              </article>
            ))}
            {state.evidence.map((item) => (
              <details className="evidence-card" key={item.evidence_id}>
                <summary>
                  <span>证据 {item.evidence_id}</span>
                  <strong>{item.claim}</strong>
                </summary>
                <div className="evidence-meta">
                  <span>{item.row_count} 行</span>
                  <span>{item.execution_time_ms.toFixed(1)} ms</span>
                  <span>Hash {item.result_hash.slice(0, 12)}</span>
                </div>
                <EvidenceTable rows={item.preview} />
                <details className="sql">
                  <summary>查看只读 SQL</summary>
                  <pre>{item.sql}</pre>
                </details>
              </details>
            ))}
            <Suspense fallback={<p className="muted">正在加载图表…</p>}>
              {state.charts.map((chart) => <ChartView chart={chart} key={chart.chart_id} />)}
            </Suspense>
          </div>
          <aside className="trace-panel">
            <p className="eyebrow">分析轨迹</p>
            <ol className="plan">
              {state.plan.map((step) => (
                <li className={step.status} key={step.id}>
                  <i /> <span>{step.title}</span>
                </li>
              ))}
            </ol>
            {state.tools.length > 0 && <p className="eyebrow">工具状态</p>}
            <div className="tools">
              {state.tools.map((tool) => (
                <details key={tool.toolCallId} className={`tool ${tool.status}`}>
                  <summary><span>{tool.name}</span><b>{tool.status}</b></summary>
                  {tool.error && <p>{tool.error}</p>}
                </details>
              ))}
            </div>
          </aside>
        </section>
        <form onSubmit={(event) => void submit(event)}>
          <textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="输入一个中文经营问题…"
            rows={2}
            disabled={state.status === 'working'}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                event.currentTarget.form?.requestSubmit()
              }
            }}
          />
          <button disabled={!question.trim() || state.status === 'working'}>开始分析</button>
        </form>
      </main>
    </div>
  )
}
