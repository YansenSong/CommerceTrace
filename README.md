# CommerceTrace

CommerceTrace（商迹）是一个基于 LangChain 的中文电商经营分析 Agent。用户可以用自然语言查询本地 SQLite 数据、核验 SQL 轨迹并生成 Plotly 图表。

## 架构

```text
React
  │ REST JSON
FastAPI
  ├─ LangChain create_agent
  │    ├─ ChatDeepSeek
  │    ├─ get_schema / run_sql / visualize_data
  │    ├─ 长对话摘要中间件（SummarizationMiddleware）
  │    └─ 业务指标口径与 schema 目录（注入系统提示词）
  ├─ LangGraph SQLite checkpointer
  └─ 匿名用户与会话目录（HttpOnly cookie + SQLite）
```

项目仅保留业务必要的自有代码：SQL AST 安全校验、只读执行、图表生成、提示词与 schema 目录、会话目录和访问控制。模型适配、消息协议、工具调用循环、调用限制、长对话摘要与 checkpoint 均由 LangChain/LangGraph 提供。

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
COMMERCE_TRACE_MODEL=deepseek-chat
COMMERCE_TRACE_DATABASE_PATH=data/commerce_trace.db
COMMERCE_TRACE_AGENT_STATE_PATH=data/agent_state.db
```

`COMMERCE_TRACE_MODEL_API_KEY` 必填，未配置时后端启动即报错。其余可选变量：

| 变量 | 默认值 | 作用 |
|---|---|---|
| `COMMERCE_TRACE_MODEL_BASE_URL` | `https://api.deepseek.com` | 模型服务地址 |
| `COMMERCE_TRACE_MODEL` | `deepseek-chat` | 模型名称 |
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
| `DELETE` | `/api/conversations/{id}` | 永久删除会话和 checkpoints |

发送消息：

```json
{
  "message": "按地区展示销售额"
}
```

响应：

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
- 只读执行通过内存库 `ATTACH` 业务库并开启 `PRAGMA query_only = ON` 完成，单条语句带执行超时。
- `DISTINCT` 值级探索仅允许低基数字段（地区、获客渠道、订单渠道、订单状态、品类、支付方式）；客户姓名、地址、电话、邮箱等敏感字段禁止。
- 历史查询轨迹只保存 SQL、列名、行数和最多 20 行预览。

## 代码检查

项目按产品决策不包含自动化测试。可运行：

```bash
npm run lint
npm run typecheck
npm run build
```

## 手工验收清单

1. 新建会话，确认列表立即出现“新会话”。
2. 发送第一条消息，确认标题更新为清理空白后的前 6 个字符。
3. 连续追问，确认 Agent 能引用同一会话的上下文。
4. 请求数据分析与图表，确认回答、查询预览和 Plotly 图表可见。
5. 刷新页面并打开历史会话，确认最终消息、查询和图表恢复。
6. 永久删除会话，确认列表、历史消息和 Agent checkpoint 均不可恢复。
7. 尝试写入 SQL 或访问非白名单表，确认工具拒绝执行。
