# CommerceTrace

CommerceTrace（商迹）是一个基于 LangChain 的中文电商经营分析 Agent。用户可以用自然语言查询本地 SQLite 数据、核验 SQL 轨迹并生成 Plotly 图表。

## 架构

```text
React
  │ REST JSON + SSE
FastAPI
  ├─ AnalysisCoordinator（持久化后台分析运行）
  │    └─ AnalysisWorkflow
  │         ├─ 生成并发布可见分析计划
  │         ├─ 逐项执行、完成条件判定与受限修订
  │         └─ 基于查询结果生成结论
  ├─ LangChain create_agent + ChatDeepSeek
  │    ├─ get_schema
  │    ├─ plan_metric_query / plan_query → prepared_query_id → run_sql
  │    └─ visualize_data
  ├─ 版本化业务语义模型（Schema、关系、指标与治理规则）
  ├─ QueryEngine（SQL AST 校验、EXPLAIN、只读执行与幂等结果）
  ├─ LangGraph SQLite checkpointer
  └─ 会话、分析运行、事件与查询工件（HttpOnly cookie + SQLite）
```

DataAgent 采用“单 Agent 决策 + 确定性工作流”：模型负责制定带前置依赖的业务分析步骤、解释查询结果并逐条判断完成条件，后端状态机强制计划只能逐项推进、已完成步骤不可改写、SQL 必须先准备再执行。查询结果未满足所有完成条件时，运行保留查询工件和未满足解释，以 `partial` 结束并可重试失败步骤。复杂运行独立于一次 HTTP 请求存在，浏览器可通过 SSE 查看计划进度并在断线后恢复。

完整的领域模型、状态转移、执行时序、查询安全门和当前能力边界见 [DataAgent 工作流设计与实现详解](docs/DataAgent工作流设计与实现详解.md)。

## 环境要求

- Python 3.12
- [UV](https://docs.astral.sh/uv/)
- Node.js 20+（Vite 7 要求 20.19+）
- 模型 API key（默认使用 DeepSeek，OpenAI 兼容 Chat Completions）

## 安装

```bash
npm run sync
```

在项目根目录的 `.env` 中配置（后端固定从仓库根目录读取）：

```dotenv
COMMERCE_TRACE_MODEL_API_KEY=sk-...
COMMERCE_TRACE_MODEL_BASE_URL=https://api.deepseek.com
COMMERCE_TRACE_MODEL=deepseek-v4-flash
COMMERCE_TRACE_DATABASE_PATH=data/commerce_trace.db
COMMERCE_TRACE_AGENT_STATE_PATH=data/agent_state.db
```

`COMMERCE_TRACE_MODEL_API_KEY` 必填，未配置时后端启动即报错。其余可选变量：

| 变量 | 默认值 | 作用 |
|---|---|---|
| `COMMERCE_TRACE_MODEL_BASE_URL` | `https://api.deepseek.com` | 模型服务地址 |
| `COMMERCE_TRACE_MODEL` | `deepseek-v4-flash` | 模型名称；当前结构化 Agent 工作流显式使用非 Thinking 模式 |
| `COMMERCE_TRACE_STATEMENT_TIMEOUT_MS` | `5000` | 单条 SQL 执行超时（毫秒） |
| `COMMERCE_TRACE_MODEL_TIMEOUT_SECONDS` | `60` | 模型请求超时（秒） |
| `COMMERCE_TRACE_MAX_RESULT_ROWS` | `500` | 普通查询结果行数上限 |
| `COMMERCE_TRACE_MAX_DISTINCT_VALUES` | `50` | 值级探索行数上限 |
| `COMMERCE_TRACE_COOKIE_SECURE` | `false` | 会话 cookie 的 Secure 标志 |

依赖由 `backend/uv.lock` 和 `frontend/package-lock.json` 锁定。当前 LangChain 核心依赖使用确切版本，不使用预览版。

后端直接读取 `COMMERCE_TRACE_DATABASE_PATH` 指向的业务数据库（`data/` 不纳入版本控制）。安装依赖后运行下面的命令初始化业务库：应用 `migrations/*.sql` 并生成固定种子的示例数据。

```bash
uv run --project backend commerce-trace init --profile test
```

命令说明：

| 命令 | 作用 |
|---|---|
| `commerce-trace migrate` | 仅应用 `migrations/*.sql` |
| `commerce-trace generate-data [--profile test\|demo]` | 迁移后生成并写入示例数据 |
| `commerce-trace init [--profile test\|demo] [--no-data] [--if-empty]` | 迁移；默认生成数据，`--no-data` 跳过，`--if-empty` 仅在无数据时生成 |

`test` 为小规模配置（80 客户 / 300 订单），`demo` 为 10 万订单规模。数据生成依赖（Faker、pyyaml）随 `uv sync --extra data` 安装，运行前请先执行 `npm run sync`。

## 运行

分别启动后端与前端：

```bash
npm run backend
npm run frontend
```

或一条命令同时启动两者：

```bash
npm run dev
```

前端地址为 `http://localhost:5173`，后端地址为 `http://localhost:8000`。

## API

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/conversations` | 新建会话 |
| `GET` | `/api/conversations` | 分页列出当前匿名用户的会话 |
| `GET` | `/api/conversations/{id}/messages` | 获取历史消息与查询/图表快照 |
| `POST` | `/api/conversations/{id}/messages` | 发送消息并获得最终 JSON 回答 |
| `POST` | `/api/conversations/{id}/analysis-runs` | 创建后台分析运行，立即返回运行状态 |
| `GET` | `/api/conversations/{id}/analysis-runs/latest` | 获取会话最近一次分析运行 |
| `GET` | `/api/analysis-runs/{run_id}` | 获取计划、步骤、查询工件与最终状态 |
| `GET` | `/api/analysis-runs/{run_id}/events` | 通过 SSE 接收有序运行事件 |
| `POST` | `/api/analysis-runs/{run_id}/retry` | 重试失败步骤，不改写已完成历史 |
| `DELETE` | `/api/conversations/{id}` | 永久删除会话和 checkpoints |

发送消息：

```json
{
  "message": "按地区展示销售额"
}
```

新的交互界面使用后台分析运行接口。创建成功返回 `202`，其中包含 `run_id`、当前状态以及随后生成的分析计划；客户端再订阅事件流。原同步消息接口继续保留兼容性。

同步接口响应：

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

浏览器通过 `HttpOnly` 匿名 cookie 隔离会话。

## 数据与安全

- `data/commerce_trace.db`：电商业务数据，只读访问。
- `data/agent_state.db`：LangGraph checkpoints、会话目录与历史快照。
- SQL 仅允许访问 `ecommerce` schema 中的八张白名单表。
- `sqlglot` 校验只读 AST、危险函数、敏感值探索和行数上限。
- 查询准备前必须通过 `get_schema` 取得所有引用表的列级上下文；紧凑表目录不会隐式授予 Schema 上下文。
- 受治理核心指标由 `plan_metric_query` 按指标、维度和同义词确定性展开 SQL；其他只读 SQL 使用 `plan_query`。
- `run_sql` 不接受任意 SQL，只能执行准备阶段签发且绑定语义模型指纹的 `prepared_query_id`。
- 只读执行通过内存库 `ATTACH` 业务库并开启 `PRAGMA query_only = ON` 完成，单条语句带执行超时。
- `DISTINCT` 值级探索仅允许低基数字段（地区、获客渠道、订单渠道、订单状态、品类、支付方式）；客户姓名、地址、电话、邮箱等敏感字段禁止。
- 历史查询轨迹保存 prepared query ID、语义指纹、EXPLAIN 计划、SQL、列名、行数和最多 20 行预览。

## 代码检查

后端行为测试覆盖语义模型、受控查询、分析运行状态机、动态计划、步骤完成条件、SSE 恢复和失败重试。运行：

```bash
npm test
npm run lint
npm run typecheck
npm run build
```

## 手工验收清单

1. 新建会话，确认列表立即出现“新会话”。
2. 发送第一条消息，确认标题更新为清理空白后的前 6 个字符。
3. 连续追问，确认 Agent 能引用同一会话的上下文。
4. 请求复杂数据分析，确认计划先出现、同一时间只有一步进行中、完成步骤被划去。
5. 刷新页面或断开网络后重新打开会话，确认计划、步骤和当前进度恢复。
6. 让某一步失败，确认错误留在对应步骤且可单独重试，已完成步骤不被改写。
7. 请求数据分析与图表，确认回答、查询预览和 Plotly 图表可见。
8. 刷新页面并打开历史会话，确认最终消息、查询和图表恢复。
9. 永久删除会话，确认列表、分析运行、事件和 Agent checkpoint 均不可恢复。
10. 尝试写入 SQL、伪造 `prepared_query_id` 或访问非白名单表，确认工具拒绝执行。
