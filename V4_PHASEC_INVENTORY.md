# V4 Phase C 远端证明盘点

> PC-002 交付。只读对照 V3 本地远端证明与 GitHub 保护分支 / merge queue。不是运行时真相源。处置以 [V4_PHASEC_PLAN.md](V4_PHASEC_PLAN.md) 第 3.3 节和 [V4_PHASEC_EQUIVALENCE.md](V4_PHASEC_EQUIVALENCE.md) 为准。

## 对照表

| 本地证明 ID | 代码位置 | 证明问题 | GitHub 对照 | 重复等级 | 缺少什么就不能删 | 矩阵 ID |
| --- | --- | --- | --- | --- | --- | --- |
| LOC-001 | `record-review` / Independent Reviewer | 这批 delivery 是否由独立 Reviewer 按验收清单确认 | required reviews / CODEOWNERS | 部分 | 独立性、最新 diff、等价验收清单、所需检查、无未授权 bypass | EQ-001 |
| LOC-002 | `verification.delivery_commit` + snapshot / evidence fingerprint | 批准是否绑定当前 exact delivery | dismiss stale reviews、latest-push approval | 部分 | 两项都开启且可机械证明最新 diff；仍缺 Evidence Contract 清单 | EQ-002 |
| LOC-003 | Evidence Contract v1 claim assessments | 每个精确 claim 是否 `confirmed` | 无等价物 | 无 | GitHub review 不重建 claim fingerprint | EQ-003 |
| LOC-004 | `prepare-remote-closeout` 要求每条 `ci_checks.status == "success"` | 所需检查是否严格成功 | required checks 把 `skipped`/`neutral` 当成功 | 表面 | V4 必须拒绝 skipped/neutral | EQ-004 |
| LOC-005 | 工作流不把管理员 bypass 当成功 | 是否存在未授权绕过 | 允许管理员 bypass | 无，GitHub 可能更松 | 禁止 bypass 的机械证明 | EQ-005 |
| LOC-006 | `merge_strategy == "ff"` 且 `result_commit == pr_head_commit`，且 result 是 target_parent 的 ff 后代 | 产品集成是否严格快进同一提交 | squash / merge / merge-queue 的 result 常不等于 head；merge queue 重跑 checks 不重跑人类 Review | 无 | 实际 topology 与 `head == result` 不能默认相等 | EQ-006 |
| LOC-007 | closeout fingerprint | 收尾状态是否与 Backlog/record 一致 | 无等价物 | 无 | GitHub merge 不计算该 fingerprint | EQ-007 |
| LOC-008 | `remote-claim` / `remote-release` CAS | 跨机器互斥与释放是否原子 | 无等价物 | 无 | branch protection 不替代 Git ref CAS | EQ-008 |
| LOC-009 | 可选 `ci_checks.source` 与 expected check source | 成功检查是否来自预期来源 | 未钉死 source 的 required check | 无 | 错误或空 source 必须失败 | EQ-009 |
| LOC-010 | `confirm-closeout` | 目标 ref 是否含精确 closeout commit | 无等价物 | 无 | 不能用 PR merged 页面替代 | EQ-007 |
| LOC-011 | `remote_pr_ci` evidence 必填字段 | target_ref / parent / head / result / strategy / pr_url / ci_checks 是否齐全 | PR metadata | 部分 | 缺字段零写入失败 | EQ-006 |

## GitHub 缺口（本 revision 不删除本地证明）

- merge queue 重跑 required status checks，不重跑人类 Review。
- `skipped` / `neutral` 可满足 GitHub required checks；V4 不是 success。
- squash / merge-commit / merge-group result 经常不是 PR head。
- 仅仅启用 branch protection 不能证明 Reviewer 独立性或 Evidence Contract 清单。
- 管理员 bypass、未钉死 check source、未开启 stale/latest-push approval 时，GitHub 比 V3 更松。

本盘点未改 `prepare-remote-closeout` 产品集成判定。
