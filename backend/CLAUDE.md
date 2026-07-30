# CommerceTrace Backend

FastAPI + LangChain 1.x + LangGraph SQLite checkpoint 的中文电商分析 Agent。

## 结构

- `api.py`：REST API、匿名 cookie、会话所有权与错误契约。
- `runtime.py`：组装 ChatDeepSeek、LangChain Agent、checkpointer 和存储。
- `agent/service.py`：`create_agent`、标准 middleware、超时和会话并发控制。
- `agent/tools.py`：`get_schema`、`run_sql`、`visualize_data` 三个 LangChain tools。
- `agent/sql_safety.py`：必须保留的 SQL AST 白名单安全边界。
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

项目不包含自动化测试。提交前至少运行 `npm run lint`、`npm run typecheck`
和 `npm run build`，并按照 README 的手工验收清单检查核心流程。
