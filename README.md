# Codex Workflow V4 可移植包

Codex Workflow V4 在 V3 交付安全外环上增加产品反馈内环：新任务默认 `task-record-v4`，先做出可观察的 focus core slice，人类通过 STATUS / 决策卡片确认方向后再进入独立 Review 和集成。已有 V3 任务继续按 V3 closeout，升级不猜 focus、不自动批准架构基线。

V3 外环仍然解决：工作流文件与项目文件边界、`verified` 后可靠收尾、Backlog 前需求校准，以及同一机器/不同机器的安全多线推进。

V3 优先支持同机多线：每个 task 独占一个 claim、branch、Git linked worktree 和写入者；多个 Developer lane 可并行，目标分支、Backlog、PLAN 和永久治理始终由唯一 Coordinator/Integrator 串行写。跨机器先使用预分配，后续可选 Git atomic refs 自主抢占。

当前已实现能力的完整清单见 [功能.md](功能.md)。

## 主要变化

| V2 问题 | V3 行为 |
| --- | --- |
| 根目录混放 AGENTS/PROJECT/PLAN/DECISIONS/scripts/.agent | 只保留 Codex 发现入口；主体集中到 `.codex-workflow/`；manifest 区分 package/project/merge/runtime 所有权 |
| `verified` 后手工改 Backlog、删 pointer | `prepare-integration → prepare-closeout → confirm/reconcile`；exact commit + state fingerprint 后才释放 claim |
| Q-001 至 Q-007 后直接拆 Backlog | 增加第 2A 步 Requirements Brief 反馈校准和批准 fingerprint |
| 单 active pointer、共享工作树 | common-dir claim/resource/queue/heartbeat、Coordinator/Integrator 持久租约 + 每 worktree 私有 lane pointer；本地多 lane 隔离 |
| 不同机器无互斥 | `remote_preassigned` 优先；可选 `remote_claimed` atomic multi-ref、heartbeat、stale takeover 和 handoff，atomic 不可用时 fail closed |

四类状态不能混称：task 的本地合同状态、Backlog durable 状态、lane effective 状态和 integration 状态分别管理。`completed/verified` 不等于 `done`，本地 done 不等于远端已同步，done 不等于 released。

## 安装

需要 Git 和 Python 3.9+。在 Windows 上推荐 `py -3` 或 `python`；脚本内部始终使用当前 `sys.executable`，不依赖裸 `python3` 名称。

```text
python install.py <project-root> --project-name <name>
```

默认 `parallel.mode=single`。新项目要直接启用同机多线：

```text
python install.py <project-root> --project-name <name> --parallel-mode local_worktree
```

安装器不会创建产品基线 commit、push、PR、merge、发布或部署。安装后在 Codex 中审查并信任更新后的项目 Hook，再开一个新会话确认 SessionStart runtime heartbeat。

### V2 升级

先做零写入计划：

```text
python install.py <project-root> --plan-upgrade
```

- 存在 `.agent/active-task` 时严格零写入拒绝；先在 V2 收口或显式保留证据后放弃。
- 没有 V2 manifest 时必须显式 `--adopt-v2`，不能凭同名文件猜所有权。
- 修改过的 V2 根 AGENTS 需要人工三方比较；将确认的项目专属规则放入文件，并用 `--agents-merge-file <path>` 迁移到 project-owned governance AGENTS。
- package 冲突默认停止。`--force-package` 只可备份并替换 package 文件，绝不会覆盖 PROJECT/PLAN/DECISIONS/Requirements/Backlog/runs/plans 等项目状态。
- staged V3 自检、legacy cleanup 和 manifest 发布属于一个带 common-dir backup/journal 的可回滚事务；最终 manifest 不写绝对路径、主机、用户名或安装时间。

## 安装后的布局

```text
AGENTS.md                              # 薄发现入口，marker 合并
.agents/skills/                        # Codex repository Skills
.codex/hooks.json                      # 项目 Hooks，结构化合并
.codex/agents/                         # Developer / Reviewer 配置
.codex-workflow/
  layout.json                          # 路径、并行和集成策略
  protocol/AGENTS.md                   # package 通用协议
  governance/                          # project 事实、计划、决策、Requirements
  state/MVP_BACKLOG.md                 # durable 状态
  state/STATUS.md                      # 由脚本生成的当前状态快照
  state/requirements-impacts/          # 已应用的 Requirements 变更影响报告
  state/runs/                          # task record v3 / v4
  state/plans/                         # large/high-risk ExecPlan
  schemas/                             # task/requirements/lane/remote claim/developer-review evidence
  bin/                                 # paths/lock/check/state/lane/Stop Hook
  docs/WORKFLOW.md                     # 完整使用顺序
  install/manifest.json                # deterministic ownership manifest
```

linked worktree 中 `.git` 通常是文件。因此共享 runtime 总是从 `git rev-parse --git-common-dir` 定位，lane pointer 从 `git rev-parse --git-dir` 定位；不在项目根制造 `.agent`、`.codex-log` 或 backup 目录。

## JSON Schema 结构门禁

`requirements-v1`、`task-record-v3`、`task-record-v4`（均含其 `lane-v1` 引用）、`remote-claim-v1`、`developer-evidence-v1` 和 `review-evidence-v1` 是强制结构门禁，不是只供阅读的示例。Requirements snapshot/gate 读取 Brief 时、对应版本 task record 读取时、state/lane 写入更新前、Developer/Review evidence 入库前，以及远端 claim 的生成、提交、读取和远端 closeout 证明时都会 fail-closed 校验。字段缺失、类型/枚举/长度/模式不符或引用的 lane 无效时，命令不写 task record、queue、closeout 或远端 claim/ref。

校验器只实现包内这些 schema 已使用的受限 JSON Schema 子集，随包以 Python 标准库运行；若后续 schema 引入未实现的关键字，也会停止而不是静默忽略。历史 V2 record 仅保留读取兼容，不能借此绕过 V3/V4 写入门禁。

## Evidence Contract v1

Developer evidence 必须把命令、有限 scope 和 claim 分开记录。scope kind 只允许：

- `canonical_delivery_paths`
- `declared_path_call_graph`
- `explicit_command_set`
- `explicit_test_set`
- `explicit_runtime_surfaces`
- `repository_tree`

每个 claim 都要引用一个 scope 和实际 supporting commands；`record-developer` 在 sealed snapshot 上生成 canonical `evidence_fingerprint`。scope target 禁止使用 `all`、`*`、`repository_wide` 或 `all_dry_runs`；完整 canonical product tree 范围只能使用 `repository_tree` target `.`，并显式排除 `mutable_workflow_control`，其精确产品 tree 身份由 snapshot 绑定。Developer `handoff` 只包含 `claim_ids`、`remaining_risks` 和 `review_focus`。

Reviewer 必须对每个 `claim_id + evidence_fingerprint` 给出 `confirmed`、`narrowed`、`rejected` 或 `unverified`。只有全部 claim 都为 `confirmed` 时 Review 才能 `pass`；任何其他 assessment 都要求 `changes_requested`。已 integrated 的旧记录保持只读兼容，未完成任务的旧 evidence 必须重录 Contract v1 后才能继续 Review、gate 或 integration。完整字段、fingerprint 材料和 Developer/Review JSON 示例见安装后的 `.codex-workflow/docs/WORKFLOW.md`。

## 最短正确流程

1. 目标发现：只写有用户来源的 PROJECT 事实。
2. 第 2A 步：创建 Requirements Brief，复述、纠偏、场景/反例、关闭 blocking questions，批准 exact fingerprint。
3. Requirements gate 通过后批准 Backlog，生成状态快照，并建立一个授权 task record。
4. single 或 `workflow_lane.py claim` 创建独立 lane；Developer 只写 allowed paths。
5. Developer 形成干净 delivery commit，用 `record-developer` 提交有限 scope、command-bound claims 和 supporting commands；workflow 生成并持久化 Contract v1 fingerprint。
6. 独立 Reviewer 只读同一 snapshot，逐 claim fingerprint 给出 assessment；Coordinator 用 `record-review` 原样入库。
7. `complete-task → gate → mark-verified`；此时只能报告 verified。
8. 选择并验证 `local_bootstrap` 或 `remote_pr_ci`，多个 verified lane 串行入队集成。
9. prepare closeout 生成 done/集成证据/依赖解锁 commit；该 commit 进入 target ref 后 confirm/reconcile。
10. 最后释放 claim；branch/worktree 默认保留，下一 ready 只提示、不自动领取。

完整命令、evidence JSON 字段、恢复边界和状态报告语言见安装后的 `.codex-workflow/docs/WORKFLOW.md`。

## 常用命令

```text
py -3 .codex-workflow/bin/workflow_check.py manual
py -3 .codex-workflow/bin/workflow_check.py requirements-snapshot <brief>
py -3 .codex-workflow/bin/workflow_check.py requirements-gate <brief>
py -3 .codex-workflow/bin/workflow_check.py requirements-impact <revised-brief> --json
py -3 .codex-workflow/bin/workflow_check.py status [--json]
py -3 .codex-workflow/bin/workflow_check.py preflight <record>
py -3 .codex-workflow/bin/workflow_check.py snapshot <record>
py -3 .codex-workflow/bin/workflow_check.py gate <record>

py -3 .codex-workflow/bin/workflow_lane.py claim <task> --base main [--apply]
py -3 .codex-workflow/bin/workflow_lane.py adopt <task> [--apply]
py -3 .codex-workflow/bin/workflow_lane.py resume-remote <record> --owner-id <assigned-uuid> [--apply]
py -3 .codex-workflow/bin/workflow_lane.py remote-claim <record> [--lease-seconds <seconds>] [--apply]
py -3 .codex-workflow/bin/workflow_lane.py remote-heartbeat <record> [--lease-seconds <seconds>] [--apply]
py -3 .codex-workflow/bin/workflow_lane.py remote-takeover <record> --owner-id <new-uuid> --approved-by <person> --approval-ref <evidence> [--apply]
py -3 .codex-workflow/bin/workflow_lane.py remote-handoff <record> --owner-id <current-uuid> --to-owner-id <new-uuid> [--apply]
py -3 .codex-workflow/bin/workflow_lane.py remote-release <record>
py -3 .codex-workflow/bin/workflow_lane.py remote-release <record> --expected-claim-oid <dry-run-oid> --apply
py -3 .codex-workflow/bin/workflow_lane.py list --all --json
py -3 .codex-workflow/bin/workflow_lane.py heartbeat --lane <lane-id>
py -3 .codex-workflow/bin/workflow_lane.py lock-status coordinator|integrator
py -3 .codex-workflow/bin/workflow_lane.py lock-acquire integrator --apply
py -3 .codex-workflow/bin/workflow_lane.py lock-heartbeat integrator --token <token> --generation <generation>
py -3 .codex-workflow/bin/workflow_lane.py lock-takeover integrator --expected-token <old-token> --expected-generation <old-generation> --approved-by <human> --approval-ref <evidence> --apply
py -3 .codex-workflow/bin/workflow_lane.py lock-release integrator --token <token> --generation <generation> --apply
py -3 .codex-workflow/bin/workflow_lane.py queue <lane-id> [--apply]
py -3 .codex-workflow/bin/workflow_lane.py refresh-base <lane-id> --base main [--apply]
py -3 .codex-workflow/bin/workflow_lane.py recover <lane-id> --takeover [--apply]
py -3 .codex-workflow/bin/workflow_lane.py rebuild [--apply]
py -3 .codex-workflow/bin/workflow_lane.py release <lane-id> [--apply]

py -3 .codex-workflow/bin/workflow_state.py record-developer <record> --evidence-json <file> --delivery-commit HEAD [--apply]
py -3 .codex-workflow/bin/workflow_state.py record-review <record> --review-json <file> [--apply]
py -3 .codex-workflow/bin/workflow_state.py complete-task <record> --acceptance-json <file> [--apply]
py -3 .codex-workflow/bin/workflow_state.py mark-verified <record> [--apply]
py -3 .codex-workflow/bin/workflow_state.py record-approval <record> --approval-json <file> [--apply]
py -3 .codex-workflow/bin/workflow_state.py prepare-integration <record> --mode <mode> [--apply]
py -3 .codex-workflow/bin/workflow_state.py prepare-local-closeout <record> --target-ref <ref> --result-commit <sha> [--apply]
py -3 .codex-workflow/bin/workflow_state.py prepare-remote-closeout <record> --evidence-json <file> [--apply]
py -3 .codex-workflow/bin/workflow_state.py confirm-closeout <record> --target-ref <ref> --closeout-commit <sha> [--apply]
py -3 .codex-workflow/bin/workflow_state.py reconcile <record> --target-ref <ref> [--apply]
py -3 .codex-workflow/bin/workflow_state.py apply-requirements-impact <revised-brief> --expected-fingerprint <sha256> [--apply]
py -3 .codex-workflow/bin/workflow_state.py resolve-requirements-impact <record> --analysis-id <sha256> --decision-json <file> [--apply]
py -3 .codex-workflow/bin/workflow_state.py sync-status [--apply]
```

除了 token/generation 匹配的 runtime heartbeat，状态修改默认 dry-run。`STATUS.md` 汇总受管任务、Backlog、Requirements 和影响报告；single/Coordinator state mutation、closeout 与已应用的 Requirements 变更会自动刷新它。隔离 lane 不写该共享文件，以免 rebase 冲突；Coordinator 运行 `workflow_check.py status` 读取 live lane，若提示过期再从 Coordinator/integration worktree 执行 `sync-status --apply`。Stop Hook 只检查当前 lane并给出下一条命令，不执行集成。

## 包验证

```text
python -B verify_package.py
```

回归测试包含 Windows-safe `sys.executable`、新装/幂等/ownership 冲突、V2 active 零写入、迁移/失败回滚、Requirements 跨换行 fingerprint、revision/替代 Brief 的历史影响分析、活动本地 lane 的人类继续决定和状态快照过期检测、错误 branch/lane、两进程 generation CAS、崩溃后 OS lock 释放、Coordinator/Integrator 持久租约的 stale 零写入与 CAS takeover、canonical delivery 的文本/二进制/mode/Git-index symlink/rename-as-delete+add/merge conflict fixture、真实双 linked worktree 隔离、lane-local Stop、两条 local lane 的排队/串行 closeout/rebase 后重新验证、local bootstrap 两阶段 closeout及到期零写入、remote_preassigned 的双 clone PR/closeout 恢复，以及 bare remote 原子竞争/heartbeat/atomic 不支持 fail-closed。

## Codex 发现入口依据

- [仓库 AGENTS 与 Skills](https://learn.chatgpt.com/docs/customization/overview#skills)
- [项目 Hooks](https://learn.chatgpt.com/docs/hooks#where-codex-looks-for-hooks)
- [项目 Custom Agents](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents)
- [Git worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees)
