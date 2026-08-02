# 直接使用查询结果判定任务完成

原实现会为每条查询额外生成 `AnalysisEvidence`，再通过 `evidence_id` 把完成条件、步骤和查询结果串联起来。这层对象与 `QueryTrace` 保存了重复数据，并增加了 API、持久化和模型结构化输出的复杂度。现在删除 `AnalysisEvidence` 和所有 `evidence_id` 引用，让完成条件核验、计划修订和最终回答直接读取已持久化的 `QueryTrace`。

- **Status**: accepted（2026-08-02 修订，删除证据 ID 机制）
- **Considered Options**:
  - `AnalysisEvidence` + `evidence_id`：能表达显式引用，但和查询工件重复，且引用存在不等于业务判断正确。
  - 对每个自然语言句子做细粒度引用：追溯最强，但生成和展示成本过高，也容易制造脆弱的文本位置关系。
  - **步骤完成条件 + `QueryTrace`（选定）**：以分析步骤为粒度定义数据需求，直接用执行后的查询结果判定完成。
- **Consequences**: 后端响应和持久化不再包含 `evidence` 或 `evidence_ids`；状态机只校验完成条件结果的数量和条件集合，语义是否满足仍由 LLM 判断；SQL、结果预览、行数和执行信息继续由 `QueryTrace` 保存和展示。
