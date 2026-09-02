# V4 Phase C 收口证据

> 本文件把 `V4_PHASEC_PLAN.md` 第 10 节完成定义映射到已实现门禁和回归。它不是运行时真相源。规划真相源仍是 [V4_PHASEC_PLAN.md](V4_PHASEC_PLAN.md)。

## 1. 落账结论

| 字段 | 值 |
| --- | --- |
| Plan ID | `V4-PHASEC` |
| Revision | `2` |
| Normative fingerprint | `d0db2b72ae6abba472b794befdc7d5b2a8cdb0e3ac5e2255dd1c784579a1b35c` |
| 计划状态 | `phase_c_passed` |
| 实施授权 | `phase_c_authorized_complete` |
| 产品 Review | Independent Review 先 `changes_requested` 后 `pass`（2026-09-02）；本包目录未自包装 V4 task record closeout |
| 验证 | `tests/test_v4_phase_c.py` 与 V3/V4 回归；macOS / CPython 3.9 包门禁 |

## 2. 完成定义核验

| # | 完成定义 | 结论 |
| --- | --- | --- |
| 1 | 远端证明对照表 | `V4_PHASEC_INVENTORY.md` 覆盖 LOC-001–011 / EQ-001–009 |
| 2 | 等价矩阵 | `V4_PHASEC_EQUIVALENCE.md` 与第 3.3 节预填行一致；Reviewer 不被 GitHub 自动替代 |
| 3 | keep-strict-ff + 只读 receipt | `V4_PHASEC_KEEP_STRICT_FF.md`；`provider-receipt-v1` 只读；receipt 不能补齐缺失 ff/CI |
| 4 | 默认 strict-ff | `head != result` 与非 ff 失败（EQ-006） |
| 5 | skipped/neutral/bypass/source/stale | EQ-002/004/005/009 负例；receipt 须 dismiss stale reviews 且 latest-push |
| 6 | 未实施发布 | integration/closeout 不写成 released |
| 7 | 人类主路径 | README/功能.md/WORKFLOW 说明默认仍是 strict-ff，receipt 只是附加证据 |
| 8 | V3/V4 回归 | `verify_package.py` 包门禁；Windows / 3.12 / 3.13 未宣称 |
| 9 | Evidence | `tests/test_v4_phase_c.py` 驱动 shipped `provider-receipt` 与 `prepare-remote-closeout`；closeout 要求 `review.status == pass` |

## 3. 已知限制

- 本包自身未安装为 V4 工作流项目，因此没有 package-local task record closeout。
- 不接受 squash / merge-commit / merge-queue 作为产品集成。
- 未删除任何现有本地远端证明。
- `validate_remote_closeout_evidence` 不绑定 `snapshot_id`；写入路径 `prepare-remote-closeout` 会绑定。
- 可选 `result_tree` 不与 `result_commit` 交叉校验。
- Windows / Python 3.12 / 3.13 仍未宣称。
