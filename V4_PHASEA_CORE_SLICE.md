# Phase A 真实 core slice 证据

> 本文件记录包目录外一个独立项目上的 V4 全生命周期。路径已脱敏，不作为运行时真相源。

## 可观察结果

用户执行：

```text
python src/observe_core.py
```

输出：`CORE_READY`

不需要阅读 JSON、lane、generation 或 queue。

## 生命周期

| 步骤 | 结果 |
| --- | --- |
| 安装 | Codex Workflow V4，`default_new_task_version=4` |
| 观察 CLI | `CORE_READY` |
| unittest | PASS |
| record-developer | generation 1 |
| request-decision HD-001 product_checkpoint | generation 2 |
| record-decision accepted | generation 3 |
| record-review | generation 4 |
| complete-task / gate / mark-verified | verification `passed` |
| prepare-integration local_bootstrap | generation 8 |
| prepare-local-closeout | commit `388d2ca9b1a4ab27b257f4153a94967665cc1b9d` |
| confirm-closeout | `CLOSEOUT_CONFIRMED` |

## 封存身份

| 字段 | 值 |
| --- | --- |
| Worktree | `redacted://phase-a-slice-worktree` |
| Task | `MVP-001` / `core_slice` |
| Snapshot | `7176cba21bb708d2d38108021ffe7f1e5902bdf0e5207422ad9e27064f940dcd` |
| Delivery hash | `10262cf5c4e676b9b8c289682e9e581073f8f85115241b3c5f5b5e09f92965e4` |
| Closeout commit | `388d2ca9b1a4ab27b257f4153a94967665cc1b9d` |
| Closeout fingerprint version | `4` |
| Closeout fingerprint | `5c8f2d75cdc30068e79330a4a69f6b08d1deaa9b4e3b274d7efa39ed69bbe7cf` |
| Task status | `completed` |
| Verification | `passed` |
| Integration | `integrated` |
| Product direction | `accepted` |
| Architecture live | `verified` |

STATUS 人类区包含焦点、CLI 入口、观察步骤、真实/临时、产品方向 `accepted`。技术细节折叠。

## 本轮修复

`confirm-closeout` 与远端 release/transfer 读取目标 task record 时，曾写死 `task-record-v3.schema.json`，导致 V4 closeout 在 confirm 失败。已改为 `task_record_schema_name(record)`。

## 未宣称

- 未把工作流包装进本包目录自身。
- 未跑 Windows / Python 3.12 / 3.13。
- 未开始 Phase B。
