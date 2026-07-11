# Codex Workflow V3 使用说明

## 1. 这套工作流解决什么

V3 把工作流主体从项目根集中到 `.codex-workflow/`，补齐需求校准、`verified → done` 收尾、同机多 worktree lane，以及可选的跨机器预分配/原子 claim。它仍是合作式控制面，不替代 Git 分支保护、受保护 CI 和人工发布审批。

## 2. 第一次使用

1. 阅读根 AGENTS 指向的协议与 governance 文件。
2. 回答 PROJECT 的目标发现问题。
3. 从 Requirements Brief 模板创建 `governance/requirements/<brief-id>.md`。
4. 让 AI 复述“已确认 / 假设 / 未确认”，请用户纠偏，补齐正常、边界/失败和非目标场景。
5. 运行 `requirements-snapshot`，让用户批准精确 brief/revision/release/fingerprint；把批准信息写回 Brief。
6. 运行 `requirements-gate`，再把同一基线写入 PROJECT 与 Backlog。
7. 将 Backlog 从 draft 审批为 ready 项；任务才可进入实现。

```text
py -3 .codex-workflow/bin/workflow_check.py requirements-snapshot .codex-workflow/governance/requirements/REQ-001.md
py -3 .codex-workflow/bin/workflow_check.py requirements-gate .codex-workflow/governance/requirements/REQ-001.md
```

## 3. 建立 task contract

从仓库 Skill 的 task-record template 创建 `.codex-workflow/state/runs/<task-id>.json`。Coordinator 填写来源、需求基线、请求、范围、非目标、allowed paths、resource keys、验收、风险、授权和 exact base commit。task record 的后续写入只经 state/lane 命令；不要手改 JSON 来跳过 generation 或角色边界。

```text
py -3 .codex-workflow/bin/workflow_check.py preflight .codex-workflow/state/runs/MVP-001.json
```

## 4. 单线与本地多线

默认 `parallel.mode=single`。需要同机并行时，由用户批准把它改为 `local_worktree`，设置合理的 `max_local_lanes`，运行 manual 的 advisory-lock probe，然后每个任务单独 claim：

```text
py -3 .codex-workflow/bin/workflow_lane.py claim MVP-001 --base main
py -3 .codex-workflow/bin/workflow_lane.py claim MVP-001 --base main --apply
py -3 .codex-workflow/bin/workflow_lane.py list --all --json
```

dry-run 会输出 lane、branch、worktree、claim 和资源范围。`--apply` 才创建 linked worktree。每条线必须使用不同 task/branch/worktree，冲突的 task/resource/branch/worktree 会被拒绝。已有分支可用 `adopt`；脏 worktree 必须显式确认 diff token。

长任务边界续 heartbeat：

```text
py -3 .codex-workflow/bin/workflow_lane.py heartbeat --lane <lane-id>
```

heartbeat 是唯一默认直接写 runtime 的 lane 命令；它不改 Git 跟踪内容，且 token/generation 不匹配即拒绝。

## 5. Developer、Reviewer 与 sealed snapshot

Developer 只修改 lane 的 allowed paths，形成干净、可恢复的 delivery commit，再提交 evidence JSON：

```text
py -3 .codex-workflow/bin/workflow_state.py record-developer <record> --delivery-commit HEAD --evidence-json developer.json
py -3 .codex-workflow/bin/workflow_state.py record-developer <record> --delivery-commit HEAD --evidence-json developer.json --apply
```

Reviewer 只读 exact commit/snapshot。Coordinator 原样记录 review，不得让 Developer 自审：

```text
py -3 .codex-workflow/bin/workflow_state.py record-review <record> --review-json review.json --apply
py -3 .codex-workflow/bin/workflow_state.py complete-task <record> --acceptance-json acceptance.json --apply
py -3 .codex-workflow/bin/workflow_check.py gate <record>
py -3 .codex-workflow/bin/workflow_state.py mark-verified <record> --apply
```

delivery hash 绑定 base；patch hash 对 canonical entries 去掉 base identity；snapshot ID 再绑定 task/lane/branch。二进制、mode 和关闭 rename 猜测后的 delete/add 都进入 canonical delta。可变 task/PLAN/Backlog 状态不进入产品 delivery hash，但由独立门禁验证。

## 6. local bootstrap 收尾

仅当 layout 策略启用、task 在 allowlist、Review/gate 通过且用户批准精确 task/target/snapshot/delivery hash 时使用。先记录 approval，再准备集成：

```text
py -3 .codex-workflow/bin/workflow_state.py record-approval <record> --approval-json approval.json --apply
py -3 .codex-workflow/bin/workflow_state.py prepare-integration <record> --mode local_bootstrap --apply
```

`local_worktree` lane 必须先入集成队列。优先级数字越小越靠前；优先级相同时按入队时间和 queue ID 排序。只有队首可以 prepare local closeout：

```text
py -3 .codex-workflow/bin/workflow_lane.py queue <lane-id> --priority 100 --apply
```

入队会将 sealed snapshot 的 `delivery_commit`、hash 和实际 `changed_paths` 写入 queue entry，并逐一验证已排队 lane 的 registry、token、generation 和 task record。后入队 lane 的实际路径与任一已排队 lane 重叠时会被拒绝；路径比较使用跨平台规范形式，大小写或 Unicode 等价路径也会停止入队。queue 会写入 task record，所以 Integrator 在 ff-only 前先把该 record 的 prepared/queued 状态提交到 lane branch。将 task branch 以 ff-only 集成到配置的本地目标分支。这是 Integrator 的显式 Git 操作，脚本不会替用户自动 merge。目标 worktree 必须干净，且 exact verified commit 已在 target ref 中。然后：

```text
py -3 .codex-workflow/bin/workflow_state.py prepare-local-closeout <record> --target-ref refs/heads/main --result-commit <exact-main-sha> --apply
py -3 .codex-workflow/bin/workflow_state.py confirm-closeout <record> --target-ref refs/heads/main --closeout-commit <printed-sha>
py -3 .codex-workflow/bin/workflow_state.py confirm-closeout <record> --target-ref refs/heads/main --closeout-commit <printed-sha> --apply
```

prepare 生成只含 task/Backlog/依赖解锁状态的 closeout commit 和 fingerprint。由于 Git commit SHA 不能写入计算它自身的同一份记录，Git 跟踪的 `integration.closeout_commit` 保持 `null`；prepare 输出的 SHA 由 confirm 参数和本机 audit 记录保存。confirm 再证明 target ref 包含该 exact commit 且该 ref 当前状态 fingerprint 未漂移，最后才释放匹配 token/generation 的 runtime claim。若 confirm 在 runtime 释放中断，确认 target ref 仍包含 closeout 后运行 `reconcile`；它会校验剩余 token/generation 并幂等完成释放。

## 7. remote PR/CI 收尾

```text
py -3 .codex-workflow/bin/workflow_state.py prepare-integration <record> --mode remote_pr_ci --apply
```

外部执行 push、产品 PR、CI 和人工合并；本工作流不会自动完成这些操作。V3.0 只接受严格 ff 证明。fetch 后提供 evidence JSON（target_ref、target_parent、pr_head_commit、result_commit、merge_strategy=`ff`、PR URL、全部 success 的 CI checks），从最新目标基线创建 closeout commit：

```text
py -3 .codex-workflow/bin/workflow_state.py prepare-remote-closeout <record> --evidence-json remote.json --apply
```

closeout commit 再走外部 closeout-only PR/CI/人工合并；fetch 后 confirm/reconcile。缺字段、CI 非 success、result 不在 target、PR head 不是 verified commit 或非 ff 全部 fail closed。

## 8. 集成队列、恢复与释放

多个 lane 可以同时 verified，但目标分支一次只集成一个。入队：

```text
py -3 .codex-workflow/bin/workflow_lane.py queue <lane-id> --apply
```

每个 queued lane 都有精确 queue token；confirm closeout 会校验 token/generation 后删除它。main 前进、路径/资源重叠、merge conflict 或任何内容修复都不自动解决。对于仍未集成的 local lane，先由人完成 rebase，确认工作树干净后再刷新其受控状态：

```text
git -C <lane-worktree> rebase main
py -3 .codex-workflow/bin/workflow_lane.py refresh-base <lane-id> --base main --apply
```

`refresh-base` 不执行 rebase 或解决冲突。它只接受已前进、已包含新 base 的 lane，移除旧 queue entry，更新 base commit，并清空旧 Developer、Review、verification、approval、integration 和 acceptance 证据。随后创建新的干净 delivery commit，重新执行 `record-developer → record-review → complete-task → gate → mark-verified → approval → prepare-integration → queue`。

stale 只表示 heartbeat 过期。显式 takeover 才递增 owner generation；默认保留 branch/worktree。release 只移除 runtime claim，不删 branch/worktree：

```text
py -3 .codex-workflow/bin/workflow_lane.py recover <lane-id> --takeover
py -3 .codex-workflow/bin/workflow_lane.py release <lane-id>
```

registry 损坏时，根据 `git worktree list --porcelain`、lane pointer、task record 和 branch 重建最小状态；不可恢复的旧 heartbeat/audit/session 不得伪造，owner 标 stale 后人工接管。

```text
py -3 .codex-workflow/bin/workflow_lane.py rebuild
py -3 .codex-workflow/bin/workflow_lane.py rebuild --apply
```

`rebuild` 先只报告将恢复的 lane 和 queue；`--apply` 把旧 registry、claim、resource、queue、heartbeat 和 audit 移入带 journal 的 backup，再重建最小 runtime。重建后的 lane 一律为 `stale`，必须用 `recover --takeover` 恢复 owner generation；heartbeat 不能绕过这一要求。

队列写入和 `refresh-base` 都先更新 Git 跟踪的 task record，再更新本机 runtime。若进程恰好在这两个阶段之间中断，先运行 `rebuild --apply`，确认报告的 lane/queue，再运行 `recover <lane-id> --takeover --apply`。rebuild 会依据当前 record 重建 queue：已 queued 的 record 重新获得相同 queue ID；已被 `refresh-base` 清空 integration 的 record 不会保留旧 queue。closeout 已进入目标分支而 confirm 中断时不运行 rebuild，使用 `reconcile <record> --target-ref <ref> --apply` 继续释放剩余 runtime claim。三个恢复路径都保留 branch 和 worktree。

## 9. 跨机器

优先使用 `remote_preassigned`：唯一 Coordinator 为不同任务写入随机 owner UUID、claim ID、branch 和 record，显式 commit/push。被分配机器 fetch 后先 checkout 指定 branch，再执行：

```text
py -3 .codex-workflow/bin/workflow_lane.py resume-remote <record> --owner-id <assigned-uuid> --apply
```

该命令只在 branch、owner UUID、claim 和 generation 一致时写入本机受控运行态和当前 worktree 私有 lane pointer；不会修改 Git 跟踪状态、推送、合并或覆盖已有 pointer。相同 pointer 可幂等恢复。目标分支仍由单一 Integrator 串行 closeout。

可选 `remote_claimed` 使用普通 Git refs。`remote-claim` 在一次 `push --atomic` 中 create-only 写入 task branch、task claim 和全部 resource claim ref；成功后写当前 worktree 的 lane pointer。`remote-heartbeat` 只推进 claim/resource refs 的 lease revision，并在 lease 过期时拒绝续租，旧 owner 必须冻结。

stale takeover 需要新的随机 owner UUID、已过期 lease 和显式人工批准证据。新 owner 先 fetch 并 checkout 精确 task branch，再执行：

```text
py -3 .codex-workflow/bin/workflow_lane.py remote-takeover <record> --owner-id <new-uuid> --approved-by <person> --approval-ref <evidence> --apply
```

该命令在一次 atomic CAS 中同时推进 task ref、task claim 和全部 resource ref，递增 `owner_generation` 与 `lease_revision`，并把批准信息写入 task branch 和 claim payload。旧 owner 的本地未推送提交、branch 和 worktree 保留；其后续 heartbeat 会因 token/generation 不匹配而停止。

有效 owner 可主动交接，不需要等待 lease 过期：

```text
py -3 .codex-workflow/bin/workflow_lane.py remote-handoff <record> --owner-id <current-uuid> --to-owner-id <new-uuid> --apply
```

handoff 使用同一组 atomic CAS 更新 task branch 与所有 claim refs，并撤销交出者的本地 pointer。接收者 fetch/checkout 更新后的 task branch，再运行 `resume-remote <record> --owner-id <new-uuid> --apply` 写入自己的 pointer；只有接收者能继续 heartbeat。任一 CAS 失败的一方冻结并保留本地工作。不支持 atomic multi-ref 时自动 claim 模式 fail closed，退回人工预分配。远端 claim 只提供合作式互斥，不替代权限和审批。

## 10. 状态报告语言

- `in_progress`：正在当前 lane 实现。
- `verified`：本地 sealed snapshot 已通过 Developer/Reviewer/gate，尚未集成。
- `done`：配置的目标 ref 含 closeout 状态并已 confirm；说明本地或远端范围。
- `released`：另有发布证据；不能由 done 推断。
- `remote_sync=pending`：可继续当前 lane 本地工作，但不得新领跨机器任务或声称 lease 仍有效。
