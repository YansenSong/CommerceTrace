import {
  FormEvent,
  lazy,
  ReactNode,
  Suspense,
  useEffect,
  useRef,
  useState,
} from 'react'

import {
  confirmKnowledge,
  createAnalysisRun,
  createConversation,
  deleteConversation,
  getAnalysisRun,
  getConversationMessages,
  getLatestAnalysisRun,
  listConversations,
  openAnalysisEventStream,
  retryAnalysisRun,
} from './api'
import type {
  AnalysisRun,
  AnalysisEventType,
  Chart,
  ChatState,
  ConversationSummary,
  Message,
  QueryTrace,
} from './types'
import './styles.css'

const ChartView = lazy(async () => {
  const module = await import('./ChartView')
  return { default: module.ChartView }
})

type IconName =
  | 'arrow'
  | 'chart'
  | 'chevron'
  | 'clock'
  | 'close'
  | 'database'
  | 'menu'
  | 'message'
  | 'plus'
  | 'send'
  | 'spark'
  | 'trace'

function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    arrow: <path d="m9 18 6-6-6-6" />,
    chart: (
      <>
        <path d="M3 3v18h18" />
        <path d="m7 16 4-5 4 3 5-7" />
      </>
    ),
    chevron: <path d="m9 18 6-6-6-6" />,
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    close: (
      <>
        <path d="m6 6 12 12" />
        <path d="m18 6-12 12" />
      </>
    ),
    database: (
      <>
        <ellipse cx="12" cy="5" rx="8" ry="3" />
        <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
        <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
      </>
    ),
    menu: (
      <>
        <path d="M4 7h16" />
        <path d="M4 12h16" />
        <path d="M4 17h16" />
      </>
    ),
    message: (
      <>
        <path d="M20 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h9a4 4 0 0 1 4 4Z" />
        <path d="M8 9h8M8 13h5" />
      </>
    ),
    plus: (
      <>
        <path d="M12 5v14" />
        <path d="M5 12h14" />
      </>
    ),
    send: (
      <>
        <path d="m22 2-7 20-4-9-9-4Z" />
        <path d="M22 2 11 13" />
      </>
    ),
    spark: (
      <>
        <path d="m12 3 1.4 4.1L17.5 9l-4.1 1.9L12 15l-1.4-4.1L6.5 9l4.1-1.9Z" />
        <path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8Z" />
      </>
    ),
    trace: (
      <>
        <circle cx="6" cy="6" r="2" />
        <circle cx="18" cy="12" r="2" />
        <circle cx="8" cy="19" r="2" />
        <path d="M8 6h3a3 3 0 0 1 3 3v0a3 3 0 0 0 3 3M16.5 13.5l-7 4" />
      </>
    ),
  }

  return (
    <svg
      aria-hidden="true"
      className="icon"
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
    >
      <g stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8">
        {paths[name]}
      </g>
    </svg>
  )
}

function renderInline(text: string) {
  const tokenPattern = /(\*\*[^*]+\*\*|`[^`]+`)/g
  return text.split(tokenPattern).map((part, index) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) {
      return <strong key={`strong-${index}`}>{part.slice(2, -2)}</strong>
    }
    if (/^`[^`]+`$/.test(part)) {
      return <code className="inline-code" key={`code-${index}`}>{part.slice(1, -1)}</code>
    }
    return part
  })
}

function markdownLines(content: string) {
  const sanitized = content.replace(
    /!\[[^\]\n]*\]\(\s*chart_[A-Za-z0-9_-]+\s*\)/g,
    '',
  )
  const lines = sanitized.replace(/\r\n?/g, '\n').split('\n')
  return lines.filter((line, index) => {
    if (line.trim()) return true
    const previous = lines[index - 1]?.trim() ?? ''
    const next = lines[index + 1]?.trim() ?? ''
    return !(previous.startsWith('|') && next.startsWith('|'))
  })
}

function tableCells(line: string) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function isTableDivider(line: string) {
  const cells = tableCells(line)
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell))
}

function isNumericCell(value: string) {
  return /^[-+]?[\d,.]+(?:%|元|万|亿)?$/.test(value.replace(/\s/g, ''))
}

function MarkdownBlocks({ lines }: { lines: string[] }) {
  const blocks: ReactNode[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index].trim()
    if (!line) {
      index += 1
      continue
    }

    if (
      line.startsWith('|') &&
      lines[index + 1]?.trim().startsWith('|') &&
      isTableDivider(lines[index + 1])
    ) {
      const headers = tableCells(line)
      const alignments = tableCells(lines[index + 1]).map((cell) =>
        cell.endsWith(':') ? 'numeric' : '',
      )
      const rows: string[][] = []
      index += 2
      while (index < lines.length && lines[index].trim().startsWith('|')) {
        rows.push(tableCells(lines[index]))
        index += 1
      }
      blocks.push(
        <div className="answer-table-wrap" key={`table-${index}`}>
          <table className="answer-table">
            <thead>
              <tr>
                {headers.map((header, columnIndex) => (
                  <th className={alignments[columnIndex]} key={`${header}-${columnIndex}`}>
                    {renderInline(header)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`row-${rowIndex}`}>
                  {headers.map((_, columnIndex) => {
                    const value = row[columnIndex] ?? ''
                    const cellClass =
                      alignments[columnIndex] || isNumericCell(value) ? 'numeric' : ''
                    return (
                      <td className={cellClass} key={`cell-${rowIndex}-${columnIndex}`}>
                        {renderInline(value)}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    const codeFence = line.match(/^```([A-Za-z0-9_-]*)/)
    if (codeFence) {
      const codeLines: string[] = []
      index += 1
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        codeLines.push(lines[index])
        index += 1
      }
      index += 1
      blocks.push(
        <pre className="markdown-code" key={`code-block-${index}`}>
          {codeFence[1] && <span>{codeFence[1]}</span>}
          <code>{codeLines.join('\n')}</code>
        </pre>,
      )
      continue
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      const HeadingTag = heading[1].length <= 2 ? 'h3' : 'h4'
      blocks.push(
        <HeadingTag className="markdown-heading" key={`heading-${index}`}>
          {renderInline(heading[2])}
        </HeadingTag>,
      )
      index += 1
      continue
    }

    const method = line.match(/^\*\*口径说明\*\*[：:]\s*(.*)$/)
    if (method) {
      blocks.push(
        <aside className="report-method" key={`report-method-${index}`}>
          <strong>口径说明</strong>
          <span>{renderInline(method[1])}</span>
        </aside>,
      )
      index += 1
      continue
    }

    if (/^[-*]\s+/.test(line)) {
      const items: string[] = []
      while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^[-*]\s+/, ''))
        index += 1
      }
      blocks.push(
        <ul className="markdown-list" key={`list-${index}`}>
          {items.map((item, itemIndex) => (
            <li key={`item-${itemIndex}`}>{renderInline(item)}</li>
          ))}
        </ul>,
      )
      continue
    }

    const orderedItem = line.match(/^\d+[.)]\s+(.+)$/)
    if (orderedItem) {
      const items: string[] = []
      while (index < lines.length) {
        const item = lines[index].trim().match(/^\d+[.)]\s+(.+)$/)
        if (!item) break
        items.push(item[1])
        index += 1
      }
      blocks.push(
        <ol className="markdown-list" key={`ordered-list-${index}`}>
          {items.map((item, itemIndex) => (
            <li key={`ordered-item-${itemIndex}`}>{renderInline(item)}</li>
          ))}
        </ol>,
      )
      continue
    }

    blocks.push(
      <p className="markdown-paragraph" key={`paragraph-${index}`}>
        {renderInline(line)}
      </p>,
    )
    index += 1
  }

  return <div className="markdown-blocks">{blocks}</div>
}

function sectionMarker(line: string, label: string) {
  const value = line.trim().replace(/^#{1,4}\s*/, '')
  return value === label || value === `${label}：` || value === `${label}:`
}

export function AssistantContent({ content }: { content: string }) {
  let lines = markdownLines(content)
  const firstContentIndex = lines.findIndex((line) => line.trim())
  let hasConclusion = false

  if (firstContentIndex >= 0) {
    const firstLine = lines[firstContentIndex].trim()
    const conclusionPrefix = firstLine.match(/^结论[：:]\s*(.*)$/)
    if (conclusionPrefix) {
      hasConclusion = true
      lines =
        conclusionPrefix[1]
          ? lines.map((line, index) =>
              index === firstContentIndex ? conclusionPrefix[1] : line,
            )
          : lines.filter((_, index) => index !== firstContentIndex)
    }
  }

  if (lines.some((line) => sectionMarker(line, '结论'))) {
    hasConclusion = true
    lines = lines.filter((line) => !sectionMarker(line, '结论'))
  }

  const basisIndex = lines.findIndex((line) => sectionMarker(line, '数据依据'))
  const methodIndex = lines.findIndex(
    (line, index) => index > basisIndex && /^口径说明[：:]/.test(line.trim()),
  )
  const answerEnd =
    basisIndex >= 0 ? basisIndex : methodIndex >= 0 ? methodIndex : lines.length
  const answerLines = lines.slice(0, answerEnd)
  const basisLines =
    basisIndex >= 0
      ? lines.slice(basisIndex + 1, methodIndex >= 0 ? methodIndex : lines.length)
      : []
  const methodText =
    methodIndex >= 0 ? lines[methodIndex].trim().replace(/^口径说明[：:]\s*/, '') : ''

  return (
    <div className="answer-content">
      {hasConclusion ? (
        <section className="answer-report">
          <span className="answer-label">结论</span>
          <MarkdownBlocks lines={answerLines} />
        </section>
      ) : (
        <MarkdownBlocks lines={answerLines} />
      )}

      {basisLines.some((line) => line.trim()) && (
        <section className="answer-basis">
          <h3 className="answer-section-title">数据依据</h3>
          <MarkdownBlocks lines={basisLines} />
        </section>
      )}

      {methodText && (
        <aside className="method-note">
          <strong>回答边界</strong>
          <span>{renderInline(methodText)}</span>
        </aside>
      )}
    </div>
  )
}

function QueryResultTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (!rows.length) {
    return (
      <div className="empty-table">
        <Icon name="database" size={18} />
        查询已执行，本次没有返回数据
      </div>
    )
  }
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

function QueryResultSection({
  queries,
  headingId,
}: {
  queries: QueryTrace[]
  headingId: string
}) {
  if (!queries.length) return null

  return (
    <section className="query-section" aria-labelledby={headingId}>
      <div className="section-heading">
        <div>
          <span className="section-icon"><Icon name="database" size={18} /></span>
          <div>
            <h2 id={headingId}>查询结果</h2>
            <p>{queries.length} 条已执行查询，可展开查看</p>
          </div>
        </div>
        <span className="verified-badge">已执行</span>
      </div>

      <div className="query-list">
        {queries.map((item) => (
          <details className="query-card" key={item.query_id}>
            <summary>
              <div className="query-summary">
                <code>{item.query_id}</code>
                <strong>{item.purpose}</strong>
                <span>
                  {item.row_count} 行
                  <i />
                  {item.execution_time_ms.toFixed(1)} ms
                </span>
              </div>
              <span className="detail-chevron"><Icon name="chevron" size={18} /></span>
            </summary>
            <div className="query-detail">
              <QueryResultTable rows={item.preview} />
              <details className="sql">
                <summary>
                  <span>查看只读 SQL</span>
                  <Icon name="chevron" size={16} />
                </summary>
                <pre><code>{item.sql}</code></pre>
              </details>
              <div className="query-meta">
                <span>{item.columns.length} 个字段</span>
                <span>仅保存前 20 行预览</span>
              </div>
            </div>
          </details>
        ))}
      </div>
    </section>
  )
}

function ChartCard({ chart }: { chart: Chart }) {
  return (
    <section className="chart-card">
      <div className="chart-heading">
        <span><Icon name="chart" size={18} /></span>
        <div><h2>{chart.title}</h2><p>数据来源 {chart.source_query_id}</p></div>
      </div>
      <ChartView chart={chart} />
    </section>
  )
}

function formatHistoryDate(value: string) {
  const date = new Date(value)
  const today = new Date()
  if (date.toDateString() === today.toDateString()) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

const suggestions = [
  '上个月的订单总量是多少？',
  '按地区展示销售额',
  '最近三个月销售趋势如何？',
]

const terminalRunStatuses = new Set(['completed', 'partial', 'failed'])

function statusForRun(run: AnalysisRun): Pick<ChatState, 'status' | 'statusMessage'> {
  const activeStep = run.plan?.steps.find((step) => step.status === 'in_progress')
  if (run.status === 'completed') {
    return { status: 'completed', statusMessage: '分析完成' }
  }
  if (run.status === 'partial') {
    return { status: 'partial', statusMessage: '部分完成' }
  }
  if (run.status === 'failed') {
    return { status: 'error', statusMessage: run.error ?? '分析失败' }
  }
  if (run.status === 'planning') {
    return { status: 'working', statusMessage: '正在生成分析计划' }
  }
  return {
    status: 'working',
    statusMessage: activeStep ? `正在执行：${activeStep.title}` : '等待执行分析计划',
  }
}

function AnalysisPlanStatus({
  run,
  onRetry,
}: {
  run: AnalysisRun
  onRetry: () => void
}) {
  if (!run.plan) {
    if (run.status !== 'failed') return null
    return (
      <section className="analysis-plan-card" aria-label="分析计划">
        <div className="analysis-plan-heading">
          <div>
            <span>ANALYSIS PLAN</span>
            <h2>分析计划生成失败</h2>
          </div>
        </div>
        <p className="plan-revision">尚未执行任何数据查询，可重新生成计划。</p>
        <button className="plan-retry" type="button" onClick={onRetry}>
          重新生成分析计划
        </button>
      </section>
    )
  }
  return (
    <section className="analysis-plan-card" aria-label="分析计划">
      <div className="analysis-plan-heading">
        <div>
          <span>ANALYSIS PLAN</span>
          <h2>分析计划</h2>
        </div>
        <small>修订 {run.plan.revision}</small>
      </div>
      {run.plan.revision_reason && (
        <p className="plan-revision">计划调整：{run.plan.revision_reason}</p>
      )}
      <ol className="plan">
        {run.plan.steps.map((step, index) => (
          <li className={step.status} key={step.step_id}>
            <div className="plan-rail">
              <span>{step.status === 'completed' ? '✓' : index + 1}</span>
              <i />
            </div>
            <div>
              <strong>{step.title}</strong>
              <small>
                {step.status === 'failed'
                  ? step.error ?? '步骤失败'
                  : step.completion_conditions.join('；')}
              </small>
            </div>
          </li>
        ))}
      </ol>
      {(run.status === 'failed' || run.status === 'partial') &&
        run.plan.steps.some((step) => step.status === 'failed') && (
        <button className="plan-retry" type="button" onClick={onRetry}>
          重试失败步骤
        </button>
      )}
    </section>
  )
}

const emptyState: ChatState = {
  messages: [],
  status: 'idle',
  statusMessage: '准备就绪',
}

export default function App() {
  const [state, setState] = useState<ChatState>(emptyState)
  const [question, setQuestion] = useState('')
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set())
  const [confirming, setConfirming] = useState<Set<string>>(new Set())
  const conversationEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
  const completedRunIdsRef = useRef<Set<string>>(new Set())

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

  useEffect(() => () => eventSourceRef.current?.close(), [])

  useEffect(() => {
    conversationEndRef.current?.scrollIntoView({
      behavior: state.status === 'working' ? 'smooth' : 'auto',
      block: 'end',
    })
  }, [state.messages, state.status])

  const finishRun = (run: AnalysisRun) => {
    eventSourceRef.current?.close()
    eventSourceRef.current = null
    setState((current) => {
      const runStatus = statusForRun(run)
      if (!run.answer || completedRunIdsRef.current.has(run.run_id)) {
        return { ...current, ...runStatus, activeRun: run }
      }
      completedRunIdsRef.current.add(run.run_id)
      const assistantMessage: Message = {
        message_id: `assistant-${run.run_id}`,
        role: 'assistant',
        content: run.answer,
        queries: run.queries,
        charts: run.charts,
        usage: run.usage,
        created_at: run.updated_at,
      }
      return {
        ...current,
        ...runStatus,
        activeRun: run,
        messages: [...current.messages, assistantMessage],
      }
    })
    void refreshHistory()
  }

  const watchRun = (runId: string) => {
    eventSourceRef.current?.close()
    const source = openAnalysisEventStream(runId)
    eventSourceRef.current = source

    const refreshRun = async () => {
      try {
        const run = await getAnalysisRun(runId)
        if (terminalRunStatuses.has(run.status)) {
          finishRun(run)
          return
        }
        setState((current) => ({
          ...current,
          ...statusForRun(run),
          activeRun: run,
        }))
      } catch (error) {
        setState((current) => ({
          ...current,
          status: 'error',
          statusMessage: error instanceof Error ? error.message : '任务恢复失败',
        }))
      }
    }
    ;([
      'planning_started',
      'plan_published',
      'plan_revised',
      'step_started',
      'step_completed',
      'step_failed',
      'run_completed',
      'run_partial',
      'run_failed',
    ] satisfies AnalysisEventType[]).forEach((eventType) =>
      source.addEventListener(eventType, () => void refreshRun()),
    )
    source.onerror = () => {
      setState((current) => ({
        ...current,
        statusMessage: '连接中断，正在恢复分析进度',
      }))
    }
    void refreshRun()
  }

  const retryRun = async () => {
    const runId = state.activeRun?.run_id
    if (!runId) return
    try {
      const run = await retryAnalysisRun(runId)
      setState((current) => ({
        ...current,
        ...statusForRun(run),
        activeRun: run,
      }))
      watchRun(run.run_id)
    } catch (error) {
      setState((current) => ({
        ...current,
        status: 'error',
        statusMessage: error instanceof Error ? error.message : '重试失败',
      }))
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const value = question.trim()
    if (!value || state.status === 'working') return
    setQuestion('')
    let conversationId = state.conversationId
    try {
      if (!conversationId) {
        const conversation = await createConversation()
        conversationId = conversation.conversation_id
      }
      const userMessage: Message = {
        message_id: `pending-${crypto.randomUUID()}`,
        role: 'user',
        content: value,
        queries: [],
        charts: [],
        usage: { input_tokens: 0, output_tokens: 0 },
        created_at: new Date().toISOString(),
      }
      setState((current) => ({
        ...current,
        conversationId,
        messages: [...current.messages, userMessage],
        status: 'working',
        statusMessage: '正在分析问题',
      }))
      const run = await createAnalysisRun(conversationId, value)
      setState((current) => ({
        ...current,
        conversationId,
        ...statusForRun(run),
        activeRun: run,
      }))
      watchRun(run.run_id)
    } catch (error) {
      setState((current) => ({
        ...current,
        conversationId,
        status: 'error',
        statusMessage: error instanceof Error ? error.message : '连接失败',
      }))
    }
  }

  const startNewChat = async () => {
    try {
      eventSourceRef.current?.close()
      eventSourceRef.current = null
      const conversation = await createConversation()
      setState({
        ...emptyState,
        conversationId: conversation.conversation_id,
      })
      setQuestion('')
      setSidebarOpen(false)
      await refreshHistory()
      window.setTimeout(() => textareaRef.current?.focus(), 0)
    } catch (error) {
      setState((current) => ({
        ...current,
        status: 'error',
        statusMessage: error instanceof Error ? error.message : '新建会话失败',
      }))
    }
  }

  const openConversation = async (conversationId: string) => {
    setLoadingHistory(true)
    try {
      eventSourceRef.current?.close()
      eventSourceRef.current = null
      const history = await getConversationMessages(conversationId)
      let latestRun: AnalysisRun | undefined
      try {
        latestRun = await getLatestAnalysisRun(conversationId)
      } catch {
        // Conversations created before durable analysis runs have no run state.
      }
      setState({
        conversationId,
        messages: history.messages,
        ...(latestRun
          ? { ...statusForRun(latestRun), activeRun: latestRun }
          : { status: 'idle' as const, statusMessage: '会话已加载' }),
      })
      if (latestRun && !terminalRunStatuses.has(latestRun.status)) {
        watchRun(latestRun.run_id)
      }
      setSidebarOpen(false)
    } catch (error) {
      setState((current) => ({
        ...current,
        status: 'error',
        statusMessage: error instanceof Error ? error.message : '加载会话失败',
      }))
    } finally {
      setLoadingHistory(false)
    }
  }

  const removeConversation = async (conversationId: string) => {
    if (!window.confirm('永久删除这个会话？此操作无法撤销。')) return
    try {
      await deleteConversation(conversationId)
      if (state.conversationId === conversationId) setState(emptyState)
      await refreshHistory()
    } catch (error) {
      setState((current) => ({
        ...current,
        status: 'error',
        statusMessage: error instanceof Error ? error.message : '删除会话失败',
      }))
    }
  }

  const askSuggestion = (suggestion: string) => {
    setQuestion(suggestion)
    window.setTimeout(() => textareaRef.current?.focus(), 0)
  }

  const handleConfirm = async (message: Message) => {
    const messageIndex = state.messages.indexOf(message)
    if (messageIndex < 0) return
    const userMessages = state.messages
      .slice(0, messageIndex)
      .filter((item) => item.role === 'user')
    const question = userMessages.at(-1)?.content ?? message.content
    const sqls = message.queries.map((query) => query.sql).filter((sql) => sql.trim())
    if (!sqls.length) return
    const messageKey = String(message.message_id)
    setConfirming((prev) => new Set(prev).add(messageKey))
    try {
      await confirmKnowledge(question, sqls)
      setConfirmed((prev) => new Set(prev).add(messageKey))
    } catch (error) {
      setState((current) => ({
        ...current,
        status: 'error',
        statusMessage: error instanceof Error ? error.message : '确认失败',
      }))
    } finally {
      setConfirming((prev) => {
        const next = new Set(prev)
        next.delete(messageKey)
        return next
      })
    }
  }

  const lastMessage = state.messages.at(-1)
  const isWaitingForAnswer = state.status === 'working' && lastMessage?.role === 'user'

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>

      <button
        aria-label="关闭对话历史"
        className={`drawer-scrim ${sidebarOpen ? 'is-visible' : ''}`}
        onClick={() => setSidebarOpen(false)}
      />

      <aside className={`sidebar ${sidebarOpen ? 'is-open' : ''}`}>
        <div className="sidebar-top">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">商</span>
            <div>
              <strong>CommerceTrace</strong>
              <small>商迹 · 经营分析智能体</small>
            </div>
          </div>
          <button
            aria-label="关闭对话历史"
            className="icon-button sidebar-close"
            onClick={() => setSidebarOpen(false)}
          >
            <Icon name="close" />
          </button>
        </div>

        <button className="new-chat" onClick={() => void startNewChat()}>
          <Icon name="plus" size={18} />
          <span>新建分析</span>
          <kbd>⌘ K</kbd>
        </button>

        <div className="history-heading">
          <span>历史对话</span>
          <span>{conversations.length}</span>
        </div>
        <nav aria-label="历史对话">
          {conversations.length === 0 && (
            <div className="history-empty">
              <Icon name="message" size={18} />
              <span>完成一次分析后，对话会保存在这里</span>
            </div>
          )}
          {conversations.map((conversation) => (
            <div className="history-row" key={conversation.conversation_id}>
              <button
                aria-current={
                  state.conversationId === conversation.conversation_id ? 'page' : undefined
                }
                className="history-item"
                disabled={loadingHistory}
                onClick={() => void openConversation(conversation.conversation_id)}
              >
                <span>{conversation.title}</span>
                <time dateTime={conversation.updated_at}>
                  {formatHistoryDate(conversation.updated_at)}
                </time>
              </button>
              <button
                aria-label={`删除会话：${conversation.title}`}
                className="history-delete"
                disabled={loadingHistory}
                onClick={() => void removeConversation(conversation.conversation_id)}
              >
                <Icon name="close" size={14} />
              </button>
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <span className="privacy-icon"><Icon name="database" size={16} /></span>
          <p><strong>本地数据连接</strong><small>只读查询 · 全程可追溯</small></p>
        </div>
      </aside>

      <main id="main-content">
        <header className="topbar">
          <div className="topbar-title">
            <button
              aria-label="打开对话历史"
              className="icon-button menu-button"
              onClick={() => setSidebarOpen(true)}
            >
              <Icon name="menu" />
            </button>
            <div>
              <span className="mobile-brand">商迹</span>
              <h1>{state.messages.length ? '经营分析对话' : '经营分析助手'}</h1>
            </div>
          </div>
          <div className="topbar-actions">
            <div className={`status ${state.status}`} role="status" aria-live="polite">
              <span className="status-dot" />
              <span>{state.statusMessage}</span>
            </div>
          </div>
        </header>

        <section className="workspace">
          <div className="conversation" aria-live="polite">
            {state.messages.length === 0 && (
              <div className="hero">
                <div className="hero-symbol" aria-hidden="true">
                  <Icon name="spark" size={26} />
                </div>
                <p className="overline">TRACEABLE ANALYTICS</p>
                <h2>从经营问题出发，<br />让数据给出答案。</h2>
                <p className="hero-copy">
                  用自然语言询问订单、销售额和经营趋势。商迹会执行只读查询，
                  并保留可查看的查询结果和 SQL。
                </p>
                <div className="suggestions" aria-label="推荐问题">
                  {suggestions.map((suggestion) => (
                    <button key={suggestion} onClick={() => askSuggestion(suggestion)}>
                      <span>{suggestion}</span>
                      <Icon name="arrow" size={17} />
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="message-list">
              {state.messages.map((message) => {
                const replyQueries =
                  message.role === 'assistant' ? message.queries : []
                const replyCharts =
                  message.role === 'assistant' ? message.charts : []
                const headingId = `queries-${message.message_id}`

                return (
                  <article key={message.message_id} className={`message ${message.role}`}>
                    <div className="message-identity">
                      <span className="avatar" aria-hidden="true">
                        {message.role === 'user' ? '你' : '商'}
                      </span>
                      <span>{message.role === 'user' ? '你' : '商迹'}</span>
                    </div>
                    <div className="message-body">
                      {message.role === 'assistant' ? (
                        <AssistantContent content={message.content} />
                      ) : (
                        <p>{message.content}</p>
                      )}
                    </div>
                    {message.role === 'assistant' &&
                      (replyQueries.length > 0 || replyCharts.length > 0) && (
                        <div className="message-artifacts">
                          <QueryResultSection queries={replyQueries} headingId={headingId} />
                          <Suspense
                            fallback={<div className="chart-skeleton">正在准备可视化…</div>}
                          >
                            {replyCharts.map((chart) => (
                              <ChartCard chart={chart} key={chart.chart_id} />
                            ))}
                          </Suspense>
                        </div>
                      )}
                    {message.role === 'assistant' && replyQueries.length > 0 && (
                      <div className="confirm-row">
                        <button
                          type="button"
                          className={`confirm-button${confirmed.has(String(message.message_id)) ? ' is-confirmed' : ''}`}
                          disabled={confirming.has(String(message.message_id))}
                          onClick={() => void handleConfirm(message)}
                        >
                          <span className="confirm-mark" aria-hidden="true">
                            {confirmed.has(String(message.message_id)) ? '✓' : '✦'}
                          </span>
                          {confirmed.has(String(message.message_id))
                            ? '已确认，可再次确认以更新'
                            : confirming.has(String(message.message_id))
                              ? '确认中…'
                              : '确认此问答'}
                        </button>
                      </div>
                    )}
                  </article>
                )
              })}

              {isWaitingForAnswer && (
                <div className="thinking" role="status">
                  <span className="avatar" aria-hidden="true">商</span>
                  <div>
                    <span /><span /><span />
                  </div>
                  <p>{state.statusMessage}</p>
                </div>
              )}

              {state.activeRun?.plan && (
                <AnalysisPlanStatus run={state.activeRun} onRetry={() => void retryRun()} />
              )}
            </div>

            <div ref={conversationEndRef} />
          </div>
        </section>

        <div className="composer-area">
          <form onSubmit={(event) => void submit(event)}>
            <label className="sr-only" htmlFor="question">输入经营分析问题</label>
            <textarea
              id="question"
              ref={textareaRef}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="问一个关于订单、销售或经营趋势的问题…"
              rows={1}
              disabled={state.status === 'working'}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  event.currentTarget.form?.requestSubmit()
                }
              }}
            />
            <button
              aria-label={state.status === 'working' ? '正在分析' : '发送问题'}
              className="submit-button"
              disabled={!question.trim() || state.status === 'working'}
            >
              {state.status === 'working' ? <span className="button-spinner" /> : <Icon name="send" size={18} />}
            </button>
          </form>
          <p className="composer-hint">
            <span>Enter 发送 · Shift + Enter 换行</span>
            <span>答案由查询结果生成，可展开查看 SQL</span>
          </p>
        </div>
      </main>
    </div>
  )
}
