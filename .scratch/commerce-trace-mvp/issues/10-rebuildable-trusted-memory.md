# 10 — 可完全重建的 Trusted 业务记忆

**What to build:** 让系统维护者能够从版本库知识和 PostgreSQL 权威记录初始化可信业务上下文，并在 ChromaDB 丢失后完整重建两个检索索引。用户的问题在首轮和中间分析步骤中都能受控检索相关 Trusted 经验。

**Blocked by:** 05 — 首轮确定性 Schema 与业务上下文.

**Status:** ready-for-agent

- [ ] 业务规则、指标和 Golden SQL 使用可审查的版本化知识格式，并在载入时验证引用的表、字段和版本。
- [ ] PostgreSQL 保存运行时记忆登记；ChromaDB 仅保存可从权威来源重建的召回索引。
- [ ] 使用唯一中文 Embedding 模型 `BAAI/bge-small-zh-v1.5`，业务知识与工具经验进入独立集合。
- [ ] 冷启动流程载入业务知识与预置 Golden SQL，登记为 Trusted 并构建两个索引。
- [ ] 删除 ChromaDB 后执行重建，检索结果的记录身份、状态和来源与删除前保持一致。
- [ ] Context Assembler 在首轮加入相关 Trusted 业务规则、指标和 SQL 经验；Agent 还能按“原始问题＋当前步骤”调用 `search_memory`。
- [ ] Schema 不进入向量索引，历史 SQL 结果不进入索引，也不直接复用。
- [ ] 检索或索引暂时失败时，系统保留完整 Schema 并以可观察的降级状态继续或安全结束。
- [ ] 集成测试使用可控 Embedding 替身验证排序和重建，真实 Embedding 测试单独标记。
