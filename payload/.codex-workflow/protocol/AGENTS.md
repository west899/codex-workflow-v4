# Codex Workflow V3 协议

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
- incident、maintenance 和明确 user directive 可以不建立完整版本 Brief，但仍必须有当前任务的范围、非目标、验收、风险和实现授权。

## 3. 四类状态不能混称

| 层 | 状态与含义 |
| --- | --- |
| task | `planned → authorized → in_progress → completed`：本地任务合同与证据 |
| Backlog | `draft/blocked/ready/done/removed`：可提交的版本状态 |
| lane effective | `claimed/active/reviewing/verified/queued/integrating/stale`：运行时执行状态 |
| integration | `not_ready → pending → queued → merged_pending_closeout → integrated`，或 `invalidated` |

`completed/verified` 只说明同一 sealed snapshot 通过本地证据和 gate；`done` 必须有目标分支中的 closeout 状态；`released` 仍由独立发布门判断。本地 done 不等于远端已同步。

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
8. closeout confirm 前不得释放 task/resource claim；release 默认不删除 branch/worktree。

本地多线必须显式把 `layout.json` 的 `parallel.mode` 设置为 `local_worktree`，并通过安装/手工 lock probe。默认 single 仍可使用相同 record/gate，但不提供并行隔离。

## 6. 单项任务闭环

```text
Requirements approved
→ Backlog durable ready
→ Coordinator 建立并授权 task record
→ claim/adopt 独立 lane
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

- `local_bootstrap`：只在策略 enable、task allowlist、未过期、独立 review/gate 和精确 snapshot 人工批准全部成立时使用；产品集成必须 ff-only。
- `remote_pr_ci`：标准模式。产品 branch 经外部 push/PR/CI/人工合并；随后从最新远端目标基线制作只含流程状态的 closeout commit，再经外部 closeout PR/CI/人工合并。
- V3.0 远端产品和 closeout 证明只允许严格 fast-forward；普通 merge、squash/rebase 在 canonical merge-delta 证明完整前 fail closed。
- 脚本不自动执行远端 push、PR、merge、force、发布或部署。`remote_claimed` 的 atomic ref 操作只管理合作式租约，不赋予产品集成权限。

## 8. 完成与恢复禁区

以下任一项存在，不得声称 verified/done/released：验收无证据、Reviewer 未绑定快照、P0/P1 未清、P2 未接受、Requirements 基线漂移、错误 worktree/branch、delivery 工作树不干净、集成结果不在目标 ref、closeout fingerprint 不匹配、CI 未成功或 claim token 不一致。

故障时保留 branch/worktree 和未提交内容。main 前进、rebase、冲突解决或集成修复改变内容时，旧 snapshot 失效，任务回到 Developer，重新测试、Review 和 gate。任何清理都在可恢复证据建立之后执行。

## 9. 流程改进

每个非简单任务关闭前完成复盘。Developer/Reviewer 只提出候选；Coordinator 归类为 task-only、AGENTS、Skill、脚本/Hook/CI 或 DECISIONS。永久治理变化必须由用户批准，并作为可审查变更验证；AI 不得批准自己的提案。

