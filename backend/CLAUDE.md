# CommerceTrace Backend

中文电商经营分析 Agent 的后端服务，FastAPI + SQLite + LLM 函数调用。

---

## 核心域 (`src/commerce_trace/`)

### 数据模型与契约

| 文件 | 作用 |
|---|---|
| `contracts.py` | 全项目共享的 Pydantic 模型和类型：`StreamEvent`（SSE 事件）、`ToolCall`、`ToolSchema`、`ToolSuccess`/`ToolFailure`、`LlmMessage`/`LlmResponse`、`Evidence`（查询证据）、`Chart`（图表） |
| `config.py` | `Settings` 类，通过 `COMMERCE_TRACE_` 前缀环境变量和 `.env` 文件加载所有配置项（数据库路径、模型参数、超时、限额等） |

### LLM 调用

| 文件 | 作用 |
|---|---|
| `llm.py` | `LlmService` 抽象 + `OpenAICompatibleLlm` 实现，将 `ToolSchema` 列表转换为 OpenAI function calling 格式，支持 HTTP 代理 |

### SQL 安全

| 文件 | 作用 |
|---|---|
| `sql_safety.py` | `SqlSafetyPolicy`：用 sqlglot 解析和验证 SQL — 只允许 SELECT/UNION，白名单 schema 和表，禁止危险函数，限制行数和 DISTINCT 探索字段；返回 `ValidatedSql` 规范化结果 |

### 上下文装配

| 文件 | 作用 |
|---|---|
| `context.py` | `ContextAssembler`：装配发送给 LLM 的 system prompt 上下文 — 包含 schema catalog（硬编码的表/列定义）、业务规则（Markdown）、指标定义（YAML）、golden SQL 示例；生成 schema 指纹用于版本校验 |

### Agent 核心 (`agent/`)

| 文件 | 作用 |
|---|---|
| `agent/__init__.py` | 只公开 `Agent` 类 |
| `agent/core.py` | `Agent` 主循环：接收用户问题 → 检查不安全请求 → 装配上下文 → LLM 调用循环（function calling）→ 工具执行 → Evidence/Chart 收集 → 暂停 → 最终答案。包含 system prompt |
| `agent/state.py` | `RequestState`：单次请求的有限状态机（STARTED → CONTEXT_READY → EXECUTING → SYNTHESIZING → COMPLETED/FAILED/INCOMPLETE），管理消息历史、工具预算（SQL 调用次数、重试次数）、工具迭代计数 |
| `agent/synthesis.py` | `synthesize()`：LLM 原始输出的后处理 — 过滤幻觉的 `[ev_xxx]` 引用和 Markdown 图表语法、补全"结论"/"证据"/"口径说明"结构、注入不完整场景的友好消息 |

### 工具系统 (`agent/tools/`)

| 文件 | 作用 |
|---|---|
| `agent/tools/__init__.py` | 工具模块对外导出，提供 `ToolExecutionContext` 别名用于向后兼容 |
| `agent/tools/base.py` | `Tool[ArgsT]` 抽象基类（属性：`kind`/`name`/`description`，方法：`get_args_schema()`/`execute()`/`schema()`，可选生命周期钩子：`on_success()`/`on_failure()`）；`ToolContext`（携带 user_id、conversation_id、request_id、tool_call_id、store、跨工具共享的 `query_results`、Evidence/Chart 收集列表）；`SqlExecutor` 协议；`FakeSqlExecutor` 测试替身 |
| `agent/tools/models.py` | 工具参数 Pydantic 模型：`RunSqlArgs`（sql/purpose/expected_columns）、`VisualizeDataArgs`（evidence_id/chart_type/title 等） |
| `agent/tools/registry.py` | `ToolRegistry`：工具注册、`schemas()`（LLM 可见）、`tool_kind()`（预算策略查询）、`transform_args()`（扩展钩子）、`execute()`（含参数验证、生命周期钩子调度、错误处理）；`build_default_registry()` 工厂函数 |
| `agent/tools/run_sql.py` | `RunSqlTool`：执行只读 SQLite 查询，SQL 安全校检、结果哈希、写入 `context.query_results` 供后续图表工具使用；`on_success()` 创建 `Evidence` 并持久化 |
| `agent/tools/visualize_data.py` | `VisualizeDataTool`：从 `context.query_results` 中按 evidence_id 查找数据，生成 Plotly JSON（metric_card/bar/line/pie）；`on_success()` 持久化 Chart |

### 持久化 (`persistence/`)

| 文件 | 作用 |
|---|---|
| `persistence/__init__.py` | 导出 `ConversationLedger`、`InMemoryStore` 和所有 SQLite 相关类 |
| `persistence/store.py` | `ConversationLedger` 协议：定义持久化接口（会话/消息/事件/工具调用/结果/证据/图表的增删查）；`InMemoryStore` 实现：用内存字典完成的测试用存储 |
| `persistence/sqlite.py` | `SQLiteResources`（异步连接管理 + 线程锁）、`SQLiteSchemaProvider`（运行时校验 schema catalog 与实际数据库一致）、`SQLiteStore`（完整的 SQLite 持久化实现）、`SQLiteSqlExecutor`（只读 SQLite 执行器，含超时保护和多库 ATTACH） |

### 运行时装配

| 文件 | 作用 |
|---|---|
| `runtime.py` | `build_runtime()`：将所有组件装配在一起 — SQLite 连接 → 持久化 store → SQL 执行器 → 安全策略 → LLM 客户端 → 工具注册表 → Context 装配器 → Agent；`FeatureConfiguration` 控制功能开关（知识库/golden examples/SQL 重试） |

### 测试辅助

| 文件 | 作用 |
|---|---|
| `testing.py` | `ScriptedLlm`（确定性 LLM，始终返回 run_sql 工具调用，失败时可重试一次）；`build_test_agent()`（用 FakeSqlExecutor + InMemoryStore 快速构建测试 Agent） |

### Web API

| 文件 | 作用 |
|---|---|
| `api.py` | FastAPI 应用：`POST /api/chat`（SSE 流式聊天）、`GET /api/conversations`（历史列表）、`GET /api/conversations/{id}`（对话回放）、`GET /health`；处理匿名用户 cookie，CORS 配置 |

### 运维工具 (`operations/`)

| 文件 | 作用 |
|---|---|
| `operations/__init__.py` | 包初始化 |
| `operations/cli.py` | CLI 入口 `commerce-trace`：`init`（初始化数据库+生成数据）、`migrate`（应用 SQL 迁移）、`generate-data`（生成测试/演示数据）、`evaluate`（跑评估数据集）、`ablation`（A-D 消融实验） |
| `operations/evaluation.py` | `EvaluationDataset`/`EvaluationCase`/`CaseResult`/`EvaluationReport` 模型；`run_evaluation()` 驱动 Agent 跑数据集并计算通过率/证据完整性/SQL 成功率；支持消融报告 |
