# Codex Workflow V4 Phase A 执行计划

> 本文件是 Phase A 唯一规划真相源，保存范围、任务、依赖、验收和变更规则。任务授权后，实时生命周期状态只从对应 task record 读取，本文件不镜像 lane、Review、gate、integration 或 closeout 状态。

## 1. 控制块

| 字段 | 值 |
| --- | --- |
| Plan ID | `V4-PHASEA` |
| Revision | `1` |
| Normative fingerprint | `4a4a0eddb63c8d7a7c593235d2cd9e66c0fe8f0f97414e4014168bf47b7acbd8` |
| Fingerprint material | `v1`：从 `## 2. 阶段目标与不变量` 行开始，到 `## 11. 待人类审阅的精确内容` 行之前的精确 UTF-8 字节，使用 SHA-256 |
| 计划状态 | `phase_a_passed` |
| 实施授权 | `phase_a_authorized_complete` / 用户于 2026-08-29 授权完成 Phase A（PA-010–012），中间自行审查和修复；用户于 2026-09-02 授权收口 Phase A，并只允许开始 Phase B 计划准备 |
| 目标分支 | `v4` |
| Phase 0 基线 | `69c52ad09576a181f18dbe8b8735a8a638a3d27c` / `phase0_passed` |
| 设计输入 | `改进建议.md` 第 14–19 节、第 23–24 节 |
| 当前允许动作 | 封存 Phase A；[V4 Phase B 执行计划](V4_PHASEB_PLAN.md) revision 2 已批准，等待实施授权 |
| 当前禁止动作 | 实施 Phase B/C；新增 decision kind、主状态机或第二套 checkpoint 命令；回改第 2–10 节冻结语义 |

规范指纹校验命令：

```bash
awk '/^## 2\. 阶段目标与不变量$/ {capture=1} /^## 11\. 待人类审阅的精确内容$/ {capture=0} capture {print}' V4_PHASEA_PLAN.md | shasum -a 256
```

状态转换只允许：

```text
awaiting_human_review
  -> approved_not_started
  -> milestone_authorized
  -> executing
  -> phase_a_passed
```

任何验收语义、范围或依赖变化都必须先走第 8 节的变更流程。

## 2. 阶段目标与不变量

Phase A 只固化经过 Phase 0 试运行的最小反馈门禁。实现必须同时保持四个不变量：

1. 每个 V4 task 绑定唯一当前 focus core slice，supporting 工作明确服务该焦点。
2. 合同、决定、观察、Review 和 closeout 都绑定精确 fingerprint/snapshot，旧证据不覆盖已变更语义。
3. 所有入口对相同规则 fail closed，无法通过跳过前置命令绕过决定或 product checkpoint。
4. V2/V3 历史任务保持原语义，V4 新字段和 fingerprint 不重算、不改写历史记录。

Phase A 的人类结果：使用者从 STATUS 或决策卡片看到当前核心结果、真实/临时部分、实质变化、观察入口和下一个需要的人类动作，主路径不要求读取 JSON、lane、generation 或 queue 细节。

## 3. 冻结范围

### 3.1 Phase A 范围内

1. `task-record-v4` Schema，只增加 `delivery_contract` 和 `decision_log` 两个核心对象及其嵌套结构。
2. `focus_slice_id`、`supports_task_id`、最小项目架构基线引用和切片护栏。
3. `request-decision` 和 `record-decision` 两个 state 子命令。
4. `contract_fingerprint`、`decision_fingerprint`、snapshot-bound observation receipt 和 `observation_fingerprint`。
5. `product_checkpoint`、`product_decision`、`architecture_decision` 和 `risk_acceptance` 四种 discriminated decision kind。
6. `required`、`show_before_dependency` 和 `not_required` 三种 checkpoint mode。
7. 统一 V4 reset helper、严格确认延续和全入口防绕过门禁。
8. STATUS、Stop Hook、Coordinator/Developer/Reviewer Skill 的人类可读交互。
9. V3/V4 双读、版本化 closeout fingerprint、安装/升级/回滚与恢复回归。
10. 一个真实 V4 core slice 完成授权、实现、观察、人类决定、Review、gate、integration 和 closeout。

### 3.2 明确延后

| 延后项 | 目标阶段 | Phase A 处理方式 |
| --- | --- | --- |
| Backlog 新列、core/supporting/focus 版本化 metadata 和 WIP 约束 | Phase B | focus 关系只存在 task contract |
| 滚动 Requirements 与切片级 impact/fingerprint | Phase B | 继续使用现有 Requirements baseline/impact |
| 完整 project guardrail registry、fitness framework 和独立 impact 分类 | Phase B | 只引用最小架构基线和内联切片护栏 |
| planning/risk/retrospective 的风险比例门禁 | Phase B | 保留现有字段，只验证结构与引用一致性 |
| 高级 decision supersede、跨 task 幂等和更细作用域失效 | Phase B | 只支持同 fingerprint 安全重试，冲突答复 fail closed |
| pending/queued 后原地 dequeue/reopen | Phase B | 只允许显式 abandon 和现有 release 清理 |
| provider receipt、保护分支和 remote merge queue 治理 | Phase C | 继续复用 V3 strict-ff/remote 机制 |
| 发布批准与部署编排 | Phase C 或独立计划 | integration/closeout 不写成 released |

### 3.3 口径冲突的冻结结论

`planning/risk/retrospective` 在 Phase A 只做 Schema 结构和引用一致性检查，不改变流程成本、不新增风险比例门禁。这一结论解决《改进建议》中“Phase A checker 读取 planning/risk”与“风险比例门禁延后”之间的范围歧义。

## 4. 难度标尺

| 等级 | 判定标准 |
| --- | --- |
| 中 | 主要影响一个消费面，状态语义已由上游任务冻结 |
| 高 | 涉及 Schema/canonicalization 或多模块契约，需要大量负例 |
| 极高 | 涉及原子状态转换、并发 CAS、证据失效、迁移回滚或跨版本恢复 |

整体 Phase A 难度：`XL`。当前 `workflow_state.py`、`workflow_check.py` 和 `workflow_common.py` 合计约 4,700 行，变更会穿过状态机、checker、installer、Hook 和恢复回归。

## 5. 里程碑与任务总表

| ID | 里程碑 | 任务结果 | 依赖 | 难度 | 规划处置 |
| --- | --- | --- | --- | --- | --- |
| PA-001 | M1 合同基础 | 冻结 Phase A 最小语义和项目架构基线契约 | Phase 0 passed | 高 | `awaiting_human_approval` |
| PA-002 | M1 合同基础 | 新增严格 Task Record V4 Schema 与模板 | PA-001 | 高 | `planned` |
| PA-003 | M1 合同基础 | 实现 canonical contract/decision/observation fingerprint | PA-002 | 高 | `planned` |
| PA-004 | M2 决定与观察 | 实现 request/record decision 事务 | PA-003 | 极高 | `planned` |
| PA-005 | M2 决定与观察 | 实现 observation receipt 和 product checkpoint | PA-004 | 极高 | `planned` |
| PA-006 | M2 决定与观察 | 实现统一 reset 和严格确认延续 | PA-005 | 极高 | `planned` |
| PA-007 | M2 决定与观察 | 将 V4 规则放入全部下游门禁 | PA-006 | 极高 | `planned` |
| PA-008 | M3 人类与兼容面 | 生成产品优先 STATUS 和 Stop Hook 下一动作 | PA-007 | 中 | `planned` |
| PA-009 | M3 人类与兼容面 | 实现 V3/V4 双读与版本化 closeout | PA-007 | 极高 | `planned` |
| PA-010 | M3 人类与兼容面 | 实现安装、升级、backup/journal/rollback | PA-009 | 极高 | `planned` |
| PA-011 | M3 人类与兼容面 | 同步 Skills、模板和产品文档 | PA-008, PA-010 | 中 | `planned` |
| PA-012 | M4 真实闭环 | 完成全矩阵回归和一个真实 V4 core slice closeout | PA-011 | 极高 | `planned` |

默认执行顺序：

```text
PA-001 -> PA-002 -> PA-003
       -> PA-004 -> PA-005 -> PA-006 -> PA-007
       -> PA-008 -> PA-009 -> PA-010 -> PA-011
       -> PA-012
```

Phase A 初始只允许一个任务处于 `in_progress`。任务并行需要先证明写入面、契约和验收完全独立，并通过计划 revision 获得人类批准。

### 5.1 单任务阶段内容

PA-002 至 PA-012 每个任务都使用同一阶段合同。PA-001 是人类计划冻结任务，以批准 exact Plan ID/revision/normative fingerprint 作为终态，不建立 Developer lane。

| 任务阶段 | 必须写入的内容 | 退出条件 |
| --- | --- | --- |
| Plan / authorization | Plan ID、revision、normative fingerprint、approved plan commit、依赖 closeout refs、Requirements baseline、目标结果、scope in/out、allowed paths、acceptance IDs、难度与恢复边界 | 人类明确授权当前任务或所在里程碑 |
| Coordinator | 创建 exact task record/delivery contract；验证前置依赖、基线 commit、架构引用、护栏、证据 scope 和负例矩阵；claim lane/resources | preflight 零警告，且任务合同与本计划无差异 |
| Developer | 只修改 allowed paths；先完成最小端到端结果；记录精确命令、平台、表面、负例、剩余风险和 Evidence Contract v1 claims | exact delivery snapshot 形成，focused/full 验证与绕过负例通过 |
| Independent Reviewer | 从 snapshot 重建范围与合同；核对每个 claim/scope/command/fingerprint；给出 `confirmed/narrowed/rejected/unverified`；检查 V3 回归和跨模块失效 | 所有 claim `confirmed`，无未处理 P0–P3 finding，Review 绑定当前 snapshot |
| Gate / integration | 重放 task-specific gate、通用 gate、集成前绕过检查、target ancestry/queue/claim 校验 | 只有当前 Review、contract、decision/checkpoint 和 integration candidate 全部匹配时才集成 |
| Closeout | 封存 task record、Developer/Review/gate/integration evidence、closeout fingerprint、bundle/manifest 和全部引用哈希 | task record 终态、runtime claim/queue/lane 按策略释放；本计划只追加 sealed task/closeout ref，不镜像实时状态 |

任务授权后发现计划外 allowed path、acceptance 或依赖时，必须停在当前阶段，不得用 Developer/Reviewer 备注替代第 8.3 节的计划变更流程。

## 6. 任务卡片

### PA-001：冻结最小语义和架构基线契约

- 目标：将 Phase 0 结论收窄为可实现的 V4 契约，关闭 Schema 开发前的语义歧义。
- 交付：批准本计划的精确 Plan ID/revision/normative fingerprint；冻结 focus、decision kind、checkpoint mode、失效矩阵、架构基线引用形状和 Phase B/C 延后表。
- 验收：每个术语有唯一定义；planning/risk 只做结构校验；无隐式 Backlog 新列或远程治理扩展。
- 主要文件：`V4_PHASEA_PLAN.md`。`DECISIONS.md` 模板的实际修改归入 PA-002/PA-011。
- 完成门：人类批准精确 Plan ID、revision 和 normative fingerprint；批准状态回写不改变 fingerprint material。

### PA-002：Task Record V4 Schema 与模板

- 目标：定义严格、可版本化、可 fail-closed 解析的 V4 record。
- 交付：`task-record-v4.schema.json`；core/supporting 模板；`delivery_contract` 与 discriminated `decision_log` 示例。
- 验收：缺 focus、Requirements/acceptance refs、observation recipe、checkpoint policy、架构基线或 fingerprint 时拒绝；decision kind 不能共用含糊字段；未知字段 fail closed。
- 主要文件：`payload/.codex-workflow/schemas/task-record-v4.schema.json`、task record template、Schema tests。
- 完成门：正负 fixture 全部通过，V3 Schema 字节不变。

### PA-003：Canonical fingerprint 与失效矩阵

- 目标：为合同、决定和观察建立稳定的精确身份。
- 交付：canonicalization helper；`contract_fingerprint`、`decision_fingerprint`、`observation_fingerprint` 版本材料；变更到失效结果的机械矩阵。
- 验收：字段顺序不影响结果；Requirements、scope、acceptance、recipe、fixture、API/Schema、依赖或护栏实质变化会改变相应 fingerprint；Python 3.9/3.12/3.13 结果一致。
- 主要文件：`workflow_common.py`、`workflow_state.py`、fingerprint tests。
- 完成门：每类实质/非实质变更都有对应负例和稳定性测试。

### PA-004：Decision 命令与并发事务

- 目标：在任务中可结构化请求、记录和阻断实质决定。
- 交付：`request-decision` / `record-decision`；默认 dry-run；`--apply`；generation CAS；外部 source/receipt；同 fingerprint 重试安全。
- 验收：两个并发答复只有一个成功；冲突答复拒绝覆盖；blocking 由 kind/scope/phase 推导；自由文本不能放宽门禁；`integration.status != not_ready` 时拒绝新请求。
- 主要文件：`workflow_state.py`、`workflow_common.py`、`tests/test_workflow_state.py`、并发/故障注入测试。
- 完成门：dry-run 零写入；CAS 失败无半状态；decision 选项不自动改写 task contract。

### PA-005：Observation receipt 与 product checkpoint

- 目标：将人类看到的实际结果绑定 exact snapshot 和当前 contract。
- 交付：observation recipe/receipt；UI/API/CLI/data 证据形状；脱敏规则；三种 checkpoint mode；accepted/changes_requested/stopped/deferred 处理。
- 验收：receipt 绑定 snapshot、contract、Requirements、artifact/environment/fixture；过期入口、错误 artifact、缺失 fixture 或敏感证据拒绝；`deferred` 保持 open。
- 主要文件：V4 Schema、`workflow_state.py`、`workflow_check.py`、observation fixtures。
- 完成门：新 delivery 不能直接复用旧 receipt；人类答复精确绑定 `decision_fingerprint`。

### PA-006：统一 reset 与严格确认延续

- 目标：使纠偏、Requirements 影响、refresh-base 和 Reviewer 新 snapshot 使用同一失效语义。
- 交付：版本化 V4 reset helper；decision history 保留；snapshot-bound 证据清理；确认延续分类；review + continuation 同事务写入。
- 验收：`changes_requested` 只在 integration not_ready 时执行；产品语义变化使旧方向确认失效；只有严格技术等价、观察重放一致且 Reviewer 确认时才延续。
- 主要文件：`workflow_state.py`、`workflow_common.py`、requirements-impact/refresh-base/review tests。
- 完成门：故障注入不留只有 Review 或只有 continuation 的半状态；原人类决定身份和时间不被改写。

### PA-007：全入口防绕过门禁

- 目标：让 V4 契约、决定、checkpoint 和架构护栏在所有下游入口一致生效。
- 交付：preflight、record-developer、record-review、complete-task、gate、prepare-integration 的统一 checker；checkpoint readiness；pending/queued fail-closed；显式 abandon 清理验证。
- 验收：open blocking decision 冻结任务；required checkpoint 缺当前 receipt/方向确认时拒绝 Review；show-before-dependency 在确认前拒绝依赖与集成；原型不能 prepare-integration。
- 主要文件：`workflow_check.py`、`workflow_state.py`、`workflow_lane.py` 的现有放弃边界、gate/end-to-end/recovery tests。
- 完成门：每个门禁都有直达命令的绕过负例；清理 queue/claim 不会伪造 done。

### PA-008：产品优先 STATUS 与 Stop Hook

- 目标：让人类直接看到产品结果和下一个安全动作。
- 交付：STATUS 派生摘要；决策卡片；Stop Hook 的精确 decision ID/下一命令；技术细节折叠区。
- 验收：先显示核心结果、focus、入口、真实/临时部分、实质变化和 blocking decisions；Stop Hook 不批准、不改状态、不集成。
- 主要文件：`workflow_common.py`、`codex_stop_hook.py`、STATUS tests。
- 完成门：UI/API/CLI/data 四类 fixture 都生成单一可执行下一动作，不泄露 token/cookie/PII/签名 URL。

### PA-009：V3/V4 双读与版本化 closeout

- 目标：引入 V4 新语义后保持历史任务、integration 和恢复契约。
- 交付：record 版本分派；V3 原算法；V4 `closeout_fingerprint_version`；旧工具遇到 V4 fail closed；rebuild/recover/reconcile/refresh-base 兼容。
- 验收：V2 只读历史兼容；V3 task 继续 closeout；V4 fingerprint 能检测 contract 或有效 decision 漂移；pending closeout 不被新算法重算。
- 主要文件：`workflow_common.py`、`workflow_state.py`、parallel-closeout/fault-recovery/end-to-end tests。
- 完成门：现有 V3 全量测试全部通过，新增 V4 漂移与恢复负例通过。

### PA-010：安装、升级和回滚事务

- 目标：让包文件和项目状态在升级时可观察、可回滚、不猜测人类决定。
- 交付：V4 package manifest/layout；`--plan-upgrade` 零写入报告；显式 apply；package-owned/project-owned 窄范围 backup/journal/rollback。
- 验收：plan 列出 V3 records、live lanes、queue、pending closeout、治理定制、架构基线与观察入口；不生成或批准项目架构基线、不猜 focus。
- 主要文件：`install.py`、`verify_package.py`、layout/manifest，安装、升级、staged rollback tests。
- 完成门：中途故障恢复原文件和 runtime 状态；Windows 路径使用当前 `sys.executable`。

### PA-011：Skills、模板与文档同步

- 目标：让 Coordinator、Developer、Reviewer 和人类对同一 V4 契约使用同一语义。
- 交付：task template；3 个 Skill；review checklist；WORKFLOW/README/功能文档；项目观察入口约定。
- 验收：Coordinator 先确认 focus 和决策卡片；Developer 遇到 trigger 创建 decision request；Reviewer 核对 checkpoint/延续/护栏；`workflow_lane.py` 不承载产品语义。
- 主要文件：`payload/.agents/skills/**`、`payload/.codex-workflow/docs/WORKFLOW.md`、`README.md`、`功能.md`。
- 完成门：文档示例通过 Schema/checker fixture；不新增另一个 checkpoint 命令或状态库。

### PA-012：全矩阵回归与真实 V4 闭环

- 目标：用机械负例和真实 core slice 证明 Phase A 的人类结果、门禁和兼容性同时成立。
- 交付：Schema/preflight、decision CAS、checkpoint、确认延续、架构护栏、安装迁移、故障恢复矩阵；一个真实 V4 task 全生命周期证据。
- 验收：人类不读 JSON 即可进入观察和做决定；product direction、verified、integrated/done、released 分开表达；排队后新决定 fail closed 且无僵尸 queue。
- 验证矩阵：Python 3.9/3.12/3.13 全量回归；可用环境中的 Windows/跨平台 fixture 或 CI；缺少的真实平台必须明确记录，不写入已验证范围。
- 完成门：独立 Review PASS；gate PASS；integration/closeout 完成；证据持久化并校验全部引用哈希。

## 7. 里程碑门禁

| 门 | 允许进入条件 | 允许退出条件 |
| --- | --- | --- |
| G-A0 计划冻结 | Phase 0 `phase0_passed` | 人类批准本计划精确 Plan ID/revision/normative fingerprint；批准状态回写后的 Git commit 作为实施基线，实施授权仍可保持关闭 |
| G-A1 合同基础 | G-A0 通过，PA-002 获授权 | PA-002/003 独立 Review PASS；V4 Schema/fingerprint 冻结；V3 回归 PASS |
| G-A2 决定与观察 | G-A1 通过 | PA-004–007 完成；CAS/失效/绕过/故障注入矩阵 PASS |
| G-A3 人类与兼容面 | G-A2 通过 | PA-008–011 完成；V3/V4 双读、closeout、upgrade/rollback 回归 PASS |
| G-A4 真实闭环 | G-A3 通过，真实 core slice 另行获人类授权 | PA-012 独立 Review、gate、integration、closeout 和证据持久化全部完成 |

不允许使用后续里程碑的临时实现反向放宽当前门禁。受阻时局部修正当前契约，范围扩展需要计划 revision。

## 8. 防漂移规则

### 8.1 规范优先级

```text
人类批准的 Requirements baseline
  > 人类批准的 V4-PHASEA exact Plan ID/revision/normative fingerprint
  > 当前已授权 task record / delivery contract
  > Developer 实现与临时工作记录
```

`改进建议.md` 是设计输入和历史理由，Phase A 实施范围以人类批准后的本计划为准。

### 8.2 单一状态源

1. 本文件只管计划基线，不同步任务的实时 phase/status/generation。
2. 任务授权后，task record 管理 Developer、Review、gate、integration 和 closeout 真相。
3. STATUS 和 Stop Hook 只派生展示，不写入决定或改变生命周期。
4. `payload/.codex-workflow/governance/PLAN.md` 和安装模板不代表本仓库 Phase A 进度。

### 8.3 计划变更流程

任何人提出新字段、新命令、新状态、新集成机制、任务合并/拆分或验收放宽时，必须按以下顺序处理：

1. 暂停受影响任务，保留当前 snapshot 和证据。
2. 记录变更理由、影响的 task IDs、范围、依赖、验收、迁移和回滚影响。
3. 判定是否仍在 Phase A 冻结范围内；属于 Phase B/C 的变更默认延后。
4. 向人类展示原计划与提议计划的精确差异。
5. 获得人类批准后增加 revision，保留修订记录并提交 Git。
6. 使所有受影响 contract/decision/receipt/review fingerprint 按矩阵失效，再恢复实施。

自由文本备注、临时测试或 Developer 便利性不能自动扩大范围。

### 8.4 证据约束

1. 所有验收 claim 遵守 Evidence Contract v1：有限 scope、精确 target、claim-command 闭包、fingerprint 和 Reviewer assessment。
2. 每个任务只声明实际观察到的平台、命令和表面，不使用未经证明的全局表述。
3. 任何 Reviewer assessment 为 `narrowed`、`rejected` 或 `unverified` 时，Review 不得 pass。
4. 每个里程碑封存 Developer evidence、Review evidence、gate 结果和引用哈希。

## 9. 文件责任边界

| 路径 | 允许任务 | 边界 |
| --- | --- | --- |
| `payload/.codex-workflow/schemas/task-record-v4.schema.json` | PA-002 | 只保存 V4 record 结构，不复制 runtime 状态 |
| task record template | PA-002, PA-011 | 示例必须通过当前 Schema |
| `payload/.codex-workflow/governance/DECISIONS.md` | PA-002, PA-011 | 只提供项目级最小架构基线形状，不替项目生成或批准真实基线 |
| `workflow_common.py` | PA-003, PA-006, PA-008, PA-009 | canonical helper、失效共享语义、派生摘要和版本分派 |
| `workflow_state.py` | PA-003–007, PA-009 | 决定写入、CAS、reset、review/continuation 原子事务 |
| `workflow_check.py` | PA-005, PA-007 | 只读 readiness 和防绕过 checker |
| `workflow_lane.py` | PA-007, PA-009 | 只处理现有 queue/claim/recovery 兼容，不加产品状态 |
| `codex_stop_hook.py` | PA-008 | 只输出下一安全动作 |
| `install.py`, `verify_package.py`, layout/manifest | PA-010 | package-owned 升级和回滚边界 |
| Skills 与产品文档 | PA-011 | 解释已实现语义，不先行发明新状态 |
| `tests/` | PA-002–012 | 每个正路径必须有绕过、并发或失效负例 |

任务需要修改表外路径时，先执行第 8.3 节的计划变更流程。

## 10. Phase A 完成定义

Phase A 只有在以下结果全部有精确证据时才可标记 `phase_a_passed`：

1. 一个新 V4 core slice 已绑定人类确认的 focus，核心可观察结果先于非必要 supporting 工作出现。
2. 人类可通过 STATUS/决策卡片进入观察，并说明真实部分、临时部分、实质变化和当前决定。
3. 影响当前切片的产品、数据、公共接口、依赖或架构决定在固化前阻断，部分继续需要拆分独立 task。
4. required checkpoint 在独立 Reviewer 前获得当前 receipt 和与 `observation_fingerprint + Requirements` 匹配的人类方向确认。
5. Reviewer 产生新 snapshot 后，产品语义变化重新请求观察；严格技术等价只通过完整延续门禁。
6. 项目架构基线和切片护栏进入 contract fingerprint，由 Coordinator、checker 和 Reviewer 独立核对。
7. product direction、technical verified、integrated/done 和 released 始终分开表达。
8. V3/V4 closeout fingerprint 分版本，现有 V3 integration、故障恢复和历史 task 保持兼容。
9. pending/queued 后的新决定 fail closed，显式 abandon 不留僵尸 queue/claim，不伪造 closeout。
10. 全部 Evidence Contract v1 claim 被 Reviewer `confirmed`，独立 Review、gate、integration、closeout 和证据持久化完成。

## 11. 待人类审阅的精确内容

本轮只请审阅以下五项：

1. 12 个任务的粒度和严格串行顺序。
2. `planning/risk/retrospective` 在 Phase A 只做结构一致性校验。
3. 最小幂等只支持同 fingerprint 重试，高级 supersede 延后。
4. pending/queued 后只支持显式 abandon，原地 reopen 延后。
5. 计划批准与实施授权分开；批准 revision 不自动开始 PA-002。

人类批准后，下一次只将 `计划状态` 更新为 `approved_not_started`，不修改第 2–10 节，因而 normative fingerprint 保持不变。该状态回写提交作为后续 task authorization 引用的 approved plan commit；实施授权继续保持关闭，直到人类明确授权相应里程碑。

## 12. 落账

Phase A 于 2026-09-02 标记 `phase_a_passed`。第 2–10 节未被修改，normative fingerprint 保持 `4a4a0eddb63c8d7a7c593235d2cd9e66c0fe8f0f97414e4014168bf47b7acbd8`。封存证据见 [V4 Phase A 收口证据](V4_PHASEA_CLOSEOUT.md)。下一阶段规划真相源是 [V4 Phase B 执行计划](V4_PHASEB_PLAN.md)；批准该计划不等于实施授权。
