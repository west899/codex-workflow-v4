# Changelog

## Unreleased

V4 产品线已冻结为 4.1.0。此后 `v4` 分支只接受 V4 缺陷修复；V5 不得合入本线。

## 4.1.0

这是可安装的 V4 产品。请钉住 tag `v4.1.0`（或跟踪 `v4` 分支的补丁），不要从后续 V5 开发分支安装。已有 V3 record 仍按 V3 算法收尾。以后若升级 V5，新包必须继续发现 `codex-workflow-v4` runtime，且不得改写 `v4` 历史或已发布 tag。

- 包名与 Git common-dir runtime 为 `codex-workflow-v4`；仍能发现已有的 `codex-workflow-v3` runtime。
- `install.py --uninstall`：默认打印清理清单；`--apply` 移除工作流叠加层，不删除产品源码。`--purge-state` / `--purge-runtime` 分别删除工作流档案和 Git common-dir runtime。
- `install.py --export-product <dir>`：导出不含 `.codex-workflow` 引擎与状态的产品树。
- `doctor` 报告已安装的 package 与 version。
- 从随包文件中移除已完成的阶段 0–C 规划、清单、收口和内部 ExecPlan；EQ 矩阵写入 `功能.md`。
- `requirements_impact_path` 与 Backlog WIP `limit` 拒绝 bool 冒充 integer。
- closeout dry-run 不再 `ensure_runtime()`，也不再创建 advisory lock 文件；`_role_lock_context` dry-run 与 lane 一样用空上下文。
- generation / owner_generation 等 JSON 整数统一拒绝 bool。
- `sync-status` / `apply-requirements-impact` 的 dry-run 不再 `ensure_runtime()`。
- `workflow_check` 拒绝 `generation=true` 这类 bool 冒充 integer。
- `mutate_record` 默认允许 V3 和 V4，避免新调用方漏掉 V4。
- V4 `expand-resources` 保持指纹合同 `scope.resource_keys` 冻结，只扩展 runtime `lane.resource_keys`。
- lane 只读/dry-run 不再 `ensure_runtime()` 创建 runtime 目录。
- `invalidate-integration` 接受 V4 task record（原先默认只允许 V3）。
- V4 受管状态写入后与 V3 一样刷新 `STATUS.md`。
- `claim` / `adopt` / `recover` 的 dry-run 不再写入 runtime `owner-id`。
- `remote-takeover` / `remote-handoff` 接受 V4 task record（原先写死 V3）。V4 `lane.assignment.takeover_approval` 纳入 schema。
- `remote-claim` / `remote-heartbeat` 的 dry-run 不再 `hash-object -w` / `commit-tree`，也不写入 runtime `owner-id`。
- 远端 closeout 的 `ci_checks[].source` 必填；缺省或不可信来源按 EQ-009 失败。
- `list` 在 worktree 存在但 record 不可读时标 stale；`confirm-closeout` 在 lane worktree 无法解析时 fail closed。
- 删除未使用的 `workflow_check.py stop`：Stop 只走 `codex_stop_hook.py`，check 不再接受会白跑 `check_governance` 的 `stop` 模式。`doctor` 写入 `功能.md` 命令表。
- 删除未引用的 `safe_join`、`LEGACY_PACKAGE_NAME`，以及 `pending-queued-recovery` 中不可达的 `forge-done` 别名。

## 4.0.0

Codex Workflow V4 正式产品源码包。安装：`python install.py <project-root> --project-name <name>`。

- 新任务默认 `task-record-v4`：可观察 focus slice、decision log、product checkpoint。
- 交付安全外环沿用协议代次 3：lane、canonical delivery、独立 Reviewer、strict-ff closeout。
- 已有 V3 record 按原算法收尾。
- 只读 GitHub provider receipt 可附加，不能替代 Independent Reviewer 或 ff/CI 证明。
- 已验证范围：macOS / CPython 3.9（`python3 -B verify_package.py`）。Windows / 3.12 / 3.13 未验证。

能力与测试依据见 [功能.md](功能.md)。
