# CommerceTrace Agent 架构设计文档

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [Agent 核心设计](#3-agent-核心设计)
4. [请求状态机](#4-请求状态机)
5. [工具系统](#5-工具系统)
6. [记忆机制](#6-记忆机制)
7. [上下文装配](#7-上下文装配)
8. [持久化层](#8-持久化层)
9. [安全策略](#9-安全策略)
10. [LLM 集成](#10-llm-集成)
11. [API 层](#11-api-层)
12. [运行时组装](#12-运行时组装)

---

## 1. 项目概述

**CommerceTrace（商迹）** 是一款面向中文电商经营分析场景的 AI Agent。它以自然语言问题为输入，通过受控的 SQL 工具调用、记忆检索和证据合成，输出有据可依的定量分析结论。

### 核心设计原则

- **只读受控访问**：所有 SQL 经过安全策略校验，仅允许对 `ecommerce` schema 的只读 SELECT 查询
- **证据驱动**：每个定量结论必须引用本次执行产生的 `Evidence ID`
- **记忆复用**：将已验证的 SQL 查询经验持久化为可信记忆，跨会话复用
- **流式事件**：全生命周期通过 SSE (Server-Sent Events) 向前端推送结构化事件

### 技术栈

| 层次 | 技术 |
|------|------|
| Web 框架 | FastAPI |
| LLM | DeepSeek（OpenAI 兼容 API） |
| 数据库 | SQLite（三库分离架构） |
| 向量索引 | ChromaDB + BGE 中文 Embedding |
| SQL 解析 | sqlglot |
| 后端语言 | Python 3.12+ |

---

## 2. 整体架构

```
┌──────────────────────────────────────────────────────────┐
│                       FastAPI                            │
│  GET /health    POST /api/chat    GET /api/conversations │
└──────────┬───────────────────────────────────────────────┘
           │
     ┌─────▼──────┐
     │   Agent    │  ← 核心：单 Agent 请求循环
     └─────┬──────┘
           │
    ┌──────┼──────────┬──────────────┐
    │      │          │              │
┌───▼──┐ ┌─▼──────┐ ┌─▼──────┐ ┌───▼──────────┐
│Context│ │ Tool   │ │Memory  │ │ Persistence  │
│Assemb-│ │Registry│ │Service │ │ (SQLite)     │
│ler   │ │        │ │        │ │              │
└──────┘ └────────┘ └────────┘ └──────────────┘
```

### 模块结构

```
backend/src/commerce_trace/
├── agent/                   # Agent 核心
│   ├── core.py              # Agent 主循环
│   ├── state.py             # 请求状态机
│   ├── synthesis.py         # 答案合成
│   └── tools/               # 工具系统
│       ├── definitions.py   # 工具定义（run_sql, visualize_data, search_memory）
│       └── registry.py      # 工具注册与执行
├── memory/                  # 记忆系统
│   ├── core.py              # 记忆核心：记录、搜索、生命周期
│   ├── index.py             # 向量索引（ChromaDB）
│   ├── bootstrap.py         # Golden SQL 初始化
│   └── replay.py            # 记忆重放验证
├── persistence/             # 持久化层
│   ├── sqlite.py            # SQLite 实现
│   └── store.py             # 协议定义 + InMemory 实现
├── context.py               # 上下文装配器
├── contracts.py             # 数据契约（Pydantic 模型）
├── llm.py                   # LLM 服务抽象与实现
├── sql_safety.py            # SQL 安全策略
├── config.py                # 配置管理
├── api.py                   # FastAPI 应用工厂
├── runtime.py               # 运行时组装（DI）
└── testing.py               # 测试工具
```

---

## 3. Agent 核心设计

### 3.1 定位

`Agent` 是一个**单 Agent 请求执行器**——每次用户提问启动一次完整的 Agent 运行周期。Agent 本身不维护跨会话状态（状态由 Persistence 层管理），每次请求独立运行。

### 3.2 Agent 构造

```python
class Agent:
    def __init__(
        self,
        *,
        llm: LlmService,                    # LLM 服务
        registry: ToolRegistry,             # 工具注册表
        context_assembler: ContextAssembler, # 上下文装配器
        store: ConversationLedger,          # 对话账本（持久化）
        memory: MemoryService,              # 记忆服务
        max_tool_iterations: int = 10,      # 最大工具迭代次数
        max_business_sql_calls: int = 5,    # 最大业务 SQL 调用次数
        max_sql_retries_per_purpose: int = 2, # 同目的 SQL 最大重试
        enable_sql_retries: bool = True,    # 是否启用 SQL 重试
        record_candidates: bool = True,     # 是否记录候选记忆
    ) -> None:
```

关键约束：
- `max_tool_iterations=10`：每个请求最多 10 次工具调用迭代
- `max_business_sql_calls=5`：即使 10 次迭代未用完，SQL 调用也不得超过 5 次
- `max_sql_retries_per_purpose=2`：同一 `purpose` 的 SQL 最多重试 2 次

### 3.3 请求执行流程

```
run()
  │
  ├─ 1. 初始化 RequestState
  │     └─ 确保用户和会话存在
  │
  ├─ 2. 预处理门禁
  │     ├─ 不安全请求 → REFUSED
  │     ├─ 问候语     → COMPLETED（直接回复）
  │     └─ 模糊问题   → CLARIFICATION_REQUIRED
  │
  ├─ 3. 上下文装配（ContextAssembler.assemble）
  │     └─ 失败 → FAILED
  │
  ├─ 4. 生成计划（_plan）
  │     ├─ 归因问题 → 3 步计划
  │     └─ 普通问题 → 1 步计划
  │
  ├─ 5. 工具执行循环
  │     ├─ LLM 决定工具调用
  │     ├─ 执行工具（run_sql / visualize_data / search_memory）
  │     ├─ 积累 Evidence / Chart
  │     ├─ 失败重试（受限制）
  │     └─ 循环直到：LLM 无工具调用 / 达到限制
  │
  └─ 6. 答案合成（synthesize）
        ├─ 检查数据覆盖范围
        ├─ 合成自然语言结论
        ├─ 附上证据引用
        └─ 输出：COMPLETED 或 INCOMPLETE
```

### 3.4 请求预处理

#### 不安全请求检测

```python
def _is_unsafe_request(question: str) -> bool:
    markers = {
        "drop ", "delete ", "update ", "insert ",
        "truncate ", "create ", "alter ", "grant ",
        "revoke ", "copy ", "merge ",
        "agent_app", "连接字符串", "数据库密码", "系统提示词",
    }
    return any(marker in lowered for marker in markers)
```

即使 LLM 生成了恶意意图的 SQL，`SqlSafetyPolicy` 在工具层也会拦截。

#### 问候语识别

精确匹配常见问候（你好、hi、在吗），直接返回友好回复，不消耗 LLM 调用。

#### 模糊问题识别

一组精确匹配的模糊问题模板，触发口径澄清。

### 3.5 计划生成

```python
def _plan(question: str) -> list[PlanStep]:
    if is_attribution(question):
        # 归因问题：3 步分析路径
        return [
            "确认总体变化并拆分订单量与客单价",
            "分析地区和品类贡献",
            "检查取消退款影响并汇总相关因素",
        ]
    else:
        # 普通问题：单步查询
        return ["执行经营指标查询"]
```

计划步骤驱动事件流，前端据此展示进度。每一步完成时状态从 `pending → in_progress → completed`。

---

## 4. 请求状态机

### 4.1 状态定义

```
STARTED ─┬─→ CONTEXT_READY ─→ PLANNED ─→ EXECUTING ─→ SYNTHESIZING ─┬─→ COMPLETED
         │                                                            │
         ├─→ REFUSED                                                  └─→ INCOMPLETE
         ├─→ CLARIFICATION_REQUIRED
         ├─→ COMPLETED (greeting)
         └─→ FAILED
```

### 4.2 状态转移表

```python
_ALLOWED_TRANSITIONS = {
    STARTED:     {CONTEXT_READY, COMPLETED, REFUSED, CLARIFICATION_REQUIRED, FAILED},
    CONTEXT_READY:  {PLANNED, FAILED},
    PLANNED:        {EXECUTING, FAILED},
    EXECUTING:      {SYNTHESIZING, FAILED},
    SYNTHESIZING:   {COMPLETED, INCOMPLETE, FAILED},
    # 终态不可转移
    COMPLETED/REFUSED/CLARIFICATION_REQUIRED/FAILED/INCOMPLETE: set(),
}
```

### 4.3 RequestState 数据模型

```python
@dataclass
class RequestState:
    user_id: str
    conversation_id: str
    request_id: str
    question: str
    phase: RequestPhase = STARTED
    retrieved: RetrievedContext | None    # 装配的上下文
    plan: list[PlanStep]                  # 执行计划
    messages: list[LlmMessage]            # LLM 对话消息
    tool_context: ToolExecutionContext    # 工具执行上下文
    evidence: list[Evidence]              # 积累的证据
    charts: list[Chart]                   # 生成的图表
    retry_counts: dict[str, int]          # 按目的计数重试
    incomplete_reason: str | None         # 未完成原因
    llm_content: str                      # LLM 最终文本输出
    current_step_index: int               # 当前步骤索引
    tool_iterations: int                  # 工具调用次数
    sql_calls: int                        # SQL 调用计数
    llm_calls: int                        # LLM 调用计数
    input_tokens: int                     # 输入 token 数
    output_tokens: int                    # 输出 token 数
```

### 4.4 工具调用门禁

`begin_tool()` 方法是工具执行前的三重限制检查：

```python
def begin_tool(self, *, name, purpose,
               max_tool_iterations, max_business_sql_calls,
               max_sql_retries_per_purpose) -> bool:
    # 1. 总工具迭代上限
    if self.tool_iterations >= max_tool_iterations:
        self.incomplete_reason = "tool_iteration_limit"
        return False
    # 2. 业务 SQL 次数上限（仅 run_sql）
    if name == "run_sql" and self.sql_calls >= max_business_sql_calls:
        self.incomplete_reason = "business_sql_limit"
        return False
    # 3. 同目的 SQL 重试上限
    if name == "run_sql" and self.retry_counts[purpose] > max_sql_retries_per_purpose:
        self.incomplete_reason = "sql_retry_limit"
        return False
    ...
```

### 4.5 未完成原因

| 原因 | 含义 |
|------|------|
| `tool_iteration_limit` | 达到工具调用总次数上限（10） |
| `business_sql_limit` | 达到 SQL 调用次数上限（5） |
| `sql_retry_limit` | 同目的 SQL 重试超过上限（2） |
| `sql_retry_disabled` | SQL 重试被配置禁用 |
| `insufficient_evidence` | 没有任何证据产生 |
| `data_coverage_gap` | 目标时间不在数据覆盖范围内 |

---

## 5. 工具系统

### 5.1 设计模式

使用 **Strategy + Registry** 模式：每个工具是独立策略，注册到 `ToolRegistry` 中统一管理和执行。

```python
class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> None: ...
    def schemas(self) -> list[ToolSchema]: ...     # LLM function calling schema
    async def execute(self, name, arguments, context) -> ToolResult: ...
```

### 5.2 工具抽象

```python
class Tool(ABC, Generic[ArgsT]):
    name: str
    description: str
    args_model: type[ArgsT]            # Pydantic 参数模型

    async def execute(self, context: ToolExecutionContext, args: ArgsT) -> ToolResult: ...

    def schema(self) -> ToolSchema:     # 自动从 args_model 生成 JSON Schema
```

每个工具通过 `args_model`（Pydantic 模型）声明参数约束，`ToolRegistry.schemas()` 自动生成 OpenAI function calling 格式的 JSON Schema。

### 5.3 内置工具

#### 5.3.1 run_sql — SQL 查询

```
名称: run_sql
参数: sql (string), purpose (string), expected_columns (string[])
功能: 执行一条有界的 SQLite 只读业务查询
```

执行流程：
1. `SqlSafetyPolicy.validate()` 安全检查
2. `SQLiteSqlExecutor.execute()` 执行查询
3. 结果序列化 + SHA-256 哈希
4. 返回 `ToolSuccess`（含 preview、columns、row_count）或 `ToolFailure`

#### 5.3.2 visualize_data — 图表生成

```
名称: visualize_data
参数: evidence_id, chart_type, title, x?, y?, value?
功能: 从本次请求的 Evidence 结果生成受控 Plotly JSON
```

支持类型：`metric_card`（指标卡）、`bar`（柱状图）、`line`（折线图）、`pie`（饼图）。

安全检查：验证 `evidence_id` 属于本次请求；验证字段名在查询结果中存在。

#### 5.3.3 search_memory — 记忆检索

```
名称: search_memory
参数: query (string)
功能: 按原始问题和当前步骤检索业务规则与工具经验
```

LLM 可以在执行过程中主动调用此工具检索相关记忆，获取之前验证过的 SQL 经验。

### 5.4 错误处理

工具返回统一的 `ToolResult`（`ToolSuccess | ToolFailure`）：

```python
class ToolFailure(BaseModel):
    success: Literal[False] = False
    safe_error_code: str        # 机器可读错误码
    safe_error_message: str     # 人类可读安全消息（不泄露内部信息）
    retryable: bool = False     # 是否可重试
```

`retryable=True` 时，Agent 循环中的 `continue` 会让 LLM 重新尝试；`retryable=False` 则会中断循环。

---

## 6. 记忆机制

记忆系统是 CommerceTrace 最核心的差异化设计。它使得 Agent 能够将已验证的 SQL 查询经验持久化，跨会话复用，避免了每次面对类似问题时"重新发明轮子"。

### 6.1 设计哲学

记忆系统采用的核心理念：

> **SQLite 是权威数据源，向量索引是派生加速器。**

所有记忆记录的增删改查以 SQLite 为准；ChromaDB 向量索引是完全可重建的派生数据，删除后可从 SQLite 无损恢复。

### 6.2 记忆记录

```python
class MemoryRecord(BaseModel):
    memory_id: str                      # 唯一标识 mem_{16位hex}
    question: str                       # 原始问题
    analysis_step: str                  # 分析步骤名称
    normalized_sql: str                 # 规范化 SQL
    tables_and_columns: list[str]       # 涉及的表和列
    schema_fingerprint: str             # Schema 指纹
    metric_versions: dict[str, str]     # 指标版本绑定
    execution_time_ms: float            # 执行耗时
    row_count: int                      # 结果行数
    column_names: list[str]             # 列名
    limited_summary: str                # 500 字总结
    result_hash: str                    # 结果哈希（SHA-256）
    status: MemoryStatus                # 状态
    source: str                         # 来源：runtime | knowledge:xxx
    created_at: datetime
    last_verified_at: datetime | None   # 最后验证时间
```

#### 去重键

```python
@property
def dedupe_key(self) -> str:
    body = "|".join([
        self.question.strip().lower(),
        self.analysis_step.strip().lower(),
        self.normalized_sql,
        self.schema_fingerprint,
        repr(sorted(self.metric_versions.items())),
    ])
    return hashlib.sha256(body.encode()).hexdigest()
```

去重键保证了：同一问题 + 同一步骤 + 同一 SQL + 同一 schema 版本 + 同一指标版本 → 同一条记忆。

### 6.3 记忆状态生命周期

```
                         ┌─────────┐
          record_candidate│         │ replay (hash match)
              ───────────►│CANDIDATE├──────────────► TRUSTED
                         │         │
                         └────┬───┬┘
                              │   │
              schema/metric   │   │ replay
              version change  │   │ (hash mismatch
                              │   │ or unsafe)
                              ▼   ▼
                         ┌─────────┐
                         │ STALE   │   REJECTED
                         │ (终态)  │   (终态)
                         └─────────┘
```

状态转移规则：

```python
ALLOWED_MEMORY_TRANSITIONS = {
    CANDIDATE: {TRUSTED, STALE, REJECTED},
    TRUSTED:   {STALE, REJECTED},
    STALE:     set(),      # 终态
    REJECTED:  set(),      # 终态
}
```

- **CANDIDATE**：运行时自动记录的新查询经验（默认状态）
- **TRUSTED**：通过 Golden Case 验证的经验
- **STALE**：因 Schema 或指标版本变更而失效
- **REJECTED**：重放验证失败或不安全的 SQL

### 6.4 记忆搜索

`MemoryService.search()` 是双层检索：

```python
async def search(self, query: str, *, limit_candidates: int = 2) -> list[MemorySearchResult]:
    # 1. 过滤：仅当前 schema_fingerprint 和 metric_versions 匹配的记录
    # 2. 排序：
    #    - 向量索引命中 → 按索引排名加权
    #    - 未命中 → 按问题文本 token 相似度
    # 3. 返回：
    #    - trusted: 最多 5 条
    #    - unverified_candidate: 最多 2 条（可配置关闭）
```

搜索结果标签：
- `trusted`：已验证记忆，Agent 可直接信赖
- `unverified_candidate`：运行时记录的候选，Agent 仅作参考

### 6.5 向量索引

#### InMemoryDerivedIndex

简单的 token 级别文本相似度（Jaccard 距离），基于中英文字符串分词：

```python
def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0
    return len(a & b) / len(a | b)  # Jaccard similarity
```

#### ChromaMemoryIndex

基于 ChromaDB 的语义向量索引，使用 BGE 中文模型（`BAAI/bge-small-zh-v1.5`）：

```
ChromaMemoryIndex
├── business_memory_index    # 业务知识和 Golden Rules
│   └── 文档: rule + metric (knowledge YAML)
└── tool_memory_index        # 工具经验
    └── 文档: question + analysis_step + normalized_sql
```

索引重建采用 **全量替换** 策略——每次 rebuild 删除旧集合，重新 upsert 全部活跃记录。这样设计是因为：
- SQLite 是权威源，数据不会丢
- 避免增量同步的复杂性
- 索引数据量小（通常 < 1000 条），全量重建成本低

### 6.6 Golden SQL 与记忆引导

#### 知识文件结构

```
knowledge/
├── golden_sql/
│   ├── monthly_revenue.yaml
│   └── revenue_by_region.yaml
├── metrics/
│   └── core.yaml
├── rules/
│   └── ...
└── VERSION
```

Golden SQL YAML 格式：

```yaml
id: "monthly_revenue"
question: "按月统计销售额"
analysis_step: "执行经营指标查询"
sql: "SELECT strftime('%Y-%m', ordered_at) AS month, SUM(total_amount) ..."
expected:
  type: result_hash
  value: "abc123..."
metric_versions:
  revenue: "1"
```

#### 引导流程

```
bootstrap_memory()
  ├── 1. 扫描 knowledge/golden_sql/*.yaml
  ├── 2. SqlSafetyPolicy 校验每条 SQL
  ├── 3. 构造 MemoryRecord (status=TRUSTED, source=knowledge)
  └── 4. upsert 到 SQLite
```

### 6.7 记忆重放验证

`replay_memories()` 是记忆质量保障机制：

```
replay_memories()
  └── 对每条 CANDIDATE / TRUSTED 记忆：
        ├── Schema/版本不匹配 → STALE
        ├── 无匹配 Golden Case → SKIPPED
        ├── Golden Case 无 expected_hash → SKIPPED
        ├── SQL 安全校验失败 → REJECTED
        ├── SQL 执行失败 → SKIPPED
        ├── result_hash == expected_hash → TRUSTED（升级！）
        └── result_hash != expected_hash → REJECTED（降级）
```

重放报告包含每条记忆的前后状态变化，支持 JSON 和 Markdown 格式输出。

### 6.8 候选记忆自动记录

Agent 执行完成后，自动将本次产生的 `Evidence` 记录为候选记忆：

```python
# Agent._complete() 中
if record_candidates:
    for item in state.evidence:
        await self.memory.record_candidate(state.question, item)
```

此时记忆状态为 `CANDIDATE`——等待后续重放验证升级为 `TRUSTED` 或被拒绝。

### 6.9 版本失效

当 Schema 或指标定义发生变化时，`invalidate_versions()` 会将所有不匹配的记忆批量标记为 `STALE`：

```python
async def invalidate_versions(self, schema_fingerprint, metric_versions) -> int:
    for record in await self.store.list_memories({CANDIDATE, TRUSTED}):
        if record.schema_fingerprint != schema_fingerprint or \
           any(metric_versions.get(key) != value for key, value in record.metric_versions.items()):
            transition_memory(record, MemoryStatus.STALE)
            await self.store.upsert_memory(record)
```

这确保了记忆不会在数据结构变化后继续返回过时结果。

---

## 7. 上下文装配

### 7.1 ContextAssembler

`ContextAssembler` 负责将分散的信息源组装为 LLM 可消费的上下文：

```python
class ContextAssembler:
    def __init__(
        self,
        memory: MemoryService,
        knowledge_loader: KnowledgeLoader,
        *,
        include_knowledge: bool = True,
        include_memory: bool = True,
        schema_provider: SchemaProvider,
    ):
```

### 7.2 装配内容

```python
class RetrievedContext:
    schema_catalog: dict[str, Any]       # 数据库 Schema 定义
    schema_fingerprint: str              # Schema SHA-256 指纹
    schema_version: str                  # Schema 版本
    knowledge_version: str               # 知识库版本
    rules: list[dict[str, Any]]          # 业务规则
    metrics: list[dict[str, Any]]        # 指标定义
    memories: list[MemorySearchResult]   # 检索到的记忆
    degraded: bool                       # 降级标记
```

### 7.3 提示词构造

上下文被序列化为单个 JSON 追加到 System Prompt：

```python
def prompt_section(self) -> str:
    payload = {
        "schema": self.schema_catalog,
        "schema_fingerprint": self.schema_fingerprint,
        "business_rules": self.rules,
        "metrics": self.metrics,
        "retrieved_memory": [
            {
                "label": item.label,       # trusted | unverified_candidate
                "question": item.record.question,
                "sql": item.record.normalized_sql,
            }
            for item in self.memories
        ],
    }
    return json.dumps(payload, ensure_ascii=False)
```

### 7.4 System Prompt

```
你是中文电商经营分析助手。
只能使用提供的受控工具和已加载上下文，不得猜测数据库值或结果。
定量结论必须引用本次执行产生的 Evidence ID。
...
先给结论，再给证据和口径说明。
图表由界面根据 visualize_data 的结构化结果单独展示；最终回答不得输出 Markdown 图片语法
```

核心约束：
- 强制引用 Evidence ID
- 不得猜测数值
- 区分"无数据"和"值为 0"
- 归因只描述相关性，不宣称因果
- 图表与文本分离

### 7.5 降级策略

当 Memory 服务不可用时：

```python
if self.include_memory:
    try:
        memories = await self.memory.search(question)
    except Exception:
        degraded = True    # 降级：不带记忆继续执行
```

降级不会导致请求失败——Agent 仍可基于 Schema 和业务规则执行分析，只是失去了记忆增强。

---

## 8. 持久化层

### 8.1 三库分离架构

SQLite 数据库使用 `ATTACH DATABASE` 实现物理分离的三库架构：

```
data/commerce_trace.db            # 主库（空壳）
data/commerce_trace-ecommerce.db  # 业务数据
data/commerce_trace-agent.db      # Agent 元数据
```

访问时通过 schema 前缀区分：
- `ecommerce.orders` — 业务数据
- `agent_app.memory_records` — Agent 元数据

### 8.2 Agent 元数据 Schema

```
agent_app 数据库表结构：

anonymous_users          # 匿名用户（基于 cookie）
  └── user_id (PK)

conversations            # 对话会话
  ├── conversation_id (PK)
  ├── user_id (FK)
  ├── title
  ├── created_at
  └── updated_at

messages                 # 对话消息
  ├── message_id (PK)
  ├── conversation_id (FK)
  ├── role (user/assistant)
  ├── content
  └── created_at

stream_events            # 流式事件（完整事件溯源）
  ├── event_id (PK)
  ├── conversation_id (FK)
  ├── request_id
  ├── event_type
  ├── payload (JSON)
  └── created_at

tool_calls               # 工具调用记录
  ├── tool_call_id (PK)
  ├── conversation_id (FK)
  ├── request_id
  ├── tool_name
  ├── arguments (JSON)
  ├── status
  └── created_at

tool_results             # 工具结果
  ├── tool_result_id (PK)
  ├── tool_call_id (FK)
  ├── success
  ├── result_summary (JSON)
  └── created_at

evidence                 # 证据记录
  ├── evidence_id (PK)
  ├── conversation_id (FK)
  ├── request_id
  ├── analysis_step
  ├── tool_call_id
  ├── claim
  ├── sql
  ├── columns_json
  ├── row_count
  ├── result_hash
  ├── execution_time_ms
  ├── preview_json
  └── executed_at

charts                   # 图表记录
  ├── chart_id (PK)
  ├── conversation_id (FK)
  ├── request_id
  ├── evidence_id
  ├── chart_type
  ├── title
  ├── figure_json
  └── created_at

memory_records           # 记忆记录（核心）
  ├── memory_id (PK)
  ├── dedupe_key (唯一索引)
  ├── question
  ├── analysis_step
  ├── normalized_sql
  ├── tables_and_columns (JSON)
  ├── schema_fingerprint
  ├── metric_versions (JSON)
  ├── execution_time_ms
  ├── row_count
  ├── column_names (JSON)
  ├── limited_summary
  ├── result_hash
  ├── status (candidate/trusted/stale/rejected)
  ├── source
  ├── created_at
  └── last_verified_at
```

### 8.3 协议分层

持久化使用 Protocol 定义接口，支持多实现：

```python
class ConversationLedger(Protocol):
    async def ensure_user(self, user_id: str) -> None: ...
    async def ensure_conversation(self, conversation_id, user_id, title) -> None: ...
    async def save_message(self, conversation_id, role, content) -> None: ...
    async def save_event(self, user_id, event: StreamEvent) -> None: ...
    ...

class MemoryRepository(Protocol):
    async def upsert_memory(self, record: MemoryRecord) -> MemoryRecord: ...
    async def list_memories(self, statuses=None) -> list[MemoryRecord]: ...
    ...

class Store(ConversationLedger, MemoryRepository, Protocol):
    """运行时使用的组合协议"""
```

实现：
- **SQLiteStore**：生产环境，SQLite 持久化
- **InMemoryStore**：测试/开发环境，纯内存

### 8.4 SQLiteStore 设计要点

- **异步安全**：使用 `asyncio.Lock` 保证 SQLite 连接串行访问
- **只读业务查询**：业务 SQL 使用 `read_only=True` 模式连接
- **权限隔离**：用户只能访问自己的 conversations，通过 `user_id` 校验
- **JSON 列**：`arguments`、`payload` 等复杂字段以 JSON 文本存储
- **去重设计**：`memory_records.dedupe_key` 有唯一约束，upsert 按 dedupe key 合并

### 8.5 对话重放

`replay_conversation()` 支持完整还原一次对话的全部事件和上下文：

```
返回结构：
{
  conversation: {...},
  messages: [{role, content, created_at}, ...],
  events: [StreamEvent, ...],
  tool_calls: [{tool_name, arguments, status}, ...],
  tool_results: [{success, result_summary}, ...],
  evidence: [{claim, sql, preview, ...}, ...],
  charts: [{chart_type, figure, ...}, ...]
}
```

这支持前端完整还原对话历史，无需重新执行。

---

## 9. 安全策略

### 9.1 多层防御

```
用户输入 → _is_unsafe_request() 关键字检测
                ↓
         LLM 生成 SQL
                ↓
         SqlSafetyPolicy.validate() 结构化校验
                ↓
         SQLite PRAGMA query_only = ON（底层保护）
```

### 9.2 SqlSafetyPolicy

使用 `sqlglot` 解析 SQL AST，实现白名单管控：

| 检查维度 | 策略 |
|---------|------|
| 语句类型 | 仅允许 SELECT / UNION / INTERSECT / EXCEPT |
| 禁止操作 | INSERT / UPDATE / DELETE / CREATE / DROP / ALTER / GRANT / REVOKE / COPY / MERGE |
| Schema 白名单 | 仅允许 `ecommerce` schema |
| 表白名单 | 仅 8 张业务表 |
| 危险函数 | 禁止 `pg_sleep` / `dblink` / `lo_import` 等 |
| DISTINCT 探索 | 仅允许白名单字段的单表 DISTINCT |
| 敏感字段 | 禁止对 `name` / `email` / `phone` 等 DISTINCT 探索 |
| 行数限制 | SELECT 最多 500 行，DISTINCT 最多 50 行 |
| 多语句 | 禁止 |

### 9.3 错误信息脱敏

所有错误消息经过脱敏处理，不暴露内部信息：

```python
# 对外暴露的安全消息
"查询执行失败，请检查字段、筛选条件或聚合方式"
"只允许只读 SELECT 查询"
"查询只能访问 ecommerce 业务数据"

# 绝不暴露
# - 数据库连接信息
# - 完整 SQL 错误
# - 系统路径/配置
```

### 9.4 认证与授权

- **匿名用户**：基于 cookie 的伪认证，`anon_{uuid}` 格式
- **对话隔离**：每次操作前校验 `user_id` 与 `conversation_id` 的所有权
- **CORS**：仅允许 `localhost:5173`（Vite dev）和 `localhost:3000`

---

## 10. LLM 集成

### 10.1 抽象层

```python
class LlmService(ABC):
    async def complete(
        self,
        messages: list[LlmMessage],
        tools: list[ToolSchema],
        system_prompt: str,
    ) -> LlmResponse:
```

实现：
- **OpenAICompatibleLlm**：生产实现，对接 DeepSeek API
- **ScriptedLlm**：测试实现，确定性工具调用序列，不依赖外部 API

### 10.2 OpenAICompatibleLlm

```
POST {base_url}/chat/completions
  ├── model: deepseek-v4-flash (默认)
  ├── temperature: 0 (确定性输出)
  ├── thinking: disabled (禁用思维链)
  ├── tools: [ToolSchema, ...] (function calling 格式)
  └── tool_choice: auto
```

特点：
- 支持 HTTP 代理（自动读取 `HTTPS_PROXY` 等环境变量）
- 60 秒超时
- 响应自动解析为 `LlmResponse`（含 tool_calls + usage）

### 10.3 ScriptedLlm（测试双）

`ScriptedLlm` 是根据问题关键词的**确定性状态机**，不发出任何网络请求：

- 检测归因关键词（为什么/原因/驱动）→ 按序返回 3 步 SQL 调用
- 检测地区/退款关键词 → 返回对应 SQL
- 检测图表需求 → 返回 visualize_data 调用
- SQL 失败且可重试 → 重试同一调用

这使得 Agent 在测试中完全可预测，不依赖外部 LLM 服务。

---

## 11. API 层

### 11.1 端点设计

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查（database + dataset + knowledge + derived_index） |
| `/api/chat` | POST | 核心：SSE 流式对话 |
| `/api/conversations` | GET | 列出用户对话历史 |
| `/api/conversations/{id}` | GET | 获取对话完整回放 |

### 11.2 SSE 事件流

Agent 运行全程通过 SSE 推送事件：

```
event: conversation.started     → 对话开始，包含问题
event: context.retrieved        → 上下文装配完成
event: plan.created             → 计划生成
event: plan.step_started        → 步骤开始
event: tool.started             → 工具调用开始
event: tool.completed           → 工具调用完成
event: tool.failed              → 工具调用失败
event: evidence.created         → 证据创建
event: chart.created            → 图表创建
event: answer.delta             → 答案片段
event: answer.completed         → 答案完成（含完整元数据）
event: request.failed           → 请求失败
```

每个事件携带 `conversation_id`、`request_id`、结构化 `payload`。

### 11.3 应用工厂

```python
def create_app(*, settings, store, agent, resources) -> FastAPI:
```

支持多种运行模式：
- **生产模式**：`build_runtime()` → SQLite + DeepSeek
- **测试模式**：`InMemoryStore + ScriptedLlm`
- **自定义注入**：依赖全部可替换

---

## 12. 运行时组装

### 12.1 build_runtime

```python
def build_runtime(settings, features=None, llm=None) -> Runtime:
```

组装过程：

```
1. SQLiteResources(database_path)           # 数据库连接资源
2. ChromaMemoryIndex(chroma_path)           # 向量索引（可选）
3. SQLiteStore(resources, index_health)      # 持久化
4. MemoryService(store, fingerprint, ...)    # 记忆服务
5. SQLiteSqlExecutor(database_path)          # SQL 执行器
6. SqlSafetyPolicy(max_rows, ...)            # 安全策略
7. OpenAICompatibleLlm(base_url, key, model) # LLM
8. build_default_registry(executor, memory, policy)  # 注册 3 个工具
9. ContextAssembler(memory, knowledge, ...)   # 上下文装配
10. Agent(llm, registry, context, store, memory)     # 核心 Agent
```

### 12.2 FeatureConfiguration

运行时特性开关：

```python
@dataclass(frozen=True)
class FeatureConfiguration:
    include_knowledge: bool = True   # 是否加载业务知识（rules + metrics）
    include_memory: bool = True      # 是否启用记忆检索
    include_candidates: bool = True  # 搜索结果是否包含候选记忆
    enable_sql_retries: bool = True  # 是否允许 SQL 失败重试
    record_candidates: bool = True   # 是否自动记录候选记忆
```

### 12.3 配置管理

使用 `pydantic-settings`，所有配置通过环境变量注入：

```bash
# 环境变量前缀：COMMERCE_TRACE_
COMMERCE_TRACE_ENVIRONMENT=production
COMMERCE_TRACE_DATABASE_PATH=data/commerce_trace.db
COMMERCE_TRACE_DEEPSEEK_API_KEY=sk-xxx
COMMERCE_TRACE_DEEPSEEK_MODEL=deepseek-v4-flash
COMMERCE_TRACE_MAX_TOOL_ITERATIONS=10
COMMERCE_TRACE_MAX_BUSINESS_SQL_CALLS=5
COMMERCE_TRACE_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
```

---

## 附录 A：关键设计决策

| 决策 | 理由 |
|------|------|
| 单 Agent 而非多 Agent | 电商分析场景足够聚焦，单 Agent + 工具调用能覆盖 |
| SQLite 而非 PostgreSQL | 轻量部署，零运维，足够承载分析场景 |
| SSE 而非 WebSocket | 请求-响应模式更匹配，SSE 实现更简单 |
| 记忆去重而非增量更新 | 避免增量同步复杂度，全量重建成本低 |
| Python Protocol 而非 ABC | 工具消费方不依赖实现，松耦合 |
| sqlglot 解析而非正则 | 安全校验必须是精确的 AST 分析 |
| temperature=0 | 经营分析需要确定性，不需要创意 |
| 匿名用户 + Cookie | 简化部署，无需注册登录 |
| 向量索引作为派生数据 | 确保 SQLite 是单一权威源 |

## 附录 B：数据流完整追踪

```
用户输入 "为什么上个月销售额下降了？"
  │
  ├─ Agent.run()
  │   ├─ [Event] CONVERSATION_STARTED
  │   ├─ 预处理：非 unsafe / 非 greeting / 非 vague
  │   │
  │   ├─ ContextAssembler.assemble("为什么上个月销售额下降了？")
  │   │   ├─ SchemaProvider.load() → schema_catalog
  │   │   ├─ KnowledgeLoader.load() → rules + metrics
  │   │   └─ MemoryService.search(question)
  │   │       └─ ChromaMemoryIndex.search() + token similarity
  │   │           → 2 trusted memories + 1 candidate memory
  │   │
  │   ├─ [Event] CONTEXT_RETRIEVED
  │   ├─ _plan() → 3-step attribution plan
  │   ├─ [Event] PLAN_CREATED
  │   │
  │   ├─ 工具循环 迭代 1:
  │   │   ├─ [Event] PLAN_STEP_STARTED (step 1)
  │   │   ├─ LLM.complete() → tool_call: run_sql(monthly revenue)
  │   │   ├─ [Event] TOOL_STARTED
  │   │   ├─ SqlSafetyPolicy.validate() ✓
  │   │   ├─ SQLiteSqlExecutor.execute()
  │   │   ├─ [Event] TOOL_COMPLETED
  │   │   ├─ _evidence_from_result() → Evidence{ev_xxx}
  │   │   └─ [Event] EVIDENCE_CREATED
  │   │
  │   ├─ 工具循环 迭代 2:
  │   │   └─ ... (region + category breakdown)
  │   │
  │   ├─ 工具循环 迭代 3:
  │   │   └─ ... (cancellation/refund check)
  │   │
  │   ├─ 工具循环 迭代 4:
  │   │   └─ LLM.complete() → content="根据查询结果，上个月销售额下降..."
  │   │
  │   ├─ synthesize(question, evidence, llm_content, None)
  │   │   ├─ temporal_coverage_gap_conclusion() → None (in range)
  │   │   ├─ 组合 evidence claims
  │   │   └─ 返回完整结论文本
  │   │
  │   ├─ [Event] ANSWER_DELTA
  │   ├─ save_message("assistant", answer)
  │   ├─ MemoryService.record_candidate() × 3 (每条 evidence)
  │   └─ [Event] ANSWER_COMPLETED {evidence_ids, usage, status: "completed"}
```

---

> 文档版本：1.0  
> 最后更新：2026-07-26  
> 基于代码版本：`0842fc8` (feat: implement SQLite persistence layer)
