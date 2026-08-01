# CommerceTrace Backend

FastAPI + LangChain 1.x + LangGraph SQLite checkpoint 的中文电商分析 Agent。

## 结构

- `api.py`：REST API、匿名 cookie、会话所有权与错误契约。
- `runtime.py`：组装 ChatDeepSeek、LangChain Agent、checkpointer 和存储。
- `agent/service.py`：`create_agent`、标准 middleware、超时和会话并发控制。
- `agent/tools/`：`get_schema`、`plan_query`、`run_sql`、`visualize_data` 四个 LangChain tools。
- `agent/sql_safety.py`：必须保留的 SQL AST 白名单安全边界。
- `agent/memory_middleware.py`：确认后记忆的 few-shot 注入中间件（不改动会话状态）。
- `memory/store.py`：确认后记忆——Git 跟踪的 Markdown 真相源（`knowledge/sql/*.md`）与 token 重叠召回。
- `persistence/conversations.py`：用户可见会话目录与最终消息快照。
- `persistence/sqlite.py`：只读业务查询执行器。

## 数据库

- `data/commerce_trace.db`：只读电商业务数据。
- `data/agent_state.db`：LangGraph checkpoints、会话目录与历史消息。

## API

- `POST /api/conversations`
- `GET /api/conversations`
- `GET /api/conversations/{id}/messages`
- `POST /api/conversations/{id}/messages`
- `DELETE /api/conversations/{id}`
- `POST /api/knowledge`（确认后记忆）
- `GET /api/knowledge`
- `DELETE /api/knowledge/{slug}`

项目不包含自动化测试。提交前至少运行 `npm run lint`、`npm run typecheck`
和 `npm run build`，并按照 README 的手工验收清单检查核心流程。
