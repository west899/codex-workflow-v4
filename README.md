# Codex Workflow V3 可移植包

Codex Workflow V3 是一个安装到现有 Git 项目的协作控制面，重点解决四件事：工作流文件与项目文件边界不清、`verified` 后没有可靠收尾、Backlog 前需求理解不足，以及同一机器/不同机器无法安全多线推进。

V3 优先支持同机多线：每个 task 独占一个 claim、branch、Git linked worktree 和写入者；多个 Developer lane 可并行，目标分支、Backlog、PLAN 和永久治理始终由唯一 Coordinator/Integrator 串行写。跨机器先使用预分配，后续可选 Git atomic refs 自主抢占。

## 主要变化

| V2 问题 | V3 行为 |
| --- | --- |
| 根目录混放 AGENTS/PROJECT/PLAN/DECISIONS/scripts/.agent | 只保留 Codex 发现入口；主体集中到 `.codex-workflow/`；manifest 区分 package/project/merge/runtime 所有权 |
| `verified` 后手工改 Backlog、删 pointer | `prepare-integration → prepare-closeout → confirm/reconcile`；exact commit + state fingerprint 后才释放 claim |
| Q-001 至 Q-007 后直接拆 Backlog | 增加第 2A 步 Requirements Brief 反馈校准和批准 fingerprint |
| 单 active pointer、共享工作树 | common-dir claim/resource/queue/heartbeat + 每 worktree 私有 lane pointer；本地多 lane 隔离 |
| 不同机器无互斥 | `remote_preassigned` 优先；可选 `remote_claimed` atomic multi-ref/CAS heartbeat，atomic 不可用时 fail closed |

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
  state/runs/                          # task record v3
  state/plans/                         # large/high-risk ExecPlan
  schemas/                             # task/requirements/lane/remote claim
  bin/                                 # paths/lock/check/state/lane/Stop Hook
  docs/WORKFLOW.md                     # 完整使用顺序
  install/manifest.json                # deterministic ownership manifest
```

linked worktree 中 `.git` 通常是文件。因此共享 runtime 总是从 `git rev-parse --git-common-dir` 定位，lane pointer 从 `git rev-parse --git-dir` 定位；不在项目根制造 `.agent`、`.codex-log` 或 backup 目录。

## 最短正确流程

1. 目标发现：只写有用户来源的 PROJECT 事实。
2. 第 2A 步：创建 Requirements Brief，复述、纠偏、场景/反例、关闭 blocking questions，批准 exact fingerprint。
3. Requirements gate 通过后批准 Backlog，并建立一个授权 task record。
4. single 或 `workflow_lane.py claim` 创建独立 lane；Developer 只写 allowed paths。
5. Developer 形成干净 delivery commit，用 `record-developer` 提交 snapshot-bound 证据。
6. 独立 Reviewer 只读同一 snapshot；Coordinator 用 `record-review` 原样入库。
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
py -3 .codex-workflow/bin/workflow_check.py preflight <record>
py -3 .codex-workflow/bin/workflow_check.py snapshot <record>
py -3 .codex-workflow/bin/workflow_check.py gate <record>

py -3 .codex-workflow/bin/workflow_lane.py claim <task> --base main [--apply]
py -3 .codex-workflow/bin/workflow_lane.py adopt <task> [--apply]
py -3 .codex-workflow/bin/workflow_lane.py list --all --json
py -3 .codex-workflow/bin/workflow_lane.py heartbeat --lane <lane-id>
py -3 .codex-workflow/bin/workflow_lane.py queue <lane-id> [--apply]
py -3 .codex-workflow/bin/workflow_lane.py recover <lane-id> --takeover [--apply]
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
```

除了 token/generation 匹配的 runtime heartbeat，状态修改默认 dry-run。Stop Hook 只检查当前 lane 并给出下一条命令，不执行集成。

## 包验证

```text
python -B verify_package.py
```

回归测试包含 Windows-safe `sys.executable`、新装/幂等/ownership 冲突、V2 active 零写入、迁移/失败回滚、Requirements 跨换行 fingerprint、错误 branch/lane、两进程 generation CAS、崩溃后 OS lock 释放、真实双 linked worktree 隔离、lane-local Stop、local bootstrap 两阶段 closeout，以及 bare remote 双 clone 原子竞争/heartbeat/atomic 不支持 fail-closed。

## Codex 发现入口依据

- [仓库 AGENTS 与 Skills](https://learn.chatgpt.com/docs/customization/overview#skills)
- [项目 Hooks](https://learn.chatgpt.com/docs/hooks#where-codex-looks-for-hooks)
- [项目 Custom Agents](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents)
- [Git worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees)

