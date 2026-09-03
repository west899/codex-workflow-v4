# Codex Workflow V4 协议

> 本文件由工作流包管理。项目专属永久规则写入 `../governance/AGENTS.md`，不要直接修改本文件。

## 1. 开始顺序

每次任务依次读取根 `AGENTS.md`、本协议、governance 下的 AGENTS/PROJECT/PLAN/DECISIONS、`../docs/WORKFLOW.md`、当前 Requirements Brief、Backlog、task record、相关代码和测试。运行：

```text
py -3 .codex-workflow/bin/workflow_check.py start
py -3 .codex-workflow/bin/workflow_check.py manual
```

在任意项目子目录运行时，脚本必须仍解析到当前 Git worktree、git-common-dir 和 git-dir；不得假设 `.git` 是目录。

## 2. 事实与需求门禁

- 最新用户明确指令优先，其次是 PROJECT 中有来源的确认事实。
- AI 的建议、示例和推断必须标为假设，不能写成用户已确认需求。
- 目标版本进入 Backlog 前必须完成第 2A 步需求反馈校准：复述、纠偏、正常/边界/失败/非目标样例、阻断问题关闭，以及精确 fingerprint 批准。
- Requirements 的 `brief_id + revision + target_release + fingerprint` 必须与 PROJECT、Backlog 和任务来源一致；实质修改后旧批准自动失效。
- 已批准 Brief revision/fingerprint 变化或被新 Brief 替代时，先运行 `requirements-impact` 并审查 Git 历史差异、Backlog 动作和 live local lane；再由 Coordinator 用精确 fingerprint 执行 `apply-requirements-impact`。未开始受影响项保持 blocked，已完成项保留历史，活动 task 一律停止在 `human_decision_required`，不得自动续跑。
- 只有人类对具体 `analysis_id` 写出 `decision: continue`、批准来源和理由后，活动 lane 才能在新基线 rebase 后用 `resolve-requirements-impact` 更新合同；该操作会清空旧证据，必须重新 Developer/Review/gate。停止或重写 task 不执行 resolve，按恢复/abandon 流程处理。
- incident、maintenance 和明确 user directive 可以不建立完整版本 Brief，但仍必须有当前任务的范围、非目标、验收、风险和实现授权。
- `requirements-v1`、`task-record-v3`、`task-record-v4`（均含 `lane-v1` 引用）、`developer-evidence-v1`、`review-evidence-v1` 和 `remote-claim-v1` 是强制 JSON Schema 结构门禁：Brief/record/evidence/claim 的对应读取或写入、状态写入、远端 claim 提交和远端 closeout 证明前均须通过；失败必须零写入。校验器遇到未支持的 schema 关键字同样 fail closed，不能手改 JSON 绕过。
- 新任务默认 `task-record-v4`。已有 V3 record 按 V3 收尾，不得用 V4 算法重算其 pending closeout。不得把 V3 任务口头升级为 V4。

## 3. 四类状态不能混称

| 层 | 状态与含义 |
| --- | --- |
| task | `planned → authorized → in_progress → completed`：本地任务合同与证据 |
| Backlog | `draft/blocked/ready/done/removed`：可提交的版本状态 |
| lane effective | `claimed/active/reviewing/verified/queued/integrating/stale`：运行时执行状态 |
| integration | `not_ready → pending → queued → merged_pending_closeout → integrated`，或 `invalidated` |

`completed/verified` 只说明同一 sealed snapshot 通过本地证据和 gate；`done` 必须有目标分支中的 closeout 状态；`released` 仍由独立发布门判断。本地 done 不等于远端已同步。

`state/STATUS.md` 只汇总上述受管真相，不可手改。single/Coordinator state mutation、closeout 和 Requirements impact apply 自动刷新；隔离 lane 不写共享快照，Coordinator 的 `status` 会读取 live lane。外部 Git/治理修改或 lane mutation 后必须从 Coordinator/integration worktree 运行 `workflow_check.py status`，过期时执行 `workflow_state.py sync-status --apply`。status 同时检查生成 fingerprint 和当前 Brief 是否漂移。

## 4. 角色与写入边界

| 角色 | 可以写 | 不可以写 |
| --- | --- | --- |
| Coordinator | task 合同、Requirements/Backlog/PLAN 的协调内容 | 冒充 Developer 或 Reviewer，自批风险/范围 |
| Developer | 当前 lane 的 allowed paths、测试和 task plan；通过 state 命令提交证据 | 手改 task JSON、全局 Backlog/PLAN/manifest、其他 lane |
| Reviewer | 无；只读指定 lane、commit 和 snapshot | 修代码、混合其他 lane diff、接受 Developer 自证 |
| Integrator | 目标分支、closeout、依赖解锁；一次一个 lane | 自动解冲突、改变已验证交付、并行写目标分支 |
| Human | 确认事实、范围、高风险选择、风险接受、bootstrap 和发布 | 由 AI 代替批准 |

task record 只通过 `workflow_state.py`/`workflow_lane.py` 的受限子命令写入。每次 mutation 在 common-dir 取得 OS advisory lock，持锁后重读 generation 并 CAS；generation 不符即停止。

## 5. lane 不变量

1. 一个 worktree 同时只有一个写入者；不得让多个 AI 共写一个目录。
2. 一个 task 同时只有一个有效 claim；一个 lane 绑定一个 task、branch、worktree 和 claim。
3. 领取时同时声明 allowed paths 和保守 resource keys；扩展资源先取得互斥 claim。
4. 共享 claim/registry/queue/heartbeat 位于 git-common-dir；lane pointer 位于当前 worktree git-dir；两者都不进 Git。
5. Developer/Reviewer/gate 必须绑定同一 `delivery_commit + delivery_hash + snapshot_id`。
6. lane A 的 Stop Hook 只检查 A；Coordinator worktree不冒充任何 lane gate。
7. heartbeat 只能在 token/generation 匹配时直接更新 runtime；stale 不等于可静默接管。
8. local_worktree lane 在 closeout 前必须有匹配的 queue token；入队会比较 sealed snapshot 的实际 changed paths，冲突路径或跨平台等价路径拒绝后入队者；只有队首可进入 local closeout，confirm 会删除匹配 queue token 及 task/resource claim。
9. main 前进后，未集成 lane 先由人 rebase 并保持干净，再用 `workflow_lane.py refresh-base <lane-id> --base main --apply` 更新 base；该操作清空旧 snapshot、Review、approval 和 integration 证据，随后重新验证。
10. `remote_claimed` heartbeat 只接受未过期的 active lease；stale takeover 必须有批准证据，并在同一次 atomic CAS 内更新 task ref、claim/resource refs、assignment 和 owner generation；handoff 使用同一事务，失败者冻结且保留本地提交。
11. release 默认不删除 branch/worktree。
12. Coordinator/Integrator 的本机持久租约必须以 token/generation、owner PID/session、heartbeat 和 TTL 记录；TTL 只标记 stale，不自动释放。接管必须用已知旧 token/generation、人工批准和 OS guard CAS；跨命令集成必须持有并传递同一 Integrator token。
13. canonical delivery delta 必须按 POSIX 路径排序并编码状态、旧/新路径、blob OID、mode；symlink 还必须编码旧/新目标，rename 必须以 delete+add 表示。任何内容修复（包括 merge conflict resolution）都以最终 base-to-result tree 重新摘要并重新验证。
14. Developer/Review evidence 必须使用 Evidence Contract v1。scope kind 只允许 `canonical_delivery_paths`、`declared_path_call_graph`、`explicit_command_set`、`explicit_test_set`、`explicit_runtime_surfaces` 和 `repository_tree`；target 禁止使用 `all`、`*`、`repository_wide` 或 `all_dry_runs` 聚合语义。`repository_tree` 只表示排除 `mutable_workflow_control` 后、由 sealed snapshot 绑定的 canonical product tree。
15. 每个 Developer claim 必须绑定一个有限 scope 和实际 supporting commands；Developer 不提交 fingerprint，workflow 将完整规范化 claim、完整 scope、排序后的完整 supporting commands 与 sealed snapshot 绑定为 `evidence_fingerprint`。handoff 只列 `claim_ids`、`remaining_risks`、`review_focus`。Reviewer 对每个精确 fingerprint 只能给出 `confirmed/narrowed/rejected/unverified`；只有全部 `confirmed` 才能 `pass`，其余结论必须为 `changes_requested`。
16. 已 integrated 的旧 evidence 只读保留；未完成任务的旧自由文本 evidence 不自动迁移，继续 Review、gate 或 integration 前必须重新记录 Contract v1 evidence。

本地多线必须显式把 `layout.json` 的 `parallel.mode` 设置为 `local_worktree`，并通过安装/手工 lock probe。默认 single 仍可使用相同 record/gate，但不提供并行隔离。

## 6. 单项任务闭环

```text
Requirements approved
→ Backlog durable ready
→ Coordinator 建立并授权 task record
→ claim/adopt/resume-remote 独立 lane
→ Developer 实现并形成干净 delivery commit
→ record-developer（同一 snapshot）
→ 独立 Reviewer → record-review
→ complete-task → gate
→ mark-verified
→ record-approval（仅 local bootstrap 需要）
→ prepare-integration
→ 唯一 Integrator 串行集成
→ prepare-local-closeout 或 prepare-remote-closeout
→ closeout commit 进入配置的 target ref
→ confirm-closeout/reconcile
→ 最后释放 claim，输出 next-ready 候选但不自动领取
```

所有状态修改命令默认 dry-run，只有 `--apply` 写入。Stop Hook 只给下一步，不能 push、merge 或 closeout。

## 7. 集成模式

- `local_bootstrap`：只在策略 enable、task 位于唯一有序 allowlist 的 `expires_after_task` 之前或等于它、该截止 task 尚未在 Backlog durable `done`、独立 review/gate 和精确 snapshot 人工批准全部成立时使用；prepare、integration-preflight、queue 和 local closeout 共用机械到期门禁，失败不写 task record/queue/closeout；产品集成必须 ff-only。
- `remote_pr_ci`：标准模式。产品 branch 经外部 push/PR/CI/人工合并；随后从最新远端目标基线制作只含流程状态的 closeout commit，再经外部 closeout PR/CI/人工合并。
- V3.0 远端产品和 closeout 证明只允许严格 fast-forward；普通 merge、squash/rebase 在 canonical merge-delta 证明完整前 fail closed。
- 脚本不自动执行远端 push、PR、merge、force、发布或部署。`remote_claimed` 的 atomic ref 操作只管理合作式租约，不赋予产品集成权限。
- `remote-release` 必须先证明最新远端 target 含 integrated closeout/fingerprint；apply 必须绑定 fresh dry-run 的 expected claim OID，并在一个 atomic push 中删除 task/resource claim refs。任何 OID 漂移都零删除停止。

## 8. 完成与恢复禁区

以下任一项存在，不得声称 verified/done/released：验收无证据、Reviewer 未绑定快照、P0/P1 未清、P2 未接受、Requirements 基线漂移、错误 worktree/branch、delivery 工作树不干净、集成结果不在目标 ref、closeout fingerprint 不匹配、CI 未成功或 claim token 不一致。

故障时保留 branch/worktree 和未提交内容。main 前进、rebase、冲突解决或集成修复改变内容时，旧 snapshot 失效，任务回到 Developer，重新测试、Review 和 gate。local lane 的 rebase 由人完成，随后执行 `refresh-base` 清理旧 queue 和状态证据。queue 或 `refresh-base` 已写 task record、但 runtime 写入中断时，执行 `rebuild --apply` 后人工检查，再执行 `recover <lane-id> --takeover --apply`；closeout 已在目标 ref 中且 confirm 释放中断时，执行 `reconcile <record> --target-ref <ref> --apply`。任何清理都在可恢复证据建立之后执行。

## 9. 流程改进

每个非简单任务关闭前完成复盘。Developer/Reviewer 只提出候选；Coordinator 归类为 task-only、AGENTS、Skill、脚本/Hook/CI 或 DECISIONS。永久治理变化必须由用户批准，并作为可审查变更验证；AI 不得批准自己的提案。
