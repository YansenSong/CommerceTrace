# CommerceTrace（商迹）

CommerceTrace 是一个面向中文电商经营分析的、证据驱动的单 Agent MVP。它把自然语言问题转换为受控的只读 SQL，通过 SSE 持续展示计划、工具状态、Evidence、图表和答案，并让每个定量结论都能回到本次真实执行的查询。

项目当前聚焦 PostgreSQL 上的 8 张电商业务表，不宣称支持多数据库、多 Agent、严格因果推断或生产级高可用。

## 能力

- 匿名 Cookie 身份、对话历史、完整 SSE 事件回放和用户隔离
- 确定性 Fake LLM，以及可选的 OpenAI-compatible 异步 Tool Calling 客户端
- 完整 Schema 首轮注入、版本化中文指标/规则和 BGE 中文记忆检索
- SQLGlot AST 白名单、PostgreSQL 只读角色、只读事务、超时与结果上限
- 最多 10 轮工具、5 次业务 SQL、同一目的最多 2 次修正
- Evidence 引用、受控 Plotly 指标卡/柱状图/折线图/饼图
- Candidate、Trusted、Stale、Rejected 四态记忆与 Golden 离线回放
- 固定种子合成数据、60 个中文评测问题、Cold/Warm 和 A–D 消融报告

## 快速开始

推荐使用 Docker Compose 从空环境启动：

```bash
cp .env.example .env
docker compose up --build
```

初始化容器会依次执行迁移、固定种子数据生成、Trusted Memory 载入和两个 ChromaDB 索引重建。首次运行需要下载容器镜像、Python/Node 依赖和 `BAAI/bge-small-zh-v1.5`，因此会比后续启动慢。

就绪后访问：

- Web：http://localhost:3000
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

推荐演示问题：

```text
按地区展示销售额
七月销售额为什么下降？
销售额怎么样？
DELETE FROM ecommerce.orders
```

前两条分别展示简单查询和有限多步归因；第三条触发必要澄清；第四条应在执行前被拒绝。

停止服务但保留数据：

```bash
docker compose down
```

## 使用 UV 进行本地开发

Python 项目由 [UV](https://docs.astral.sh/uv/) 管理，锁文件是 `backend/uv.lock`。Python 3.10 及以上可执行：

```bash
UV_CACHE_DIR=/tmp/commerce-trace-uv-cache uv sync \
  --project backend --extra test --extra data --extra memory
npm --prefix frontend install
```

如果本机 PostgreSQL 地址不是 Compose 服务名，请先覆盖连接配置：

```bash
export COMMERCE_TRACE_DATABASE_URL=postgresql://commerce_app:commerce_app@localhost:5432/commerce_trace
export COMMERCE_TRACE_QUERY_DATABASE_URL=postgresql://commerce_reader:commerce_reader@localhost:5432/commerce_trace
```

初始化并启动：

```bash
UV_CACHE_DIR=/tmp/commerce-trace-uv-cache uv run --project backend commerce-trace init --profile test
UV_CACHE_DIR=/tmp/commerce-trace-uv-cache uv run --project backend \
  uvicorn commerce_trace.api:app --reload --port 8000
npm --prefix frontend run dev
```

`test` 数据档用于快速验证；`demo` 档生成更大的展示数据。相同档位和种子会生成相同数据哈希。

## 配置

Compose 读取根目录 `.env`。主要变量如下：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATA_PROFILE` | `test` | `test` 或 `demo` |
| `LLM_MODE` | `fake` | `fake` 或 `openai` |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible `/chat/completions` 基地址 |
| `OPENAI_API_KEY` | 空 | 仅 `openai` 模式需要 |
| `OPENAI_MODEL` | `gpt-4.1-mini` | 服务端可用模型名 |
| `COOKIE_SECURE` | `false` | HTTPS 部署时设为 `true` |

更多后端配置及预算上限见 `backend/src/commerce_trace/config.py`。不要提交真实密钥；`.env` 已被 Git 忽略。

## 架构与权威源

```text
React ──POST + SSE──> FastAPI ──> 单 Agent 状态机 ──> Tool Registry
                              │                    ├─ run_sql
                              │                    ├─ visualize_data
                              │                    └─ search_memory
                              ├─ PostgreSQL：业务数据、对话、Evidence、记忆登记
                              ├─ knowledge/：规则、指标、Golden SQL
                              └─ ChromaDB：可删除、可重建的派生召回索引
```

- Schema 的权威来源是 PostgreSQL 对应的小型固定业务模型，运行时以稳定版本和指纹携带。
- 规则、指标和 Golden SQL 的权威来源是 `knowledge/`。
- 对话、Evidence 和运行时记忆登记的权威来源是 PostgreSQL。
- ChromaDB 不保存完整对话或历史结果，只保存能由以上权威源重建的两个索引。

Agent 在首次模型调用前装配完整 Schema 与相关业务上下文。工具结果经后端形成 Evidence；最终答案由后端合成并校验引用。只有最终答案采用的成功 SQL 才登记为 Candidate，Candidate 不会因重复成功自动成为 Trusted。

## SQL 与数据安全

应用层仅允许单条 PostgreSQL `SELECT`、`WITH ... SELECT` 和受控 `EXPLAIN`，并限制可访问表、危险函数、低基数 `DISTINCT` 字段和返回行数。查询连接同时使用只能读取 `ecommerce` Schema 的 `commerce_reader`，并在只读事务中设置语句超时。

用户问题中出现写入意图、`agent_app` 越权访问、连接信息、密码或系统提示词请求时，会在上下文和模型调用前拒绝。数据库技术错误会映射为可重试的安全错误，不进入 SSE、Evidence 或模型上下文。

## 测试与构建

```bash
make test
make lint
make typecheck
make build
```

等价的后端完整测试命令：

```bash
cd backend
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.. \
UV_CACHE_DIR=/tmp/commerce-trace-uv-cache \
uv run pytest -p pytest_asyncio.plugin tests ../data_generator/test_generate.py
```

普通测试使用 Fake LLM、Fake SQL Executor 和内存存储，不需要网络或真实模型密钥。需要 PostgreSQL、真实模型或真实 Embedding 的测试应分别标记为 `integration`、`model`、`embedding`。

## 评测、记忆回放与实验

以下命令需要已初始化的 PostgreSQL。所有报告同时记录数据种子、模型模式、知识版本和 Schema 版本，输出到未纳入版本控制的 `reports/`。

```bash
# 运行约 60 个中文问题；调试时可加 --limit 10
UV_CACHE_DIR=/tmp/commerce-trace-uv-cache uv run --project backend commerce-trace evaluate

# 用版本化 Golden Case 重新执行并决定 Candidate 的生命周期
UV_CACHE_DIR=/tmp/commerce-trace-uv-cache uv run --project backend commerce-trace replay-memory

# 清空 Candidate 后执行语义不同的 Cold/Warm 问题
UV_CACHE_DIR=/tmp/commerce-trace-uv-cache uv run --project backend \
  commerce-trace memory-experiment --limit 10

# A Schema+Prompt；B +规则/Trusted；C +执行反馈；D +Candidate
UV_CACHE_DIR=/tmp/commerce-trace-uv-cache uv run --project backend commerce-trace ablation
```

评测报告包含总体与分类通过率、归因通过率、首次 SQL 成功率、自我修正率、澄清准确率、危险请求拦截率、Evidence 引用完整率、调用次数、延迟以及模型返回的 Token 用量。Fake LLM 评测主要验证公开行为与执行链路；Golden 结果哈希和植入场景断言才是正式正确性标签。README 不预先宣称准确率、延迟或记忆提升，实际结果以每次生成的报告为准。

`knowledge/golden_sql/` 中缺少 `expected.value` 的 Case 会在回放报告中明确标为 `skipped`，不会借助运行时结果自动晋升 Candidate。

## API

- `POST /api/chat`：提交 `{ "question": "...", "conversation_id": "可选" }`，返回 `text/event-stream`
- `GET /api/conversations?limit=50&offset=0`：当前匿名用户的历史列表
- `GET /api/conversations/{id}`：按原始顺序返回消息、事件、Evidence 和图表
- `GET /health`：分别报告数据库、数据集和知识登记状态

SSE 事件都包含唯一 `event_id`、`conversation_id`、`request_id`、时间戳和结构化载荷。前端按 `event_id` 去重，同一展示模型同时用于实时流与历史回放。

## 与 Vanna 的关系

设计和实现过程参考了 Vanna 项目中值得复用的产品理念：单 Agent Tool Calling、显式工具契约、流式工具状态和面向数据问答的交互方式。

CommerceTrace 没有复制 Vanna 的框架代码，也没有尝试重建其平台能力。以下边界由本项目独立设计和实现：

- Evidence 作为答案完成前的后端硬约束
- SQL AST 与数据库只读角色组成的双层防线
- Candidate/Trusted/Stale/Rejected 生命周期及 Golden 离线回放
- PostgreSQL、版本库知识和 ChromaDB 之间的权威源划分
- 固定场景数据、中文行为评测、Cold/Warm 与架构消融

详细设计见 `docs/specs/2026-07-25-data-agent-design.md`，MVP 验收范围见 `docs/specs/2026-07-25-chinese-ecommerce-data-agent-mvp-spec.md`。

## 常见问题

- `database: unavailable`：确认 PostgreSQL 健康并检查两个连接 URL；查询 URL 必须使用 `commerce_reader`。
- `dataset: missing`：执行 `commerce-trace generate-data --profile test`。
- `knowledge: missing`：执行 `commerce-trace bootstrap-memory`，需要时再运行 `rebuild-memory-index`。
- 首次索引构建很慢：BGE 模型需要首次下载；后续使用本地缓存和 Chroma 卷。
- 本地 UV 缓存不可写：像 Makefile 一样设置 `UV_CACHE_DIR=/tmp/commerce-trace-uv-cache`。
- 浏览器没有历史：匿名身份存于 HttpOnly Cookie；更换浏览器配置或清除 Cookie 会得到新的隔离身份。
