# WrenAI 项目亮点与源码实现分析

> 调研范围：本地WrenAI项目中的源码、测试、README 与内置文档；不把 roadmap 当成现有能力。

## 执行摘要

WrenAI 当前形态并不是一个把大模型、提示词和数据库简单串起来的“聊天式 Text-to-SQL 应用”，而是一套面向外部 Agent 的 **GenBI 上下文与执行基础设施**。大模型负责理解问题、选择工作流和修复错误；WrenAI 则提供可版本化的业务语义（MDL）、按需上下文检索、SQL 语义展开、执行前验证、数据源适配、访问控制和可部署的浏览器端 BI 产物。官方架构也明确把系统分为 Agent workflow、Project context、Planning engine、Execution layer 四层（`docs/core/reference/architecture.md`）。

其最值得借鉴的设计有三点：

1. **把业务知识变成仓库资产，而不是一次性 Prompt**：模型、关系、指标、规则以及确认过的 NL→SQL 都是可评审、可提交的文件。
2. **把正确性拆成可编排原语**：检索、`dry-plan`、`dry-run`、结构化错误、执行和成功案例回写均可被 Agent 独立调用，而不是封装在不可见的黑盒链路中。
3. **用稳定核心隔离变化**：Rust/DataFusion 负责语义和权限，Python 负责 Agent/CLI 编排与连接器，SDK/MCP/skills 只是不同接入面。这使同一治理规则可复用到 CLI、Agent 框架和浏览器 WASM。

## 架构与核心链路

```mermaid
flowchart LR
    U[用户问题] --> A[外部 Agent / Skill]
    A --> K[Memory: schema + 已确认 NL-SQL]
    A --> Q[面向 MDL 的 SQL]
    Q --> E[WrenEngine]
    E --> P[sqlglot 策略检查与引用解析]
    P --> X[最小 Manifest 提取]
    X --> R[Rust wren-core / DataFusion]
    R --> C[模型、关系、计算字段、RLAC/CLAC 展开]
    C --> D[dry-plan / dry-run]
    D --> N[Connector]
    N --> DB[(数据源)]
    DB --> O[PyArrow / MCP / SDK 结果]
    O --> M[确认后写入 knowledge/sql/*.md]
    M --> K
```

实际查询主路径集中在 `WrenEngine`：`dry_plan()` 只规划，`query()` 在规划后交给连接器执行，`dry_run()` 则要求连接器做数据库侧校验（`core/wren/src/wren/engine.py:87`、`:107`、`:128`）。内部 `_plan()` 先解析目标方言 SQL、检查策略、提取相关 MDL，再由 `CTERewriter.rewrite()` 生成目标数据库可执行 SQL（`core/wren/src/wren/engine.py:163`；`core/wren/src/wren/mdl/cte_rewriter.py:236`）。这是一个清晰的“生成—规划—验证—执行—记忆”闭环，而不是让 LLM 直连数据库。

## 10 个有价值的功能实现

### 1. MDL 将业务语义变成统一、可治理的查询表面

MDL 不只描述表和列，还容纳关系、计算字段、视图、Cube、Measure、时间维度以及行列级访问控制。项目源文件由 `build_manifest()` 汇总，并编译成 `target/mdl.json`；`validate_project()` 和 `validate_manifest()` 分别处理项目级与 Manifest 级校验（`core/wren/src/wren/context.py:779`、`:849`、`:1589`）。Rust 中 `Model` 明确区分物理列、可见列、`table_reference`、`ref_sql` 和 RLAC，`Column` 则暴露 CLAC（`core/wren-core-base/src/mdl/manifest.rs:360-480`）。

**价值**：Agent 查询的是稳定业务对象，而不是充满历史表、近似字段和隐式 Join 的物理库；底层表或 SQL 改变时，上层问法与指标契约可以保持稳定。

### 2. 查询时只提取相关 Manifest，降低规划成本与上下文耦合

`WrenEngine._plan()` 从 SQL AST 收集表引用，按 SQL 的引号及大小写规则解析为规范模型名，然后调用 `ManifestExtractor.extract_by(tables)`；只有失败且未启用严格策略时才回退到完整 Manifest（`core/wren/src/wren/engine.py:163-228`）。提取器还会把通过关系和 RLAC 条件间接依赖的对象纳入切片，避免“裁剪后权限表达式缺模型”（`core/wren-core-py/src/extractor.rs:162-167`）。

**价值**：这是比“把全库 Schema 塞进 Prompt”更工程化的最小依赖闭包。大型语义模型下能减少 Rust Session 构建与 SQL 展开的工作量，也把一次查询的实际依赖暴露得更清楚。

### 3. CTE 重写把语义模型安全地编译为目标方言 SQL

`CTERewriter` 负责解析和限定标识符、收集查询真正使用的模型/列/视图、调用 wren-core 展开模型 SQL，再把结果注入 CTE；它还专门处理列大小写碰撞、用户自定义 CTE 名称、星号选择、输出别名引用和 View 对模型的传递依赖（`core/wren/src/wren/mdl/cte_rewriter.py:67`、`:236`、`:807`、`:844`、`:978`、`:1056`）。

**价值**：LLM 可以写简洁的“业务 SQL”，复杂关系和计算逻辑由编译器式管线展开。与在 Prompt 中要求模型复制长 SQL 相比，逻辑只定义一次，而且可被确定性测试。

### 4. 正确性被拆成 `dry-plan`、`dry-run` 与结构化错误

`dry_plan()` 不访问数据库即可返回最终方言 SQL，适合人工审阅、Agent 自检与生成链路追踪；`dry_run()` 在同一规划结果上调用各连接器的数据库侧验证；真正执行出错时，`WrenError` 会携带错误码、阶段和 `DIALECT_SQL` 元数据（`core/wren/src/wren/engine.py:87-145`）。这使调用方能够区分 SQL planning、policy check、dry-run 和 execution，而不必靠解析异常字符串判断重试策略。

**价值**：Agent 可在昂贵或高风险执行前发现模型名、字段、方言和数据库错误，并针对失败阶段修复。可观测性目前主要体现在“可见计划 + 结构化错误”，而非成熟审计平台。

### 5. 两层安全治理：SQL 边界检查 + Rust RLAC/CLAC

Python 严格模式首先要求 FROM/JOIN 来源必须是 Manifest 中模型或可见 CTE，并阻断未知表值函数；它还在 AST **所有位置**扫描 `read_csv`、`read_parquet`、`dblink`、`postgres_scan` 等读取器，防止文件遍历、SSRF、跨库读取和数据外带，同时支持运维方额外配置 `denied_functions`（`core/wren/src/wren/policy.py:173-198`、`:221-300`、`:387-419`、`:421-440`）。

Rust 层再根据 Session Property 构造行过滤，并在字段规划时验证列级规则；相关入口包括 `ModelPlanNode::build_rlac_filter()`（`core/wren-core/core/src/logical_plan/analyze/plan.rs:478`）与 `validate_clac_rule()`（`core/wren-core/core/src/logical_plan/analyze/access_control.rs:534`，调用点见 `plan.rs:823`）。大量测试覆盖必填/可选属性、计算字段、别名、跨模型子查询、递归 RLAC 和 CLAC 拒绝（`core/wren-core/core/src/mdl/mod.rs:2040-3902`）。

**价值**：安全规则附着在语义层，调用入口换成 CLI、MCP 或 SDK 后仍能复用，而不是依赖某个 Agent“自觉加 WHERE”。

### 6. Memory 采用“Markdown 真相源 + 可丢弃索引”

确认过的 NL→SQL 以 YAML frontmatter Markdown 保存到 `knowledge/sql/*.md`。`write_query_markdown()` 使用确定性 slug，同一问题更新原文件，并保留用户在 frontmatter 下方写的说明（`core/wren/src/wren/memory/markdown.py:96-180`）。LanceDB 中的 `query_history` 和 `schema_items` 只是派生索引，前者保存问题与 SQL，后者按模型、列、关系、视图、Cube 等粒度建立 embedding 文本（`core/wren/src/wren/memory/store.py:35-64`；`core/wren/src/wren/memory/schema_indexer.py:243-288`）。

**价值**：团队能在 Git 中审查“Agent 学到了什么”，索引损坏可重建，也避免知识被锁在向量库或聊天历史里。

### 7. 小项目全量上下文，大项目语义检索，并提供零依赖降级

`MemoryStore.get_context()` 根据完整 Schema 描述长度选择策略：小于阈值时直接返回结构化全文，较大时在 LanceDB 中按查询、类型、模型和 MDL hash 检索（`core/wren/src/wren/memory/store.py:258-345`）。NL→SQL recall 在安装 memory extra 时使用 LanceDB 语义相似度；否则自动降级为 `GrepIndex`，以 token overlap + 完整问题子串加权排序，并保持确定性 tie-break（`core/wren/src/wren/memory/index_backend.py:72-106`、`:109-140`、`:147-180`）。

**价值**：不是所有项目都值得承担 embedding 模型和向量库成本；该设计让最小安装仍可运行，而大项目可平滑升级到语义检索。

### 8. Connector 接口小而稳定，依赖按数据源延迟加载

所有连接器只需实现 `query(sql, limit)`、`dry_run(sql)` 和 `close()`，结果统一为 PyArrow Table（`core/wren/src/wren/connector/base.py:23-35`）。工厂用数据源到模块的注册表延迟导入，缺少可选依赖时返回明确的 pip extra 安装提示；MySQL/Doris 等共用实现但仍可传入具体数据源行为（`core/wren/src/wren/connector/factory.py:6-72`）。

**价值**：新数据源扩展面很窄，上层规划与 Agent 工具无须理解驱动差异；PyArrow 也为 CLI、Python、MCP 和分析框架提供共同结果格式。

### 9. MCP 把能力拆成可发现、可限权的 Agent 工具

MCP 服务分别注册查询、上下文、知识、写入、Resource 和 Prompt，而不是只暴露一个万能 `run`。查询结果默认最多 1,000 行、硬上限 10,000 行，并用 `limit + 1` 探测截断后返回 `truncated`（`core/wren/src/wren/mcp_server.py:78-111`、`:121-129`）。写入工具 `store_query` 只有在服务显式启用 `allow_write` 后才注册（`core/wren/src/wren/mcp_server.py:504-536`、`:674-687`）。

**价值**：读取、执行与知识写入是不同权限面；Agent 能发现细粒度工具，宿主也能控制结果体积和副作用。这比把数据库与文件写权限一次性授予 Agent 更稳健。

### 10. 多接入面复用同一核心，并把工作流作为版本化 Skill 分发

CLI 是 `WrenEngine`、context、memory、profile 等模块的薄封装；MCP 提供标准 Agent 协议；`wren-langchain` 与 `wren-pydantic` 将同一查询、规划和 memory 能力包装为框架工具。内置 `onboarding`、`generate-mdl`、`enrich-context`、`usage`、`genbi` Skill 则规定“先取上下文、再生成、先验证、成功后再记忆”的操作次序（`core/wren/src/wren/skills_content/*/SKILL.md`）。

**价值**：Prompt/工作流可随 CLI 版本同步，不必复制到每个应用；底层能力也没有被某个 Agent 框架绑定。SDK 还用 conformance、unit 和 integration 测试维持工具契约（`sdk/wren-langchain/tests/`；`sdk/wren-pydantic/tests/`）。

## 工程成熟度

- **分层与契约清楚**：Rust Core、PyO3、Python 编排、Connector、SDK/CLI/MCP 的责任边界明确。
- **测试不只覆盖 happy path**：除 Rust 语义/权限测试外，Python 测试覆盖多数据库 limit/分号/方言细节、strict policy 绕过、配置类型校验、memory 后端和 MCP capability gating（`core/wren/tests/unit/`、`core/wren/tests/connectors/`、`core/wren/tests/suite/`）。
- **有独立评测资产**：`evals/spodbtify_ab/run_eval.py` 与结构化输出 Schema 支持 NL-SQL A/B 结果校验；本次已确认其 `validate` 流程通过（`evals/spodbtify_ab/README.md`、`agent_output.schema.json`）。
- **演进机制较完整**：项目 Schema 有版本检查、升级计划和 apply 阶段，避免格式变化只能手工迁移（`core/wren/src/wren/context.py:497-509`、`:1275-1554`）。

## 边界与风险

1. **严格模式默认关闭**：`WrenConfig.strict_mode` 默认是 `False`（`core/wren/src/wren/config.py:32-34`）。生产接入不能仅因项目“支持治理”就假设已启用，应显式配置并用最小权限数据库账号兜底。
2. **非 source 位置的数据读取器依赖维护型 blocklist**：源码注释明确指出无法 allowlist 所有标量函数，新连接器或数据库新增 reader 时需更新 `_DATA_READER_NAMES`；`denied_functions` 只是纵深防御（`core/wren/src/wren/policy.py:31-53`）。
3. **尚非完整审计/治理平台**：README 把 audit logs、rate limits、approval workflow、data-flow inspector 列在后续计划。当前结构化错误和 planned SQL 很有价值，但不等于持久化审计、告警和全链路 trace（`README.md` “What's next”）。
4. **GenBI 验证主要是产物结构检查**：`core/wren/src/wren/genbi/verify.py` 检查入口文件、`mdl.json`、快照数据资产，并扫描 `.env` 与疑似明文凭据，但尚没有 headless 浏览器内执行 WASM smoke query 的端到端保证；“能通过 verify”不等于数据查询一定能在部署环境成功。
5. **Memory 有一致性与并发边界**：Markdown 是真相源，LanceDB 是派生索引，因此写入后重建、索引陈旧检测和多进程并发读写需要调用方设计清楚；`schema_is_current()` 仅以 Manifest hash 判断 Schema 索引是否一致（`core/wren/src/wren/memory/store.py:258-272`）。
6. **Agent SDK 当前更适合同步、单 toolkit/agent 使用**：LangChain/Pydantic 包装的底层连接与工具以同步调用为主，共享一个 toolkit 时还涉及连接器生命周期；高并发服务应按请求隔离、连接池化并补充异步/压力测试。
7. **“记住成功 SQL”不自动等于正确**：写入 memory 前仍需业务确认、dry-run 和结果校验，否则错误案例会变成后续 few-shot。仓库的“确认后存储”是工作流约定，不是自动事实验证器。

## 对自研 Data Agent 的借鉴优先级

| 优先级 | 建议 | 原因 |
| --- | --- | --- |
| P0 | 建立版本化语义层；LLM 只查询已建模对象 | 这是减少错误 Join、错字段和指标漂移的根基 |
| P0 | 分离 plan、validate、execute，错误必须有 code/phase/metadata | 让 Agent 可修复，也便于测试和观测 |
| P0 | 默认严格模式、只读账号、结果硬上限；写能力单独门控 | Agent 系统首先要控制可访问面、资源消耗和副作用 |
| P1 | 查询时提取最小语义依赖闭包，并把展开 SQL 暴露出来 | 同时改善规模、解释性与调试效率 |
| P1 | 将已确认 NL→SQL/业务规则保存在 Git 文件，向量库只做派生索引 | 可审计、可迁移、可回滚，避免“不可见学习” |
| P1 | 小 Schema 全量提供，大 Schema 才检索；准备词法降级 | 控制复杂度并保证离线/最小部署可用 |
| P2 | 用统一 Connector 契约屏蔽数据库差异，统一 Arrow 结果 | 易扩展、易测试，也方便接多种 Agent 框架 |
| P2 | 把 Agent 操作顺序写成版本化 Skill，并做框架 conformance test | 工作流与底层版本同步，降低不同宿主行为漂移 |
| P2 | 补齐持久审计、指标、trace、审批与端到端部署 smoke test | 这是从成熟开发工具走向生产 Agent 平台的关键缺口 |

## 结论

WrenAI 最突出的价值不是“能生成 SQL”，而是把数据 Agent 的可靠性问题拆成了可独立验证的工程部件：版本化语义、最小上下文、确定性 SQL 编译、执行前验证、双层权限、有限结果、可审查记忆和可替换接入面。对自研项目而言，最应先复制的是这些边界与契约，而不是 UI 或某一套 Prompt。与此同时，生产化时必须主动补齐默认关闭的 strict mode、审计与限流、索引并发一致性，以及真正的端到端运行验证。
