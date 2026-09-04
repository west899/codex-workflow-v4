# Codex Workflow V4

可移植的 Codex 项目工作流包。当前可安装产品是 **4.1.0**（tag `v4.1.0`）。新任务默认 `task-record-v4`：先做一个可观察的 focus core slice，人类通过 STATUS / 决策卡片确认方向，再进入独立 Review 和集成。

交付安全外环沿用 V3：Requirements fingerprint、allowed paths、lane 隔离、canonical delivery、独立 Reviewer、gate、CAS、串行 integration 和 two-phase closeout。已有 V3 任务按原算法收尾。

产品版本是 V4；安装布局里的 `layout_version` / `protocol_version` 仍为 `3`，表示此外环协议代次，不是产品名。GitHub 默认分支是 `v4`。安装请钉住 `v4.1.0`（或只接收 V4 补丁的 `v4` 分支），不要从后续 V5 开发分支安装。

完整已实现能力与测试依据见 [功能.md](功能.md)。版本记录见 [CHANGELOG.md](CHANGELOG.md)。许可证为 [MIT](LICENSE)。

## 已验证能力（摘要）

依据：`python3 -B verify_package.py`（macOS / CPython 3.9）。每条的测试名见 [功能.md](功能.md)。

| 能力 | 依据 |
| --- | --- |
| 安装、V2 升级计划、ownership 边界 | `tests/test_install.py` |
| JSON Schema fail-closed | `tests/test_schema_gate.py` |
| V4 合同、decision、product checkpoint | `tests/test_v4_contract.py`, `tests/test_v4_workflow.py` |
| STATUS 产品摘要 | `tests/test_v4_status.py` |
| Backlog focus/WIP、滚动 Requirements、风险档、护栏 | `tests/test_v4_phase_b.py` |
| 只读 provider receipt；strict-ff 远端 closeout | `tests/test_v4_phase_c.py` |
| lane、queue、local/remote closeout、恢复 | `tests/test_workflow_lane.py`, `tests/test_parallel_closeout.py`, `tests/test_end_to_end.py`, `tests/test_fault_recovery.py` |

Windows / Python 3.12 / 3.13 **未验证**。

## V4 产品线

- `v4` 分支和 `v4.*` tag 是 V4 产品线。4.1.0 之后只接受 V4 缺陷修复，不把 V5 功能合进 `v4`。
- 已安装项目以 `.codex-workflow/install/manifest.json` 的 `package` / `version` 为准；`doctor` 会打印这两项。
- 已有 V3 task record 继续按 V3 算法收尾；V4 record 继续按 V4 算法收尾。
- 以后若升级 V5：新包必须仍能发现 `codex-workflow-v4` runtime；不得改写 `v4` 历史、移动已发布 tag，也不得把未完成的 V4 任务强制改写成 V5。
- 历史 tag `v4.0.0` 保留，但缺少 runtime 改名与随后的 fail-closed 修复；新安装使用 `v4.1.0`。

## 明确不做

- 不自动 push、开 PR、合并、force-update、删除 branch/worktree、发布或部署
- 不把 GitHub branch protection / merge queue 当成 Independent Reviewer
- 不接受 squash / merge-commit / merge-queue 作为产品集成（仍要求 strict-ff）
- integration/closeout 不写成 `released`

## 安装

需要 Git 和 Python 3.9+。Windows 上推荐 `py -3`；脚本使用当前 `sys.executable`。

从冻结的产品 tag 取得安装器，再写入目标项目：

```text
git clone --branch v4.1.0 --depth 1 https://github.com/west899/codex-workflow-v4.git
cd codex-workflow-v4
python install.py <project-root> --project-name <name>
```

已有 clone 时：

```text
git fetch origin --tags
git checkout v4.1.0
python install.py <project-root> --project-name <name>
```

不要用历史 `main` 上的 phase-0 提交安装。`main` 已与 `v4` 对齐到同一产品提交；V5 开始后仍以 `v4` / `v4.*` 为 V4 产品线。

默认 `parallel.mode=single`。同机多线：

```text
python install.py <project-root> --project-name <name> --parallel-mode local_worktree
```

安装器不会创建产品基线 commit、push、PR、merge、发布或部署。工作流是嵌在产品 Git 仓库里的助手层，不另建产品子目录。

卸载默认只打印清单（像日志：会列出要删的包文件、要保留的治理状态、live lane、未收口任务）。确认后加 `--apply`。产品源码不会被删。

```text
python install.py <project-root> --uninstall
python install.py <project-root> --uninstall --apply
python install.py <project-root> --uninstall --apply --purge-state
python install.py <project-root> --export-product <clean-dir>
python install.py <project-root> --export-product <clean-dir> --apply
```

`--purge-state` 会删掉 `.codex-workflow/governance` 与 `state`（工作流自己的档案）。`--export-product` 复制业务文件，排除工作流引擎和状态。

### V2 升级

```text
python install.py <project-root> --plan-upgrade
```

- 存在 `.agent/active-task` 时零写入拒绝
- 没有 V2 manifest 时必须 `--adopt-v2`
- `--force-package` 只替换 package 文件，不覆盖 PROJECT/PLAN/DECISIONS/Requirements/Backlog/runs/plans

## 安装后的布局

```text
AGENTS.md                              # 薄发现入口
.agents/skills/                        # Coordinator / Developer / Reviewer Skills
.codex/hooks.json
.codex-workflow/
  layout.json                          # 路径、并行、集成策略（协议代次 3）
  protocol/  governance/  state/  schemas/  bin/  docs/WORKFLOW.md
```

## JSON Schema 结构门禁

`requirements-v1`、`task-record-v3`、`task-record-v4`（均含其 `lane-v1` 引用）、`remote-claim-v1`、`provider-receipt-v1`、`developer-evidence-v1` 和 `review-evidence-v1` 是强制结构门禁，不是只供阅读的示例。Requirements snapshot/gate 读取 Brief 时、对应版本 task record 读取时、state/lane 写入更新前、Developer/Review evidence 入库前，远端 claim 的生成、提交、读取和远端 closeout 证明时，以及只读 provider receipt 校验时都会 fail-closed 校验。字段缺失、类型/枚举/长度/模式不符或引用的 lane 无效时，命令不写 task record、queue、closeout 或远端 claim/ref。Provider receipt 只能附加，不能替代 Independent Reviewer 或 ff/CI 本地证明。远端产品集成默认仍是 strict-ff，integration/closeout 不写成 released。

校验器只实现包内这些 schema 已使用的受限 JSON Schema 子集，随包以 Python 标准库运行；若后续 schema 引入未实现的关键字，也会停止而不是静默忽略。历史 V2 record 仅保留读取兼容，不能借此绕过 V3/V4 写入门禁。

## Evidence Contract v1

Developer evidence 必须把命令、有限 scope 和 claim 分开记录。scope kind 只允许：

- `canonical_delivery_paths`
- `declared_path_call_graph`
- `explicit_command_set`
- `explicit_test_set`
- `explicit_runtime_surfaces`
- `repository_tree`

每个 claim 都要引用一个 scope 和实际 supporting commands；`record-developer` 在 sealed snapshot 上生成 canonical `evidence_fingerprint`。scope target 禁止使用 `all`、`*`、`repository_wide` 或 `all_dry_runs`；完整 canonical product tree 范围只能使用 `repository_tree` target `.`，并显式排除 `mutable_workflow_control`。Developer `handoff` 只包含 `claim_ids`、`remaining_risks` 和 `review_focus`。

Reviewer 必须对每个 `claim_id + evidence_fingerprint` 给出 `confirmed`、`narrowed`、`rejected` 或 `unverified`。只有全部 claim 都为 `confirmed` 时 Review 才能 `pass`。完整字段见安装后的 `.codex-workflow/docs/WORKFLOW.md`。

## 最短正确流程

1. 写有用户来源的 PROJECT 事实。
2. Requirements Brief：复述、纠偏、场景，批准 exact fingerprint。
3. gate 通过后批准 Backlog，授权一个 V4 task record，确认 `focus_slice_id`。
4. Developer 形成可观察结果和干净 delivery commit，`record-developer`。
5. required checkpoint：人类观察并 `record-decision` 后才独立 Review。
6. `record-review` → `complete-task` → `gate` → `mark-verified`。
7. `local_bootstrap` 或 `remote_pr_ci` 串行 closeout；confirm 后释放 claim。

## 常用命令

```text
py -3 .codex-workflow/bin/workflow_check.py manual
py -3 .codex-workflow/bin/workflow_check.py requirements-snapshot <brief>
py -3 .codex-workflow/bin/workflow_check.py requirements-gate <brief>
py -3 .codex-workflow/bin/workflow_check.py preflight <record>
py -3 .codex-workflow/bin/workflow_check.py gate <record>
py -3 .codex-workflow/bin/workflow_check.py provider-receipt <receipt.json>
py -3 .codex-workflow/bin/workflow_check.py status [--json]

py -3 .codex-workflow/bin/workflow_lane.py claim <task> --base main [--apply]
py -3 .codex-workflow/bin/workflow_state.py request-decision <record> --decision-json <file>
py -3 .codex-workflow/bin/workflow_state.py record-decision <record> --decision-id <id> --expected-fingerprint <sha256> --resolution-json <file> [--apply]
py -3 .codex-workflow/bin/workflow_state.py record-developer <record> --evidence-json <file> --delivery-commit HEAD [--apply]
py -3 .codex-workflow/bin/workflow_state.py record-review <record> --review-json <file> [--apply]
py -3 .codex-workflow/bin/workflow_state.py prepare-remote-closeout <record> --evidence-json <file> [--apply]
```

除 heartbeat 外，状态修改默认 dry-run，`--apply` 才写入。完整命令表见 [功能.md](功能.md) 与 `.codex-workflow/docs/WORKFLOW.md`。

## 包验证

```text
python -B verify_package.py
```

## 随包模块（各文件的职责）

| 文件 | 职责 |
| --- | --- |
| `install.py` | 安装、升级计划、ownership、回滚 |
| `verify_package.py` | 包门禁：必需文件、schema、Hooks、全量 unittest |
| `workflow_paths.py` | 解析 `.codex-workflow` 路径、worktree、common-dir |
| `workflow_lock.py` | OS advisory lock 与持久租约 CAS |
| `workflow_common.py` | fingerprint、schema、STATUS、V4 合同与 receipt 校验 |
| `workflow_check.py` | 只读门禁（preflight/gate/status/provider-receipt） |
| `workflow_state.py` | 受管状态写入（decision/evidence/closeout） |
| `workflow_lane.py` | lane、queue、remote claim；不承载产品语义 |
| `codex_stop_hook.py` | 提示下一步，不批准、不集成 |

## Codex 发现入口依据

- [仓库 AGENTS 与 Skills](https://learn.chatgpt.com/docs/customization/overview#skills)
- [项目 Hooks](https://learn.chatgpt.com/docs/hooks#where-codex-looks-for-hooks)
- [项目 Custom Agents](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents)
- [Git worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees)
