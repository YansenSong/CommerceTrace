# CommerceTrace Backend

FastAPI + LangChain 1.x + LangGraph SQLite checkpoint 的中文电商分析 Agent。

## 结构

- `api.py`：REST API、匿名 cookie、会话所有权与错误契约。
- `api.py`：组装 ChatDeepSeek、Agent、后台分析协调器、checkpointer 和存储。
- `analysis/`：分析运行状态机、顺序工作流、受限计划修订和后台任务协调。
- `semantic.py`：业务对象、关系、指标、维度、同义词与治理规则的版本化唯一事实源。
- `query_engine.py`：`prepare → prepared_query_id → execute` 受控查询接口。
- `agent/core.py`：`create_agent`、分析计划/步骤适配、长对话摘要与证据总结。
- `agent/tools/`：`get_schema`、`plan_metric_query`、`plan_query`、`run_sql`、`visualize_data` 五个 LangChain 工具；`run_sql` 只接受准备后的 ID。
- `agent/sql_safety.py`：必须保留的 SQL AST 白名单安全边界。
- `agent/memory_middleware.py`：确认后记忆的 few-shot 注入中间件（不改动会话状态）。
- `memory/store.py`：确认后记忆——Git 跟踪的 Markdown 真相源（`knowledge/sql/*.md`）与 token 重叠召回。
- `persistence/conversations.py`：用户可见会话目录与最终消息快照。
- `persistence/analysis_runs.py`：分析运行快照和只追加的有序事件。
- `persistence/sqlite.py`：只读业务查询执行器。

## 数据库

- `data/commerce_trace.db`：只读电商业务数据。
- `data/agent_state.db`：LangGraph checkpoints、会话目录与历史消息。

## API

- `POST /api/conversations`
- `GET /api/conversations`
- `GET /api/conversations/{id}/messages`
- `POST /api/conversations/{id}/messages`
- `POST /api/conversations/{id}/analysis-runs`
- `GET /api/conversations/{id}/analysis-runs/latest`
- `GET /api/analysis-runs/{run_id}`
- `GET /api/analysis-runs/{run_id}/events`（SSE）
- `POST /api/analysis-runs/{run_id}/retry`
- `DELETE /api/conversations/{id}`
- `POST /api/knowledge`（确认后记忆）
- `GET /api/knowledge`
- `DELETE /api/knowledge/{slug}`

提交前运行 `npm test`、`npm run lint`、`npm run typecheck` 和 `npm run build`，
并按照 README 的手工验收清单检查核心流程。
