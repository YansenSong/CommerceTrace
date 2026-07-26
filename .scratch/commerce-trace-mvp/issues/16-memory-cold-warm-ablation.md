# 16 — 持续记忆 Cold/Warm 与消融实验

**What to build:** 让项目评审者可以定量判断业务知识、执行反馈和 Candidate 持续记忆是否真正改善系统，并确认未经验证的经验没有降低 Warm Run 正确率。

**Blocked by:** 12 — Golden 回放与记忆生命周期; 15 — MVP 核心能力评测与报告.

**Status:** ready-for-agent

- [ ] Cold Run 在清空运行时 Candidate 后执行第一组问题，并记录准确率、调用次数、延迟、成本和新登记 Candidate。
- [ ] Warm Run 使用语义相近但措辞不同的第二组问题，不通过重复完全相同问题证明记忆效果。
- [ ] 报告 Trusted 与 Candidate 的 Recall@K、Candidate 采用情况、错误传播和污染率。
- [ ] 消融实验依次比较完整 Schema 与 System Prompt、业务规则与 Trusted SQL、执行反馈与自我修正、Candidate 持续记忆。
- [ ] 各实验使用相同数据种子、模型参数、问题划分和版本元数据，使不同配置结果可比较。
- [ ] Candidate 不导致 Warm Run 正确率下降；若下降，报告明确列出受影响问题和被召回经验，而不隐藏失败。
- [ ] 实验输出合并进结构化 JSON 与 Markdown 报告，并能从干净 Candidate 状态重复运行。
- [ ] 测试证明清空 Candidate 不会删除 Trusted、业务知识或 PostgreSQL 权威记录之外的数据。
