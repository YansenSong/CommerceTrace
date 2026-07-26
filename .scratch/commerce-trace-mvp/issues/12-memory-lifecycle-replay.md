# 12 — Golden 回放与记忆生命周期

**What to build:** 让系统维护者通过可复现的离线回放决定 Candidate 是否可以成为 Trusted，并在知识或 Schema 变化后自动阻止过时经验继续影响回答。

**Blocked by:** 11 — Evidence 门控的 Candidate 自动积累.

**Status:** ready-for-agent

- [ ] 记忆状态只允许 Candidate、Trusted、Stale 和 Rejected，并校验所有允许与禁止的状态转换。
- [ ] Candidate 只有在匹配的 Golden Case 上重新通过安全校验、真实执行并满足期望结果后才能晋升 Trusted。
- [ ] Golden 结果不匹配、安全校验失败或执行明确否定经验时，Candidate 转为 Rejected 并退出检索。
- [ ] Schema 指纹、涉及字段或指标版本不再匹配时，适用的 Candidate 与 Trusted 转为 Stale 并退出检索。
- [ ] 没有对应 Golden Case 的真实用户经验不会因为重复执行成功而自动晋升。
- [ ] 状态变更以 PostgreSQL 为准，并能重建出不包含 Stale 或 Rejected 的 ChromaDB 索引。
- [ ] 回放命令生成机器可读和人类可读结果，列出通过、拒绝、失效、跳过及原因。
- [ ] 集成测试覆盖晋升、拒绝、版本失效、幂等回放和索引重建后的召回行为。
