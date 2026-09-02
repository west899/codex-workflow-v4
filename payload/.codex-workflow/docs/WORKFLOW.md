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

新任务使用 V4 core/supporting 模板创建 `.codex-workflow/state/runs/<task-id>.json`。Coordinator 先和人类确认 `focus_slice_id`、观察入口、真实/临时部分和架构基线，再填写来源、需求基线、请求、范围、非目标、allowed paths、resource keys、验收、风险、`delivery_contract`、授权和 exact base commit。已在进行中的 V3 record 继续走 V3 closeout，不要改写其 pending fingerprint。task record 的后续写入只经 state/lane 命令；不要手改 JSON 来跳过 generation 或角色边界。

`checkpoint.mode=required` 时，独立 Review 前必须有当前 snapshot 的 observation receipt 和人类产品方向确认。使用 `request-decision` / `record-decision`，不要新增另一套 checkpoint 命令。冲突答复必须显式 `supersede`。`workflow_lane.py` 不承载产品语义。STATUS 和 Stop Hook 只派生下一动作，不批准、不改状态。Backlog focus metadata 与 task contract 必须一致；默认 WIP 为 1 个未确认方向的 core slice。`horizon=future_candidate` 不能进入已批准 Must 合同。small/no-trigger 可将 retrospective 标为 `not_required`。V4 任务必须有 Backlog focus metadata。产品方向 `accepted` 后，core slice 的 `direction_confirmed` 会写入 Backlog。`rolling-promotion` 列出须晋升出 Must 合同的 future candidate。pending/queued 后用 `pending-queued-recovery` 核验：只允许 abandon-only，`dequeue`/`reopen` 失败，且不伪造 done；真正清理 runtime 仍走 `workflow_lane.py release --abandon`。

```text
py -3 .codex-workflow/bin/workflow_check.py preflight .codex-workflow/state/runs/MVP-001.json
```

### 3.1 JSON Schema 结构门禁

随包 schema 是运行时门禁：`requirements-v1.schema.json` 在 Brief snapshot/gate 读取时校验；`task-record-v3.schema.json` / `task-record-v4.schema.json` 在对应版本 task record 读取和 state 写入前校验，并强制其 `lane-v1.schema.json` 引用；`developer-evidence-v1.schema.json` 和 `review-evidence-v1.schema.json` 分别在 `record-developer`、`record-review` 入库前校验；`remote-claim-v1.schema.json` 在远端 claim 生成、提交和读取时校验。远端 release 也会校验目标分支中的 task record 和 claim。任一缺字段、错误类型/枚举/长度/模式或无效 lane 都 fail closed，不写 record、queue、closeout 或远端 ref。

校验器仅支持这些 schema 已使用的 JSON Schema 子集，且不依赖第三方包；新增未支持的 schema 关键字同样会明确失败。修复源数据或 schema/实现并补回归测试，不能通过手改状态文件绕开门禁。V2 record 仅用于历史读取兼容，不能进入 V3/V4 state 写入。

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

### 5.1 Evidence Contract v1

Developer evidence 和 Review evidence 必须声明 `evidence_contract_version: 1`。Contract 把“执行了什么”与“证据能够支持多大结论”分开记录，任何结论都不能从命令成功自动扩张到更大的路径、调用图、测试集或 runtime 表面。

Developer evidence 包含四个受约束部分：

- `commands`：每项有唯一 `id`、精确命令、repo-relative `cwd`、exit code、是否为预期失败、精确结果和非空 `scope_ids`；
- `scopes`：每项有唯一 `id`、有限 `kind`、非空 `targets`，以及显式 `observed_surfaces`、`excluded_targets`；
- `claims`：每项有唯一 `id`、有限 `kind`、单一可验证 `predicate`、一个 `scope_id` 和非空 `supporting_command_ids`；
- `handoff`：只允许 `claim_ids`、`remaining_risks` 和 `review_focus`，不得用自由文本扩大 claim。

`claim.kind` 只允许 `static_analysis`、`runtime_observation`、`test_result` 或 `manual_observation`。非零 command exit code 必须同时声明 `expected_failure: true`；`explicit_runtime_surfaces` 必须逐项填写非空 `observed_surfaces`。`canonical_delivery_paths.targets` 必须与 workflow 重算的 canonical `changed_paths` 完全一致。

`scope.kind` 只允许以下值：

| kind | `targets` 的精确含义 |
| --- | --- |
| `canonical_delivery_paths` | canonical delivery 中逐项列出的规范化变更路径 |
| `declared_path_call_graph` | 明确列出的入口符号、路径边界和本次实际追踪到的调用图节点 |
| `explicit_command_set` | 逐项列出的命令入口、子命令或 mode 组合 |
| `explicit_test_set` | 完整限定的测试 ID 或精确测试文件与选择器 |
| `explicit_runtime_surfaces` | 明确列出的命令、mode、runtime bucket、接口或可观察表面 |
| `repository_tree` | target 只能是仓库根 `.`，`excluded_targets` 必须包含 `mutable_workflow_control`；精确 canonical product tree 由 sealed `snapshot_id` 绑定，supporting command 的 `cwd` 也必须为 `.` |

`targets` 不得包含 `*`，经规范化后也不得等于 `all`、`repository_wide`、`all_dry_runs` 或其他等价聚合 token。需要覆盖完整 canonical product tree 时只能使用 `repository_tree` 的 target `.`，并显式排除 canonical delivery 不纳入摘要的 `mutable_workflow_control`；其他 kind 不产生隐式“全仓”“全部调用点”“所有 dry-run”语义。路径 glob 也不能作为已观察路径集合的替代品。

每个 claim 必须同时引用一个已声明 scope 和实际 supporting commands，且每个 supporting command 的 `scope_ids` 必须包含该 claim 的 `scope_id`。每个 scope 和 command 都必须至少支持一个 claim，handoff 的 `claim_ids` 必须恰好覆盖全部 claims。`result`、`predicate`、handoff 或自然语言摘要都不能超出引用 scope。Developer 不填写 fingerprint；`record-developer` 在 sealed snapshot 上生成 `evidence_fingerprint`，并把 `snapshot_id` 与 fingerprint 加入持久化 Developer evidence。其 canonical JSON SHA-256 材料为算法 `codex-evidence-claim-v1`、`snapshot_id`、完整规范化 claim、完整引用 scope，以及按 command `id` 排序的完整 supporting commands。Reviewer 使用该 fingerprint 识别 snapshot、陈述、范围或命令证据的任何漂移。

完整 Developer evidence 示例：

```json
{
  "evidence_contract_version": 1,
  "agent_id": "developer-1",
  "commands": [
    {
      "id": "CMD-001",
      "command": "git diff --name-only <base> HEAD",
      "cwd": ".",
      "exit_code": 0,
      "expected_failure": false,
      "result": "Printed exactly src/feature.py and tests/test_feature.py",
      "scope_ids": ["SCOPE-001"]
    },
    {
      "id": "CMD-002",
      "command": "python -B -m unittest tests.test_feature.FeatureTests.test_happy_path tests.test_feature.FeatureTests.test_invalid_input",
      "cwd": ".",
      "exit_code": 0,
      "expected_failure": false,
      "result": "2/2 explicit tests passed",
      "scope_ids": ["SCOPE-002"]
    }
  ],
  "scopes": [
    {
      "id": "SCOPE-001",
      "kind": "canonical_delivery_paths",
      "targets": ["src/feature.py", "tests/test_feature.py"],
      "observed_surfaces": [],
      "excluded_targets": []
    },
    {
      "id": "SCOPE-002",
      "kind": "explicit_test_set",
      "targets": [
        "tests.test_feature.FeatureTests.test_happy_path",
        "tests.test_feature.FeatureTests.test_invalid_input"
      ],
      "observed_surfaces": [],
      "excluded_targets": ["all other test IDs"]
    }
  ],
  "claims": [
    {
      "id": "CLAIM-001",
      "kind": "static_analysis",
      "predicate": "The canonical delivery changes exactly the two listed paths.",
      "scope_id": "SCOPE-001",
      "supporting_command_ids": ["CMD-001"]
    },
    {
      "id": "CLAIM-002",
      "kind": "test_result",
      "predicate": "The two explicitly named feature tests pass.",
      "scope_id": "SCOPE-002",
      "supporting_command_ids": ["CMD-002"]
    }
  ],
  "handoff": {
    "claim_ids": ["CLAIM-001", "CLAIM-002"],
    "remaining_risks": ["No claim is made about tests outside SCOPE-002."],
    "review_focus": ["Check callers outside the changed paths for compatibility impact."]
  }
}
```

Reviewer 必须对当前 Developer evidence 的每个 claim 精确提交一次 `claim_assessments`。每项绑定 `claim_id + evidence_fingerprint`，并使用以下 assessment：

- `confirmed`：陈述、scope 和 supporting commands 全部足以支持原 claim；
- `narrowed`：证据只支持更窄范围，必须在 `notes` 中写明可确认的精确 targets 或 surface；
- `rejected`：证据或实现与 claim 冲突；
- `unverified`：Reviewer 在授权的只读边界内无法确认。

Review `status=pass` 只允许所有 claim assessment 都为 `confirmed`。出现任一 `narrowed`、`rejected` 或 `unverified` 时，Review 必须为 `changes_requested`；不得把范围修正降格为可随 `pass` 带过的自由文本备注。P0-P3 findings 继续描述独立发现，不能替代逐 claim assessment。

完整 Review evidence 示例。以下 64 个 `a` 表示演示 snapshot，两个 fingerprint 是上方 Developer evidence 在该 snapshot 下按合同算法计算出的有效示例值；实际提交必须使用当前 sealed snapshot 与 workflow 持久化的 fingerprint：

```json
{
  "evidence_contract_version": 1,
  "agent_id": "reviewer-1",
  "snapshot_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "status": "pass",
  "claim_assessments": [
    {
      "claim_id": "CLAIM-001",
      "evidence_fingerprint": "b3e1adb1bb978978358f7d9cfb0f03d3bd464d0bbe8aa666dd3a0512202a3590",
      "assessment": "confirmed",
      "notes": "Canonical base-to-delivery reconstruction contains exactly the two scoped paths."
    },
    {
      "claim_id": "CLAIM-002",
      "evidence_fingerprint": "5640edd6eacd01961b8184a6671ce5f1827a94e7f4bd189b8f620ce7d684533e",
      "assessment": "confirmed",
      "notes": "The command and result are limited to the two named test IDs and do not claim broader coverage."
    }
  ],
  "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
  "requirement_checklist": [
    "AC-001: implementation and scoped command evidence match the sealed snapshot."
  ],
  "accepted_findings": [],
  "summary": "Every Developer claim fingerprint is confirmed at its declared scope."
}
```

Evidence Contract v1 不从旧自由文本推断 claim。已经 `integration.status=integrated` 的旧记录保持只读兼容，不重写历史，也不重新开启 gate。尚未集成的任务只要 Developer 或 Review evidence 仍是旧格式，就必须在继续 Review、`complete-task`、gate 或 integration 前重新记录 Contract v1 evidence；内容发生变化时仍按新 snapshot 完整重走 Developer 与 Reviewer。

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
