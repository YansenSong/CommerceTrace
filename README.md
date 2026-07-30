# CommerceTrace

CommerceTrace（商迹）是一个基于 LangChain 的中文电商经营分析 Agent。用户可以用自然语言查询本地 SQLite 数据、核验 SQL 轨迹并生成 Plotly 图表。

## 架构

```text
React
  │ REST JSON
FastAPI
  ├─ LangChain create_agent
  │    ├─ ChatDeepSeek
  │    ├─ get_schema
  │    ├─ run_sql
  │    └─ visualize_data
  ├─ LangGraph SQLite checkpointer
  └─ 匿名用户与会话目录
```

项目仅保留业务必要的自有代码：SQL AST 安全校验、只读执行、图表生成、会话目录和访问控制。模型适配、消息协议、工具调用循环、调用限制、长对话摘要与 checkpoint 均由 LangChain/LangGraph 提供。

## 环境要求

- Python 3.12
- [UV](https://docs.astral.sh/uv/)
- Node.js 20+
- DeepSeek API key

## 安装与初始化

```bash
npm run sync
npm run init
```

在 `backend/.env` 中配置：

```dotenv
COMMERCE_TRACE_DEEPSEEK_API_KEY=sk-...
COMMERCE_TRACE_DEEPSEEK_MODEL=deepseek-chat
COMMERCE_TRACE_DATABASE_PATH=data/commerce_trace.db
COMMERCE_TRACE_AGENT_STATE_PATH=data/agent_state.db
```

依赖由 `backend/uv.lock` 和 `frontend/package-lock.json` 锁定。当前 LangChain 核心依赖使用确切版本，不使用预览版。

## 运行

分别启动后端与前端：

```bash
npm run backend
npm run frontend
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
| `GET` | `/health` | 检查业务库、状态库和 Agent |

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

浏览器通过 `HttpOnly` 匿名 cookie 隔离会话。不同会话可并行；同一会话同时发送第二条消息会返回 `409 conversation_busy`。单次请求总超时 120 秒。

## 数据与安全

- `data/commerce_trace.db`：电商业务数据，只读访问。
- `data/agent_state.db`：LangGraph checkpoints、会话目录与历史快照。
- SQL 仅允许访问 `ecommerce` schema 中的八张白名单表。
- `sqlglot` 校验只读 AST、危险函数、敏感值探索和行数上限。
- 历史查询轨迹只保存 SQL、列名、行数和最多 20 行预览。
- LangSmith 仅在显式配置官方环境变量时启用，默认关闭。

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
6. 同时向同一会话发送请求，确认第二个请求收到 `409`。
7. 永久删除会话，确认列表、历史消息和 Agent checkpoint 均不可恢复。
8. 尝试写入 SQL 或访问非白名单表，确认工具拒绝执行。
