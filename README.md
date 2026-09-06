# CommerceTrace · 商迹

一个面向本地电商经营数据的 **AI 分析 Agent**。用户不需要手写 SQL，可以直接用中文提出业务问题；系统会先生成可见的分析计划，再在受控语义模型和 SQL 安全门下查询 SQLite 数据，最后返回结论、查询轨迹与 Plotly 图表。

```text
“最近 30 天销售额为什么下降？”
“按地区比较客单价和复购率。”
“找出退款率异常的品类，并给我画图。”
```

CommerceTrace 的重点不是“让 LLM 随意生成 SQL”，而是把模型负责的推理部分和后端必须确定执行的安全边界拆开：**模型负责计划与解释，系统负责 Schema、指标定义、SQL 准备、只读验证、执行状态和结果持久化。**

## 核心能力

- **自然语言数据分析**：用中文查询本地电商数据。
- **可见分析计划**：复杂请求先展示步骤，再逐项执行。
- **受治理业务指标**：核心指标通过版本化语义模型确定性展开，而不是每次让模型重新猜 SQL。
- **安全 SQL 执行**：SQL AST 校验、EXPLAIN、白名单表、危险函数检查、超时与行数限制。
- **Prepared Query**：模型不能把任意 SQL 直接交给执行器，必须先准备并签发 `prepared_query_id`。
- **Agentic RAG / Schema Context**：模型按需获取表结构和业务语义上下文。
- **后台分析运行**：复杂任务独立于单个 HTTP 请求运行。
- **SSE 实时进度**：前端可查看计划、步骤和运行事件，并支持断线恢复。
- **失败步骤重试**：保留已完成历史，只重试失败部分。
- **会话持久化**：消息、分析运行、查询工件、图表和 LangGraph checkpoints 保存在 SQLite。
- **图表生成**：根据查询结果生成 Plotly 可视化。

## 架构

```text
React Frontend
      │
      │ REST JSON + SSE
      ▼
FastAPI
      │
      ├─ Conversation / Session
      │      └─ HttpOnly anonymous cookie
      │
      ├─ AnalysisCoordinator
      │      └─ AnalysisWorkflow
      │           ├─ generate plan
      │           ├─ execute step-by-step
      │           ├─ completion conditions
      │           ├─ bounded revision
      │           └─ final interpretation
      │
      ├─ LangChain Agent + ChatDeepSeek
      │      ├─ get_schema
      │      ├─ plan_metric_query
      │      ├─ plan_query
      │      ├─ run_sql(prepared_query_id)
      │      └─ visualize_data
      │
      ├─ Versioned Semantic Model
      │      ├─ schemas
      │      ├─ relationships
      │      ├─ governed metrics
      │      └─ exploration rules
      │
      ├─ QueryEngine
      │      ├─ AST validation
      │      ├─ EXPLAIN
      │      ├─ read-only execution
      │      └─ idempotent results
      │
      └─ SQLite
             ├─ business database
             └─ agent/session state
```

## 运行模型

DataAgent 使用“**LLM 决策 + 确定性状态机**”的组合方式：

```text
用户问题
  │
  ▼
生成分析计划
  │
  ▼
逐步执行
  │
  ├─ 获取 Schema / 指标语义
  ├─ 准备查询
  ├─ 安全校验
  ├─ 执行 SQL
  ├─ 检查完成条件
  └─ 必要时有限修订
  │
  ▼
总结结论 + 查询轨迹 + 图表
```

计划中的已完成步骤不会被随意改写。查询结果不足以满足完成条件时，运行可以以 `partial` 状态结束，并保留未满足原因和查询工件。

更完整的设计说明见：

```text
docs/DataAgent工作流设计与实现详解.md
```

## 技术栈

| 层 | 技术 |
|---|---|
| Frontend | React / TypeScript / Vite |
| Backend | FastAPI / Python 3.12 |
| Agent | LangChain / LangGraph |
| LLM | DeepSeek OpenAI-compatible API |
| SQL Parsing | sqlglot |
| Business DB | SQLite |
| Agent State | SQLite / LangGraph checkpointer |
| Visualization | Plotly |
| Streaming | Server-Sent Events (SSE) |
| Python Env | uv |

## 环境要求

- Python **3.12**
- [uv](https://docs.astral.sh/uv/)
- Node.js **20+**（Vite 7 需要 20.19+）
- npm
- 一个可用的模型 API Key

## 60 秒快速开始

### 1. 克隆仓库

```bash
git clone <repository-url>
cd CommerceTrace
```

### 2. 安装前后端依赖

根目录已经提供统一脚本：

```bash
npm run sync
```

它会执行：

```text
backend: uv sync --extra data
frontend: npm install
```

### 3. 配置 `.env`

在仓库根目录创建 `.env`：

```dotenv
COMMERCE_TRACE_MODEL_API_KEY=sk-...
COMMERCE_TRACE_MODEL_BASE_URL=https://api.deepseek.com
COMMERCE_TRACE_MODEL=deepseek-v4-flash
COMMERCE_TRACE_DATABASE_PATH=data/commerce_trace.db
COMMERCE_TRACE_AGENT_STATE_PATH=data/agent_state.db
```

其中：

```text
COMMERCE_TRACE_MODEL_API_KEY
```

为必填项，缺失时后端会在启动阶段报错。

其他常用配置：

| 变量 | 默认值 | 作用 |
|---|---|---|
| `COMMERCE_TRACE_MODEL_BASE_URL` | `https://api.deepseek.com` | 模型 API 地址 |
| `COMMERCE_TRACE_MODEL` | `deepseek-v4-flash` | 当前模型 |
| `COMMERCE_TRACE_STATEMENT_TIMEOUT_MS` | `5000` | SQL 超时，毫秒 |
| `COMMERCE_TRACE_MODEL_TIMEOUT_SECONDS` | `60` | 模型请求超时 |
| `COMMERCE_TRACE_MAX_RESULT_ROWS` | `500` | 普通查询最大结果行数 |
| `COMMERCE_TRACE_MAX_DISTINCT_VALUES` | `50` | 值级探索最大数量 |
| `COMMERCE_TRACE_COOKIE_SECURE` | `false` | 会话 Cookie Secure 标志 |

### 4. 初始化演示数据

```bash
uv run --project backend commerce-trace init --profile test
```

`test` 是小规模数据集，适合开发和测试。

如需较大的演示数据：

```bash
uv run --project backend commerce-trace init --profile demo
```

当前约定：

- `test`：约 80 客户 / 300 订单；
- `demo`：约 10 万订单。

### 5. 启动

一条命令同时启动前后端：

```bash
npm run dev
```

或分别启动：

```bash
npm run backend
npm run frontend
```

默认地址：

| 服务 | 地址 |
|---|---|
| Frontend | `http://localhost:5173` |
| Backend | `http://localhost:8000` |

## 数据初始化命令

CLI 提供：

| 命令 | 作用 |
|---|---|
| `commerce-trace migrate` | 应用 `migrations/*.sql` |
| `commerce-trace generate-data --profile test` | 生成测试数据 |
| `commerce-trace generate-data --profile demo` | 生成大规模演示数据 |
| `commerce-trace init` | 迁移并生成数据 |
| `commerce-trace init --no-data` | 仅迁移 |
| `commerce-trace init --if-empty` | 仅空数据库时生成数据 |

数据生成依赖随：

```bash
uv sync --extra data
```

安装。

## 典型使用

### 基础查询

```text
本月总销售额是多少？
```

### 分组分析

```text
按地区比较销售额和订单数。
```

### 指标诊断

```text
最近一个月退款率上升了吗？主要是哪几个品类导致的？
```

### 可视化

```text
画出最近 12 个月销售额趋势，并标出同比变化。
```

### 多步骤问题

```text
找出最近 90 天销售额下降最多的三个品类，分别分析订单量、客单价和退款率的变化，再告诉我最可能的原因。
```

这类问题会创建后台 Analysis Run，前端可实时看到每一步的状态。

## API

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/conversations` | 新建会话 |
| `GET` | `/api/conversations` | 分页获取会话 |
| `GET` | `/api/conversations/{id}/messages` | 获取历史消息 / 查询 / 图表 |
| `POST` | `/api/conversations/{id}/messages` | 同步发送消息 |
| `POST` | `/api/conversations/{id}/analysis-runs` | 创建后台分析运行 |
| `GET` | `/api/conversations/{id}/analysis-runs/latest` | 获取最近运行 |
| `GET` | `/api/analysis-runs/{run_id}` | 获取运行详情 |
| `GET` | `/api/analysis-runs/{run_id}/events` | SSE 事件流 |
| `POST` | `/api/analysis-runs/{run_id}/retry` | 重试失败步骤 |
| `DELETE` | `/api/conversations/{id}` | 永久删除会话与 checkpoints |

同步消息请求：

```json
{
  "message": "按地区展示销售额"
}
```

同步接口响应会包含：

```json
{
  "conversation_id": "conv_...",
  "answer": "……",
  "queries": [],
  "charts": [],
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0
  }
}
```

复杂分析建议使用 `analysis-runs` 接口；创建成功后由客户端订阅 SSE，而不是保持一个长时间同步 HTTP 请求。

## SQL 安全模型

CommerceTrace 不把模型输出的 SQL 当作可信输入。

### 1. 白名单 Schema

查询仅允许访问 `ecommerce` 语义范围内的业务表。

### 2. Schema Context 前置要求

模型在准备查询前，需要通过 `get_schema` 获取所引用表的列级上下文。表名目录本身不等价于完整 Schema 授权。

### 3. 两类查询计划

受治理的核心指标：

```text
plan_metric_query
```

其他只读分析：

```text
plan_query
```

### 4. Prepared Query

`run_sql` 不接收任意 SQL 字符串，只接受准备阶段签发的：

```text
prepared_query_id
```

Prepared Query 同时绑定语义模型指纹，避免模型绕过准备 / 治理阶段。

### 5. AST 与执行限制

后端使用 `sqlglot` 检查：

- 只读语句；
- 非白名单表；
- 危险函数；
- 敏感字段探索；
- 结果行数；
- 值级 DISTINCT 探索；
- 执行超时。

SQL 在只读模式下执行，并保留 EXPLAIN 与查询轨迹。

## 敏感字段与值探索

低基数字段可以被用于受控 DISTINCT 探索，例如：

- 地区；
- 获客渠道；
- 订单渠道；
- 订单状态；
- 品类；
- 支付方式。

客户姓名、地址、电话、邮箱等敏感字段不应被 Agent 用于自由枚举探索。

## 查询工件

为了让分析可审计，系统会保存与查询有关的信息，例如：

- prepared query ID；
- 语义模型指纹；
- EXPLAIN 计划；
- 最终 SQL；
- 列名；
- 行数；
- 有限结果预览。

这样用户可以区分“Agent 的解释”和“实际执行过的查询”。

## 会话与恢复

浏览器通过 HttpOnly 匿名 Cookie 区分会话。

后台 Analysis Run 独立于一次 HTTP 请求存在，所以：

- 刷新页面后可以恢复运行状态；
- SSE 断线后可以重新订阅；
- 已完成步骤不会因重连丢失；
- 失败步骤可以单独重试；
- 历史查询和图表可以恢复。

## 开发命令

根目录统一提供：

```bash
npm test
npm run lint
npm run typecheck
npm run build
```

对应行为：

```text
npm test       → backend unittest
npm run lint   → backend ruff
npm run typecheck → backend mypy + frontend typecheck
npm run build  → frontend production build
```

修改 Agent / Query Engine / Semantic Model 时，建议先跑最小相关测试，再跑完整命令集合。

## 手工验收清单

1. 新建会话后列表立即出现。
2. 第一条消息后标题正确更新。
3. 连续追问能使用同一会话上下文。
4. 复杂分析先出现计划，再按步骤推进。
5. 同一时刻只有当前步骤处于执行态。
6. 刷新页面后分析状态可恢复。
7. 制造某一步失败，确认已完成步骤不被重写。
8. 重试失败步骤后可继续运行。
9. 数据分析结果同时包含回答、查询轨迹和图表。
10. 删除会话后，对应消息、运行、事件与 checkpoint 不再恢复。
11. 尝试写入 SQL、伪造 prepared query ID 或查询非白名单表，确认被拒绝。

## 项目边界

CommerceTrace 当前面向 **本地 / 演示型电商分析场景**。如果接入真实生产数据库，建议进一步增加：

- 企业身份认证 / RBAC；
- 数据库凭据隔离；
- 更严格的数据权限过滤；
- 指标版本发布与变更审计；
- 查询成本控制；
- 模型调用审计和预算限制；
- 更细粒度的 PII 脱敏；
- 生产级任务队列与可观测性。

LLM 负责帮助解释数据，不应替代经过治理的财务、经营或审计口径。

## 文档

深入理解工作流、状态转移、查询安全门和能力边界：

```text
docs/DataAgent工作流设计与实现详解.md
```

## License

详见仓库中的 License / 项目声明（如后续调整，以仓库当前文件为准）。
