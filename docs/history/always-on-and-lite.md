# 4.2 设计笔记：always-on 变薄与 lite-authorize

> 历史设计输入，已实现进 4.2.0。不是运行时真相源。
> 范围：当时 remaining-issue #1（工作流本身仍然太重）。

## 1. 问题定位：V4 的 always-on 重量

原始问题在 `docs/history/problem.md` 第 9 节：每一项机制单独看都合理，叠加后使用者和 AI 必须同时理解大量概念；小型功能承担与大型、高风险任务接近的流程成本；AI 容易把完成流程当作主任务。V4.1.0 在 V3 外环上加了产品内环，没有把外环从默认路径拿掉。

### 1.1 每次任务开始就必须读的文件

> V4.1.0 冻结点的 always-on 清单如下。工作树 step 1 已把根入口和协议 §1 改成薄启动：不再 `依次读取` 协议全文与 `WORKFLOW.md`；那两份仍随包，只在 claim / `record-developer` / closeout 打开。

根入口 `payload/AGENTS.md` 和 `payload/.codex-workflow/protocol/AGENTS.md` §1 **当时**要求，开始任何项目工作前依次读取：

| 顺序 | 路径 | 行数 | 角色 |
| --- | --- | --- | --- |
| 0 | `payload/AGENTS.md` | 15 | 薄发现入口 |
| 1 | `payload/.codex-workflow/protocol/AGENTS.md` | 113 | 协议：16 条 lane 不变量 + 完整闭环 |
| 2 | `payload/.codex-workflow/governance/AGENTS.md` | 10 | 项目规则 |
| 3 | `payload/.codex-workflow/governance/PROJECT.md` | 44 | 项目事实 |
| 4 | `payload/.codex-workflow/governance/PLAN.md` | 31 | 计划 |
| 5 | `payload/.codex-workflow/governance/DECISIONS.md` | 58 | 架构基线 + 11 条工作流决定 |
| 6 | `payload/.codex-workflow/docs/WORKFLOW.md` | 389 | 使用说明、Evidence Contract、closeout |

合计 **约 660 行 always-on 指导**，然后还要读当前 Requirements Brief、Backlog、task record、代码和测试。协议还要求立刻跑：

```text
py -3 .codex-workflow/bin/workflow_check.py start
py -3 .codex-workflow/bin/workflow_check.py manual
```

Coordinator skill `payload/.agents/skills/orchestrate-project-task/SKILL.md` 再加 `status`。这三步发生在任何产品代码之前。

对照：Codex 官方要求 AGENTS.md「Keep it small」，默认合计上限 32 KiB；Claude Code 官方要求 always-on `CLAUDE.md` 目标 **200 行以内**。V4 把完整外环手册放进了每次会话。

### 1.2 到「第一个可观察产品结果」之前的强制路径

`README.md`「最短正确流程」+ `WORKFLOW.md` §2–3 + Coordinator skill §2–5，一条新功能在 Developer 写出可观察结果之前必须经过：

```text
PROJECT 有来源事实
→ 写 Requirements Brief（复述 / 纠偏 / 场景 / 关闭阻断问题）
→ workflow_check.py requirements-snapshot
→ 人类批准 exact brief/revision/release/fingerprint
→ workflow_check.py requirements-gate
→ 同一基线写入 PROJECT 与 Backlog
→ workflow_state.py sync-status --apply
→ Backlog 从 draft 批成 ready，写入 focus metadata（kind / focus_slice_id / supports_task_id / WIP）
→ 从 task-record-v4-core-template.json 建 state/runs/<id>.json
→ 人类确认 focus、观察配方、真实/临时、架构基线、WIP
→ workflow_check.py preflight
→ workflow_lane.py claim（先 dry-run，再 --apply）
→ 才进入 Developer 实现
```

`preflight` 走 `workflow_check.py` 的 `task_gate(..., final=False)`，对 V4 还会跑 `_v4_contract_gate`：live Requirements baseline、focus 关系、Backlog focus metadata、planning/risk、live 架构基线、live 依赖、architecture gate、decision refs、continuation、contract fingerprint。缺一块就不能授权实现。

第一个可观察结果出现之后，默认路径仍未结束：`record-developer` → required checkpoint 的 `request-decision` / `record-decision` → 独立 Reviewer `record-review` → `complete-task` → `gate` → `mark-verified` → `prepare-integration` → `queue` → two-phase closeout → `confirm-closeout` → `release`。这是外环，不是「看见功能」所需的最小集，但 V4 把它写成同一条主链。

### 1.3 命令面：三条 CLI，约 53 个入口

`功能.md` §12 命令表：

```text
workflow_check.py: start / manual / doctor / preflight / snapshot / gate /
                   requirements-snapshot / requirements-gate / requirements-impact /
                   rolling-promotion / status / integration-preflight /
                   closeout-gate / provider-receipt
                   （14）

workflow_lane.py:  claim / adopt / list / heartbeat / expand-resources / queue /
                   refresh-base / recover / rebuild / release /
                   lock-status / lock-acquire / lock-heartbeat / lock-takeover / lock-release /
                   preassign / resume-remote /
                   remote-claim / remote-heartbeat / remote-takeover /
                   remote-handoff / remote-release
                   （22）

workflow_state.py: request-decision / record-decision / pending-queued-recovery /
                   record-developer / record-review / complete-task / mark-verified /
                   record-approval / prepare-integration /
                   prepare-local-closeout / prepare-remote-closeout /
                   confirm-closeout / reconcile / invalidate-integration /
                   apply-requirements-impact / resolve-requirements-impact / sync-status
                   （17）
```

实现体量：`workflow_common.py` 6038 行，`workflow_lane.py` 2684，`workflow_state.py` 2405，`workflow_check.py` 1605。默认 dry-run 再 `--apply` 使每一步状态写入变成两次调用。人类要理解 task / Backlog / lane effective / integration 四套状态，才能读懂「现在到哪了」。

### 1.4 Schema：small 任务也要填满 27 个顶层必填字段

`payload/.codex-workflow/schemas/task-record-v4.schema.json` `required`：

```text
version, generation, phase, task_id, status, contract_fingerprint,
source, requirements_impact, request, scope, planning,
implementation_authorization, risk, base_commit, acceptance,
delivery_contract, decision_log, lane, verification, developer,
review, human_approvals, integration, process_retrospective,
rule_proposals, remaining_risks
```

`delivery_contract` 对 core_slice 还强制：focus、requirement_ids、acceptance_ids、checkpoint、observation recipe（method / entrypoint_ref / fixture_ref / steps）、known_placeholders、architecture baseline + guardrails。模板 `task-record-v4-core-template.json` 把这些一次铺开。

### 1.5 small / no-trigger 仍然付的同一笔账

V4 阶段 B 声称「让 planning/risk 真正改变流程成本」。代码里实际减免只有一处：

- `v4_retrospective_not_required_allowed`（`workflow_common.py`）：仅当 `planning.level=small` 且全部 risk flag 为 false、且 retrospective questions 全 false 时，`process_retrospective.not_required` 可通过 `complete-task` / `gate`。
- `validate_v4_planning_risk`：只有 high tier 才强制 `exec_plan`。small 与 medium 在授权前没有更短合同。

small / no-trigger **仍然必须**：

1. 读完 §1.1 的 always-on 文件并跑 `start` / `manual`。
2. 若走目标版本路径：完整 Requirements 2A + fingerprint + Backlog focus/WIP。
3. 创建带 27 个必填字段的 `task-record-v4`（含 observation 与 architecture）。
4. `preflight` 的全部 V4 live gate。
5. `claim`（即便 `parallel.mode=single`）。
6. Evidence Contract v1（有限 scope、command-bound claim、fingerprint）。
7. 独立 Reviewer `record-review`（checkpoint.mode=required 时还要先做产品观察 JSON）。
8. `gate` → `mark-verified` → prepare-integration → two-phase closeout。

协议允许 incident / maintenance / user_directive 不做完整版本 Brief，但仍要范围、非目标、验收、风险、实现授权；新任务默认仍是 v4 schema。`core_slice` 不能用 kind 降低敏感数据 / 破坏性 / 不可逆架构的高风险门禁——这是对的——但反过来，**没有风险触发的 small 任务也不能降低主链**。这就是 problem.md §9「小型功能承担与大型、高风险任务接近的流程成本」在 4.1.0 里的具体落点。

### 1.6 重量来自两处叠加，不是单点缺陷

1. **Always-on 指导过重**：把外环手册（lane 不变量、Evidence Contract 全文、closeout 步骤）放进每次会话必读，而不是技能引用。
2. **默认路径 = 最大路径**：内环（focus / checkpoint / decision）叠在外环（Brief / claim / evidence / Reviewer / queue / closeout）上，风险比例只减免复盘，不减免命令数、字段数或必读页数。

V5 若再加字段或命令，会重复 V4 的失败模式。要减的是默认路径上的步骤，不是外环的 fail-closed 能力。

## 2. 外部研究：always-on vs on-demand vs fail-closed

下列 URL 均为本次拉取的现行文档，用来回答同一比较题：指导放在 always-on 上下文、按需技能，还是确定性执行（hooks / CI / 脚本）。

### 2.1 OpenAI Codex：AGENTS 保持短，技能渐进披露，规则用 hooks/CI 执行

| 层 | 官方说法 | 对 V4 的对照 |
| --- | --- | --- |
| Always-on | [Custom instructions with AGENTS.md](https://developers.openai.com/codex/guides/agents-md)：启动时组指令链；默认合计 `project_doc_max_bytes` = 32 KiB。示例是「跑测试 / 用 pnpm / PR 前 lint」这类短约定，不是整本工作流手册。 | V4 根 `AGENTS.md` 是薄入口（15 行），但立刻强制再读 660 行协议+手册。超过「Keep it small」。 |
| On-demand | [Agent Skills](https://developers.openai.com/codex/skills) / [Customization](https://developers.openai.com/codex/concepts/customization)：先注入 name + description + path，最多占上下文 2% 或 8 000 字符；选中后才读 `SKILL.md`；references/scripts 再按需。官方构建顺序：先 AGENTS.md，再 skill/plugin，再 MCP，再 subagent。 | V4 三个 skill 本身不算长，但 Coordinator skill 把整条 V3+V4 闭环写进入口；协议还要求未调用 skill 也读完 WORKFLOW.md。没有「按阶段加载 references」。 |
| Fail-closed | [Hooks](https://developers.openai.com/codex/hooks/)：在 PreToolUse / Stop 等生命周期跑脚本或 MCP；用来扫描密钥、在 turn 结束时做校验。Customization 明确要求 **AGENTS.md 与 pre-commit / linter / typechecker 配对**，不要让模型记住不可破的规则。 | V4 的 fail-closed 在 Python CLI + schema，这是对的。错在把同一套规则再全文复制进 always-on markdown，让模型「记住」lane 不变量，而不是只在写状态时执行。 |

比较题答案：Codex 把「每次都要知道的」限制在短 AGENTS.md；把「有时才走的流程」放进 skill 渐进披露；把「绝不能靠模型自觉」放进 hooks/CI。V4 把后两层的说明书也塞进了第一层。

### 2.2 Claude Code：200 行 always-on；技能按需；hooks 才是保证

| 层 | 官方说法 | 对 V4 的对照 |
| --- | --- | --- |
| Always-on | [How Claude remembers your project](https://code.claude.com/docs/en/memory)：`CLAUDE.md` 每会话加载，是上下文不是强制配置；目标 **200 行**。多步手续或局部知识应搬到 skill 或 path-scoped rule。[Best practices](https://code.claude.com/docs/en/best-practices)：每行问「删掉会导致犯错吗？不会就删」。过长会导致模型忽略真正指令。小修复（错字、日志、重命名）应直接做，不必 plan mode。 | V4 always-on ≈ 660 行，且含完整 Evidence Contract 示例 JSON。这正是官方警告的「bloated instructions → ignored instructions」。 |
| On-demand | [Extend Claude Code](https://code.claude.com/docs/en/features-overview)：CLAUDE.md = always-on；Skill = 按需工作流；description 每会话可见，正文仅在使用时加载。[Skills](https://code.claude.com/docs/en/skills)：`SKILL.md` 建议 <500 行，细节进 references。 | V4 skill 入口已经接近「整本手册」。应按阶段拆：授权、实现、Review、closeout 各一份 reference，默认只加载当前阶段。 |
| Fail-closed | 同一页把 Hook vs Skill 写死：hook **在事件上保证触发**；skill 由模型解释，结果可变。「never edit `.env`」写在 markdown 里只是请求；`PreToolUse` 拦截才是执行。Stop hook 可做确定性门，连续拦截有上限。 | V4 Stop Hook（`codex_stop_hook.py`）只提示下一步，不批准、不集成——这符合 Codex/Claude 对 Stop 的定位。状态写入的 schema/CAS 才是真正 fail-closed，应保留。不要把更多规则搬回 always-on 文本。 |

比较题答案：Claude 把「保证发生」和「希望模型记得」分成 hooks 与 markdown。V4 的脚本门禁已经走 hook/CLI 这条正确路；重量来自还要求模型在动手前读完同一套规则的说明书。

### 2.3 DORA：小批次反馈；重审批同时拖慢并增加故障

[Working in small batches](https://dora.dev/capabilities/working-in-small-batches/)（2025-12-08 更新）：小批次缩短反馈、降低沉没成本；生成式 AI 下更关键，因为 AI 会放大交付不稳定性，小批次是安全网。明确陷阱：**先把小批次再捆成大批才送去测试/发布**，等于把反馈又推迟。另一原因是「交接的固定成本太大」，团队才攒大批。

对 V4 的对照：内环（focus slice、checkpoint）符合小批次。但默认路径把 Brief → 27 字段合同 → claim → evidence → Reviewer → queue → two-phase closeout 做成**每次小改的固定交接成本**。结果是：要么 AI 绕开流程直接改代码（破坏外环），要么小改也走完整外环（破坏反馈速度）。DORA 的解法不是取消测试/审查，而是降低**单次交接的固定成本**，让小时级批次付得起。

比较题答案：DORA 把人类放在「假设是否值得、合并是否安全」上，而不是每个内环步骤。确定性检查（自动化测试、CI）应在批次内部快速跑完。V4 把人类审批和 CLI 仪式放在内环之前过多。

### 2.4 GitHub Spec Kit：阶段命令按需调用；质量门可选

[Agentic SDD](https://github.github.com/spec-kit/reference/agentic-sdd.html)：主链是 `/speckit.specify → plan → tasks → implement`。**只有 specify 在 plan 之前严格必需**；clarify / checklist / analyze 是「有实质歧义时才加」的质量门。小功能可以一次 `/speckit.implement`；大功能按阶段切，避免撑爆上下文。人类在阶段之间看产物（spec/plan/tasks），不是每个内部工具调用都停。

比较题答案：Spec Kit 的重量是 **opt-in 阶段**，不是 always-on 手册。V4 的 rolling-promotion、architecture registry、decision_log、observation receipt 应对标这些可选质量门，而不是 preflight 的默认必过项。

### 2.5 OpenSpec：默认三条命令；规格只覆盖即将改的切片

[OpenSpec](https://github.com/Fission-AI/OpenSpec) 官方哲学：fluid not rigid、easy not complex、brownfield-first。默认 core profile：

```text
/opsx:explore（可选）→ /opsx:propose → /opsx:apply → /opsx:archive
```

[Using OpenSpec in an Existing Project](https://github.com/Fission-AI/OpenSpec/blob/main/docs/existing-projects.md) 写明：**不必先为整个代码库写规格；只为即将改的切片写 delta。** 人类在 propose 之后、apply 之前读计划。Team workflow 把确定性审查放到 GitHub PR（外环），而不是在内环再造一套状态机。Expanded profile（`/opsx:ff`、verify、onboard）是可选。

比较题答案：OpenSpec 把 always-on 压到「当前这一次变更的文件夹」；流程命令是 chat 里的 skill，不是 53 个必须先记住的 CLI。V4 的 task record 是「系统全集合同」，即使改一行测试也要带齐 delivery_contract / architecture / observation。

### 2.6 对照总表

| 问题 | Codex / Claude 官方 | Spec Kit / OpenSpec | DORA | V4.1.0 实际 |
| --- | --- | --- | --- | --- |
| Always-on 该有多长？ | 短 AGENTS / ≤200 行 CLAUDE.md | 几乎不把 SDLC 手册放进每会话 | 不把交接固定成本做成每批次必付 | ~660 行必读 + 53 CLI 入口 |
| 流程说明书放哪？ | Skill 渐进披露；references 按需 | `/speckit.*` / `/opsx:*` 按阶段调用 | 内环快、外环在合并/发布 | 协议要求未进入阶段也读完全文 |
| 什么必须机械执行？ | Hooks、linter、CI、schema | 阶段产物存在性；PR 审查在 Git | 自动化测试与小批次 CI | schema + CAS + closeout（应保留） |
| 小改怎么走？ | 跳过 plan，直接做 | specify 后可一次 implement；analyze 可选 | 小时级可发布切片 | 与高风险任务同一主链，只免 retrospective |
| 人类停在哪？ | 计划批准、合并、高风险 | 阶段产物之间 | 假设与发布，而非每个内部步骤 | Brief 指纹、授权、checkpoint、bootstrap、closeout 多处，且 CLI 默认 dry-run |

## 3. V5 怎么减默认路径（仍 fail-closed）

目标一句话：**small / no-trigger 从「授权」到「第一个可观察结果」不再经过完整外环说明书和完整 27 字段合同；外环安全能力仍在，只是按需加载、按风险打开。**

不在本规划实现。下面是产品线约束，不是 schema 草稿。

### 3.1（a）small / no-trigger 默认路径要拿掉什么

判定（沿用 V4 已有函数，不新发明档位）：`v4_effective_risk_tier == small` 且无 risk flag。不满足则走今天的完整链。

从默认路径拿掉（改为按需 / 派生 / 事后）：

1. **Always-on 阅读清单缩到薄入口。** 根 `AGENTS.md` 只保留：发现入口、禁止自动 push/merge、下一动作看 STATUS、完整协议按 skill reference 加载。不再要求每个 Developer 会话先读完 `WORKFLOW.md` 389 行和 16 条 lane 不变量。这些在即将执行 `claim` / `record-developer` / closeout 时由对应 skill 引用打开。
2. **目标版本 Brief 2A 不再是 small 任务的前置。** incident / maintenance / user_directive 已允许窄合同；small / no-trigger 同样允许：绑定已有 approved fingerprint（若存在），或写 5 行以内的 task-local 范围/非目标/验收，而不是新 Brief + snapshot + gate + 双基线写入。
3. **不要为 small 任务手工填 27 个必填字段。** Coordinator 从短卡片生成 record：scope / allowed paths / 一条验收 / small+no-trigger。`delivery_contract.checkpoint.mode` 默认 `not_required`（V4 已允许，须写理由和来源）。observation recipe、architecture guardrails、decision_log 在无触发时为空或派生，而不是模板里的完整对象。
4. **`start` + `manual` + `status` + `preflight` + `claim` dry-run/`--apply` 不再是五次独立仪式。** 默认路径收敛为一条「下一安全动作」入口（skill 调用一条 check，由脚本决定需要哪些只读检查）。`claim` 在 `parallel.mode=single` 且无资源冲突时对 small 可隐式占用当前 worktree，不必先教 Git worktree。
5. **产品 checkpoint JSON 不在 small 默认链上。** 人类仍应能看见结果，但用 STATUS 派生的入口/命令，而不是 `request-decision` + observation receipt 才能开始写代码。required checkpoint 留给 core_slice / 新用户行为 / 高不确定性。
6. **Evidence Contract 全文示例不进 always-on。** small 仍要干净 delivery commit 和 sealed snapshot（见 3.2）；Developer evidence 可用「命令 + 路径」短表，由脚本生成 fingerprint，而不是让实现者先读完 WORKFLOW.md §5.1。
7. **closeout 说明书不进实现会话。** Integrator skill / closeout reference 在 `mark-verified` 之后才加载。实现者的「完成」停在 verified，而不是两阶段 closeout 细节。

验收直觉（规划级，非本仓库实现）：一条无风险的 small 修复，人类和 AI 在第一份可运行 diff 之前不应被要求阅读超过约 200 行工作流文本，也不应被要求发出超过个位数的工作流命令。

### 3.2（b）外环哪些继续 fail-closed（即使 small）

这些是 V4 已验证的安全底线，V5 默认路径可以**不讲解**，但不能**关闭**：

| 保留 | 入口（现名） | 为什么不能下主链 |
| --- | --- | --- |
| 写入隔离：一 worktree 一写入者；冲突路径/资源拒绝 | `workflow_lane.py claim` | 并行损坏无法靠 Review 挽回 |
| 干净 delivery + sealed snapshot | `record-developer` / `snapshot` | 审查对象必须稳定 |
| 独立 Reviewer，不能用 GitHub review 替代 | `record-review`；EQ-001 | `功能.md` 明确不做 |
| 默认 dry-run，`--apply` 才写状态 | 全部 state/lane 写命令 | 合作式控制面，防误写 |
| strict-ff closeout；squash / merge-commit / merge-queue 失败 | `prepare-remote-closeout` | EQ-006；本规划不改 merge 策略 |
| 不自动 push / 开 PR / 合并 / force / 删除 branch / 发布 | Skills 与 `功能.md` §10 | 人类外环所有权 |
| generation CAS；手改 JSON 不能绕过 schema | `workflow_state.py` / 8 份 schema | fail-closed 结构门禁 |
| 高风险不被 core_slice 或 small 降低 | `v4_effective_risk_tier` | 已有行为，V5 保持 |

Independent Reviewer 对 small 仍然存在，但 **Reviewer skill 按需加载**，且 small 的审查范围绑定 canonical changed paths，而不是先读完整 WORKFLOW。不新增第二套 checkpoint 命令，不把 GitHub protection 当成 Reviewer。

### 3.3 三层分工（V5 指导架构）

直接套用 Codex/Claude 的分层，不再自创第四套状态机：

```text
Always-on（≤200 行）
  根 AGENTS.md：发现、禁止自动 merge、看 STATUS、按风险打开哪条 skill

On-demand skills（渐进披露）
  $orchestrate-lite      small / no-trigger 默认
  $orchestrate-project-task  完整 V4 等价链（medium/high 或 core_slice）
  $implement-project-task / $review-project-change
  references/ 按阶段：requirements, claim, evidence, checkpoint, closeout

Fail-closed（模型不必记住）
  schema + CAS + lane lock + closeout 脚本
  Stop Hook 只提示下一动作
  项目 linter/CI 继续做格式与测试
```

Coordinator 入口 skill 只做路由：根据 `planning.level` + risk flags 选择 lite 或 full，然后读对应 reference。禁止把 22 个 lane 命令写进 always-on。

### 3.4（c）产品线与兼容

- V5 另开分支/包名（例如 `codex-workflow-v5`），**不往 `v4` 合功能**。`v4` / `v4.*` 只接受 V4 缺陷修复。
- 新包必须仍能发现 `codex-workflow-v4` runtime（`README.md` / `CHANGELOG.md` 已写死；`verify_package.py` 检查这段承诺）。
- 不得改写 `v4` 历史、移动已发布 tag、把未完成 V4 任务强制改写成 V5。已有 V3/V4 record 按当时算法收尾。
- 本规划文件放在 `docs/v5/`，不进入 `payload/`，因此不会被 `install.py` 装进目标项目。

### 3.5 明确不做（避免范围滑到 issue 2–9）

- 不实现 observation runner（issue 2）。
- 不引入逐轮 `review_authorization`（issue 3）。
- 不解决中断后的推理恢复（issue 6）。
- 不做 merge-queue 产品集成、发布编排、自动 push。
- 不把 provider receipt 升级为 Reviewer 等价。

### 3.6 建议的落地顺序（仍不在本目标实现）

1. 量一条真实 small 任务在 V4 下的必读行数和 CLI 次数（本文件 §1 已给出静态清单，作为基线）。
2. 把根 AGENTS 真正做成薄入口；把 WORKFLOW/协议拆进 skill references（文档/包布局，行为门禁不变）。
3. 为 small/no-trigger 增加 lite 授权路径：派生 record、checkpoint=not_required、single-mode 隐式 lane。
4. 保持 Reviewer + sealed snapshot + strict-ff 为 fail-closed。
5. 用现有 `tests/test_v4_phase_b.py` 的 retrospective 测试风格，为「small 不再要求完整 Brief/observation」补回归——那是 V5 实现阶段的事。

## 4. 完成定义（对本规划）

本文件在仓库中，且同时满足：

1. 用本仓库真实路径/命令描述了 always-on 重量和 small 仍付的成本，并引用 `docs/history/problem.md` §9。
2. 研究节含现行官方 URL，并按 always-on / on-demand / fail-closed 回答比较题。
3. 方案节同时包含：（a）small/no-trigger 默认路径削减；（b）lane 隔离、sealed snapshot、独立 Reviewer、strict-ff closeout、禁止自动 push/merge 仍 fail-closed；（c）V5 新线且继续发现 `codex-workflow-v4` runtime。
4. 不修改 `payload/`、`tests/`、`install.py`、`verify_package.py`、`功能.md`、`README.md`、`CHANGELOG.md`。
