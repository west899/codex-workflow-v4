# V4 Phase C 等价矩阵

> PC-003 交付。处置与 [V4_PHASEC_PLAN.md](V4_PHASEC_PLAN.md) 第 3.3 节预填行一致。不得把附加 provider receipt 写成替代证明。

## 一句话规则

附加 receipt ≠ 替代证明。receipt 只能作为额外合作式证据；缺任何现有本地远端证明时 closeout 必须失败。EQ-001 在 closeout 上由 `review.status == pass` 机械执行；GitHub required reviews 与合作式旗标都不能替代 Independent Reviewer。EQ-002 要求 receipt 绑定当前 delivery，并 dismiss stale reviews / require latest-push approval。EQ-005 拒绝 `admin_bypass_allowed`。

## Topology 字段

| 字段 | 含义 | 本 revision 成功条件 |
| --- | --- | --- |
| `pr_head_commit` | PR head / 已验证 delivery | 必须等于 `verification.delivery_commit` |
| `merge_group` | 可选；merge queue 预测提交 | 填写也不使非 ff 成功 |
| `result_commit` | 进入目标分支的提交 | 必须等于 `pr_head_commit` |
| `result_tree` | 可选；result 的 tree | 填写也不使 `head != result` 成功 |

禁止默认 `head == result`。本 revision 对 `head != result` fail closed（EQ-006）。

## 预填矩阵

| 矩阵 ID | 本地证明 | GitHub 对照 | 看起来重复？ | 本 revision 可否删除本地证明 | 本 revision 可否当成 Reviewer |
| --- | --- | --- | --- | --- | --- |
| EQ-001 | Independent Reviewer + `record-review` | required reviews / CODEOWNERS | 部分 | 否 | 否 |
| EQ-002 | 最新 delivery snapshot / exact diff | dismiss stale reviews、latest-push approval | 仅当两项都开启且可机械证明 | 否 | 否 |
| EQ-003 | Evidence Contract 验收清单 | 无等价物 | 否 | 否 | 否 |
| EQ-004 | CI `status == success` | required checks 把 `skipped`/`neutral` 当成功 | 否，语义更松 | 否 | 否 |
| EQ-005 | 无管理员 bypass | 允许管理员 bypass | GitHub 默认可能更松 | 否 | 否 |
| EQ-006 | `result_commit == pr_head_commit` 且 ff | squash / merge / merge-queue 的 result 常不等于 head | 否 | 否 | 否 |
| EQ-007 | closeout fingerprint | 无等价物 | 否 | 否 | 否 |
| EQ-008 | remote claim / release CAS | 无等价物 | 否 | 否 | 否 |
| EQ-009 | expected check source | 未钉死 source 的 required check | 否 | 否 | 否 |

Reviewer 五缺一即不得替代：独立性、最新 diff 批准、等价验收清单、所需检查、无未授权 bypass。
