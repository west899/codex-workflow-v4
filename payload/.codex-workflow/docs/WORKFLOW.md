# Codex Workflow V3 使用说明

## 1. 这套工作流解决什么

V3 把工作流主体从项目根集中到 `.codex-workflow/`，补齐需求校准、`verified → done` 收尾、同机多 worktree lane，以及可选的跨机器预分配/原子 claim。它仍是合作式控制面，不替代 Git 分支保护、受保护 CI 和人工发布审批。

## 2. 第一次使用

1. 阅读根 AGENTS 指向的协议与 governance 文件。
2. 回答 PROJECT 的目标发现问题。
3. 从 Requirements Brief 模板创建 `governance/requirements/<brief-id>.md`。
4. 让 AI 复述“已确认 / 假设 / 未确认”，请用户纠偏，补齐正常、边界/失败和非目标场景。
5. 运行 `requirements-snapshot`，让用户批准精确 brief/revision/release/fingerprint；把批准信息写回 Brief。
6. 运行 `requirements-gate`，再把同一基线写入 PROJECT 与 Backlog；随后运行 `sync-status --apply` 生成第一份状态快照。
7. 将 Backlog 从 draft 审批为 ready 项；任务才可进入实现。

```text
py -3 .codex-workflow/bin/workflow_check.py requirements-snapshot .codex-workflow/governance/requirements/REQ-001.md
py -3 .codex-workflow/bin/workflow_check.py requirements-gate .codex-workflow/governance/requirements/REQ-001.md
py -3 .codex-workflow/bin/workflow_state.py sync-status --apply
```

## 3. 建立 task contract

从仓库 Skill 的 task-record template 创建 `.codex-workflow/state/runs/<task-id>.json`。Coordinator 填写来源、需求基线、请求、范围、非目标、allowed paths、resource keys、验收、风险、授权和 exact base commit。task record 的后续写入只经 state/lane 命令；不要手改 JSON 来跳过 generation 或角色边界。

```text
py -3 .codex-workflow/bin/workflow_check.py preflight .codex-workflow/state/runs/MVP-001.json
```

### 3.1 JSON Schema 结构门禁

四份随包 schema 是运行时门禁：`requirements-v1.schema.json` 在 Brief snapshot/gate 读取时校验；`task-record-v3.schema.json` 在所有 V3 task record 读取和 state 写入前校验，并强制其 `lane-v1.schema.json` 引用；`remote-claim-v1.schema.json` 在远端 claim 生成、提交和读取时校验。远端 release 也会校验目标分支中的 task record 和 claim。任一缺字段、错误类型/枚举/长度/模式或无效 lane 都 fail closed，不写 record、queue、closeout 或远端 ref。

校验器仅支持这四份 schema 已使用的 JSON Schema 子集，且不依赖第三方包；新增未支持的 schema 关键字同样会明确失败。修复源数据或 schema/实现并补回归测试，不能通过手改状态文件绕开门禁。V2 record 仅用于历史读取兼容，不能进入 V3 state 写入。

### 3.2 已批准 Requirements 的变更

不要直接改 PROJECT/Backlog 的 baseline。已批准 Brief 改 revision/fingerprint，或用新 Brief 替代旧 Brief 时，`manual`、`status` 和 Backlog task 的 preflight 会停止。先生成影响报告：

```text
py -3 .codex-workflow/bin/workflow_check.py requirements-impact .codex-workflow/governance/requirements/REQ-001.md --json
py -3 .codex-workflow/bin/workflow_state.py apply-requirements-impact .codex-workflow/governance/requirements/REQ-001.md --expected-fingerprint <approved-sha256>
py -3 .codex-workflow/bin/workflow_state.py apply-requirements-impact .codex-workflow/governance/requirements/REQ-001.md --expected-fingerprint <approved-sha256> --apply
```

报告从 Git 历史中读取上一份 approved Brief，按稳定 `REQ-*` ID 比较，并读取本机 live lane 的 task record。它会把未开始且受影响或未映射的 Backlog 项置为 `blocked`，把已完成项标为 `preserve_history`，并把活动 task 标为 `human_decision_required`。`--apply` 同时更新 PROJECT/Backlog baseline、写入 `state/requirements-impacts/` 报告并刷新 `STATUS.md`；这些受管文件应一起提交。它绝不自动让活动 lane 继续。

若人类确认某个活动 task 的既有合同仍适用，先把该 lane rebase 到含影响报告的基线；需要 base refresh 时按第 9 节执行 `refresh-base`。然后由人类提供绑定报告 ID 的决定 JSON（`analysis_id`、`decision: continue`、`approved_by`、`approved_at`、`source`、`rationale`），再执行：

```text
py -3 .codex-workflow/bin/workflow_state.py resolve-requirements-impact <record> --analysis-id <report-sha256> --decision-json decision.json --apply
```

该命令把 task 绑定到新 baseline，保存人类决定，并清空旧 Developer/Review/acceptance/approval/integration 证据；Developer、Reviewer 和 gate 必须重新完成。若决定停止或重写 task，不执行此命令，保持 blocked 并按 lane 恢复/abandon 流程处理。

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

### 4.1 Coordinator / Integrator 持久租约

共享写入另有两类本机持久租约：`locks/coordinator.lock.json` 保护 claim、queue、registry 和 runtime claim 的协调写入；`locks/integrator.lock.json` 保护目标分支的 closeout 与 claim 释放。每份记录含 token、generation、随机 owner/session、PID、heartbeat 和 TTL；旁边的 `.guard` 是短时 OS advisory guard。普通 `--apply` 命令自动取得并按同一 token 释放租约；进程崩溃时 OS guard 会释放，但 JSON 会保留为 active/stale，后续命令绝不自动清除或接管。

当一次人工集成跨越外部 ff、PR 或 CI 等多个命令时，先显式持有 Integrator 租约，保存输出的 token/generation，并把它传给 closeout/confirm：

```text
py -3 .codex-workflow/bin/workflow_lane.py lock-acquire integrator --apply
py -3 .codex-workflow/bin/workflow_lane.py lock-heartbeat integrator --token <token> --generation <generation>
py -3 .codex-workflow/bin/workflow_state.py prepare-local-closeout <record> --target-ref <ref> --result-commit <sha> --integrator-token <token> --integrator-generation <generation> --apply
py -3 .codex-workflow/bin/workflow_state.py confirm-closeout <record> --target-ref <ref> --closeout-commit <sha> --integrator-token <token> --integrator-generation <generation> --apply
py -3 .codex-workflow/bin/workflow_lane.py lock-release integrator --token <token> --generation <generation> --apply
```

`lock-heartbeat` 是 token/generation 精确匹配时的 runtime 续租例外。若 TTL 已过，只能先检查并带已知旧 token/generation、人工批准人与批准来源显式接管；takeover 输出新的活跃租约，完成工作后仍须显式 release：

```text
py -3 .codex-workflow/bin/workflow_lane.py lock-status integrator
py -3 .codex-workflow/bin/workflow_lane.py lock-takeover integrator --expected-token <old-token> --expected-generation <old-generation> --approved-by <human> --approval-ref <evidence> --apply
```

Coordinator 的 claim、queue、registry 和 release 命令会自动持有并释放同类租约；`lock-status/takeover/release coordinator` 用于观察或恢复崩溃留下的租约，不要在普通 claim 前手工留下一个 active Coordinator 租约。两个 takeover 竞争时只有一个 compare-and-swap 能成功；错误 token、generation、活跃租约或缺少审批都 fail closed。

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

delivery hash 绑定 base；patch hash 对 canonical entries 去掉 base identity；snapshot ID 再绑定 task/lane/branch。二进制、mode、symlink 的旧/新目标和关闭 rename 猜测后的 delete/add 都进入 canonical delta；merge conflict 只摘要 base 到最终 resolved tree 的实际差异。专门 fixture 用 Git index 构造 symlink，因此不依赖 Windows 创建 symlink 的权限。可变 task/PLAN/Backlog 状态不进入产品 delivery hash，但由独立门禁验证。

## 6. local bootstrap 收尾

仅当 layout 策略启用、task 在 allowlist、Review/gate 通过且用户批准精确 task/target/snapshot/delivery hash 时使用。`allowed_task_ids` 是唯一且有顺序的列表，`expires_after_task` 必须是其中一个 Backlog task；只有列表中位于该 task 之前（含自身）的 task 可用。截止 task 的 Backlog durable status 一旦成为 `done`，新的 `prepare-integration --mode local_bootstrap`、已有 pending task 的 `integration-preflight`、入队及 local closeout 都会 fail closed，不增加 record generation、不创建 queue/closeout 状态；此后改用 `remote_pr_ci`。先记录 approval，再准备集成：

```text
py -3 .codex-workflow/bin/workflow_state.py record-approval <record> --approval-json approval.json --apply
py -3 .codex-workflow/bin/workflow_state.py prepare-integration <record> --mode local_bootstrap --apply
```

`local_worktree` lane 必须先入集成队列。优先级数字越小越靠前；优先级相同时按入队时间和 queue ID 排序。只有队首可以 prepare local closeout：

```text
py -3 .codex-workflow/bin/workflow_lane.py queue <lane-id> --priority 100 --apply
```

入队会将 sealed snapshot 的 `delivery_commit`、hash 和实际 `changed_paths` 写入 queue entry，并逐一验证已排队 lane 的 registry、token、generation 和 task record。后入队 lane 的实际路径与任一已排队 lane 重叠时会被拒绝；路径比较使用跨平台规范形式，大小写或 Unicode 等价路径也会停止入队。queue 会写入 task record，所以 Integrator 在 ff-only 前先把该 record 的 prepared/queued 状态提交到 lane branch。将 task branch 以 ff-only 集成到配置的本地目标分支。这是 Integrator 的显式 Git 操作，脚本不会替用户自动 merge。若该人工步骤跨多个命令，先按 4.1 显式持有 Integrator 租约，并在后续 closeout/confirm 传入同一 token/generation。目标 worktree 必须干净，且 exact verified commit 已在 target ref 中。然后：

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

外部执行 push、产品 PR、CI 和人工合并；本工作流不会自动完成这些操作。V3.0 只接受严格 ff 证明。跨这些外部步骤时可按 4.1 持有同一 Integrator 租约；fetch 后提供 evidence JSON（target_ref、target_parent、pr_head_commit、result_commit、merge_strategy=`ff`、PR URL、全部 success 的 CI checks），从最新目标基线创建 closeout commit：

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

远端 claim/resource refs 只能在 closeout 状态已经出现在最新远端目标 ref 后释放。先 fetch 目标分支并 dry-run，记录输出的 exact claim OID；再把该 OID 作为 CAS token 显式应用：

```text
git fetch origin --prune
py -3 .codex-workflow/bin/workflow_lane.py remote-release <record>
py -3 .codex-workflow/bin/workflow_lane.py remote-release <record> --expected-claim-oid <dry-run-oid> --apply
```

命令会交叉检查远端 advertised target 与本地 remote-tracking ref、目标 ref 中的 integrated task record、Backlog 和 closeout fingerprint，再以 `push --atomic` 和每个 ref 的 expected OID 一次删除 task claim 与全部 resource claims。缺少 token、claim 在 dry-run 后被 heartbeat/handoff 推进、任一 resource ref 漂移或 target 未含 closeout时均零删除失败。成功后保留 task branch，移除匹配的本地 pointer，并在 git-common-dir audit 中记录 target/OID/refs；另一 clone 执行 fetch --prune 后应看不到 claim/resource refs。

## 10. 状态报告语言

`state/STATUS.md` 是受管状态的生成快照，不是新的真相来源。它汇总当前 Requirements baseline/contract、Backlog durable counts、task records（包含 live local lane）和已应用的影响报告。single/Coordinator 的 state mutation、closeout 与 Requirements impact apply 会自动刷新；隔离 lane 不写该共享文件，避免把状态快照带入 delivery/rebase 冲突。外部 Git 操作、lane mutation、手动治理编辑或恢复后要从 Coordinator/integration worktree 检查并按需重建：

```text
py -3 .codex-workflow/bin/workflow_check.py status
py -3 .codex-workflow/bin/workflow_state.py sync-status --apply
```

`status` 对快照 fingerprint、当前 Brief 漂移和无效 approved contract 都 fail closed。`sync-status --apply` 在 task lane 中拒绝执行；不要手改 `STATUS.md` 伪造状态。

- `in_progress`：正在当前 lane 实现。
- `verified`：本地 sealed snapshot 已通过 Developer/Reviewer/gate，尚未集成。
- `done`：配置的目标 ref 含 closeout 状态并已 confirm；说明本地或远端范围。
- `released`：另有发布证据；不能由 done 推断。
- `remote_sync=pending`：可继续当前 lane 本地工作，但不得新领跨机器任务或声称 lease 仍有效。
