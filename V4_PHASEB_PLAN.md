# Codex Workflow V4 Phase B 执行计划

> 本文件是 Phase B 唯一规划真相源，保存范围、任务、依赖、验收和变更规则。任务授权后，实时生命周期状态只从对应 task record 读取，本文件不镜像 lane、Review、gate、integration 或 closeout 状态。

## 1. 控制块

| 字段 | 值 |
| --- | --- |
| Plan ID | `V4-PHASEB` |
| Revision | `1` |
| Normative fingerprint | `c28f862ce5651e64cd44f6434b1b07c27f413b6829548c141ecd5a574a8ee0c7` |
| Fingerprint material | `v1`：从 `## 2. 阶段目标与不变量` 行开始，到 `## 11. 待人类审阅的精确内容` 行之前的精确 UTF-8 字节，使用 SHA-256 |
| 计划状态 | `approved_not_started` |
| 实施授权 | `implementation_not_authorized` |
| 目标分支 | `v4` |
| Phase A 基线 | `phase_a_passed` / Plan ID `V4-PHASEA` revision `1` / fingerprint `4a4a0eddb63c8d7a7c593235d2cd9e66c0fe8f0f97414e4014168bf47b7acbd8` |
| 设计输入 | `改进建议.md` 第 6.4、7、10、11、12、15.4、16、18–19、23–24 节；`V4_PHASEA_PLAN.md` 第 3.2 节延后表 |
| 当前允许动作 | 记录本批准；等待人类实施授权 |
| 当前禁止动作 | 实施 Phase B/C；开始 PB-002；修改 Phase A 冻结语义；新增 decision kind、主状态机或第二套 checkpoint 命令 |

规范指纹校验命令：

```bash
awk '/^## 2\. 阶段目标与不变量$/ {capture=1} /^## 11\. 待人类审阅的精确内容$/ {capture=0} capture {print}' V4_PHASEB_PLAN.md | shasum -a 256
```

状态转换只允许：

```text
awaiting_human_review
  -> approved_not_started
  -> milestone_authorized
  -> executing
  -> phase_b_passed
```

任何验收语义、范围或依赖变化都必须先走第 8 节的变更流程。计划批准与实施授权分开；批准 revision 不自动开始 PB-002。

## 2. 阶段目标与不变量

Phase B 在 Phase A 已生效的最小反馈门禁之上，让流程成本随风险缩放，并把 focus、Requirements 细化和架构护栏提升为可机械验证的项目级能力。

实现必须同时保持六个不变量：

1. 不重写 V3 lane、lease、remote claim、canonical delivery 和 closeout；V3 任务继续按原算法收尾。
2. 不增加新的全局主状态机，也不增加第二套 checkpoint 命令。
3. 不破坏现有 Backlog 固定列 parser：现有列顺序与索引语义保持不变。
4. 不静默放宽现有 Requirements V1 gate；若稳定核心 + 当前切片仍造成过宽影响，必须停下来设计切片级 fingerprint/impact，而不是把未确认未来细节塞进同一 approved Brief。
5. small/no-trigger 可以降低复盘成本，但不能降低授权、范围、验证、独立 Review、安全和恢复底线。
6. STATUS 继续只派生展示；Backlog metadata 与 task `delivery_contract` 必须互相一致，`workflow_lane.py` 仍不承载产品语义。

Phase B 的人类结果：使用者能从 Backlog/STATUS 看到当前 focus、supporting 关系与在制约束；小风险任务不再承担与高风险任务相同的复盘重量；重复出现的架构护栏有稳定身份和可重放检查；冲突决定不能靠最后写入覆盖。

## 3. 冻结范围

### 3.1 Phase B 范围内

1. Backlog 对 `kind` / `supports_task_id` / `focus_slice_id` 的版本化 metadata，以及与 task contract 的机械一致性。
2. 可配置的机械 WIP：默认每个产品目标只允许一个未确认方向的 core slice 在制。
3. 滚动 Requirements 的操作路径：目标版本稳定核心 + 当前切片细节 + 未来候选隔离，并继续走现有 impact。
4. project-owned guardrail registry、fitness check 证据和独立 impact 分类。
5. `planning` / `risk` / `retrospective` 的风险比例门禁，包括 small/no-trigger 可记录 `not_required`。
6. 决策显式 supersede、同 fingerprint 之外的受控幂等，以及更细作用域失效。
7. pending/queued 后安全原地 dequeue/reopen 的原子事务；证明不了安全性时保持显式 abandon。
8. Skills、模板、WORKFLOW/README/功能文档与安装盘点同步到已实现语义。
9. 全量回归，以及一个能证明风险缩放和 focus/WIP 的真实或受控闭环。

### 3.2 明确延后

| 延后项 | 目标阶段 | Phase B 处理方式 |
| --- | --- | --- |
| provider receipt、保护分支和 remote merge queue 治理 | Phase C | 继续复用 V3 strict-ff/remote 机制 |
| 发布批准与部署编排 | Phase C 或独立计划 | integration/closeout 不写成 released |
| 新的 decision kind 或第二套 checkpoint 命令 | 不纳入 B | 继续使用 Phase A 的四种 kind 与三种 checkpoint mode |
| 批量把活动 V3 record 改写成 V4 | 不纳入 B | V3 继续按原语义收尾 |
| 用 lane/queue 猜测产品优先级 | 不纳入 B | 产品优先级仍由 Coordinator 在领取前处理 |

### 3.3 口径冲突的冻结结论

1. Backlog 人类可读列若要增加 `kind` / `supports` / `focus`，只能追加在现有列之后；parser 继续使用现有索引读取 ID、依赖、状态和阻塞类型。推荐同时使用与 Requirements baseline 同类的版本化 metadata 区块作为机械真相，避免把产品关系写进易漂移的单元格散文。
2. 滚动 Requirements 先把现有 Brief/impact 操作为“稳定核心 + 当前切片”；只有在真实任务证明该模型仍然过宽时，才另开计划 revision 设计切片级 fingerprint。禁止为了避免 impact 而把假设写成 confirmed。
3. WIP 默认值为 1 个未确认方向的 core slice；supporting / hardening / governance 不占用该配额，但必须绑定同一 `focus_slice_id`。该约束可配置，缺省 fail closed。
4. `core_slice` 只增加产品反馈要求，不能降低 sensitive data、destructive change 或 irreversible architecture 的高风险门禁。
5. 不同答复必须显式 supersede，不能最后写入者覆盖；同 fingerprint 安全重试继续保留。
6. dequeue/reopen 必须与 queue、claim、integration 状态和 generation CAS 同一事务。若无法证明不出现半状态或伪造 done，该项保持 abandon-only，不得用临时命令假装 reopen。

## 4. 难度标尺

| 等级 | 判定标准 |
| --- | --- |
| 中 | 主要影响一个消费面，状态语义已由上游任务冻结 |
| 高 | 涉及 Backlog parser、Requirements 操作路径或多模块契约，需要大量负例 |
| 极高 | 涉及原子状态转换、并发 CAS、证据失效、queue 事务或跨版本恢复 |

整体 Phase B 难度：`XL`。变更会穿过 Backlog parser、Requirements impact、checker、decision 事务、queue/claim 和安装盘点。

## 5. 里程碑与任务总表

| ID | 里程碑 | 任务结果 | 依赖 | 难度 | 规划处置 |
| --- | --- | --- | --- | --- | --- |
| PB-001 | M1 计划冻结 | 冻结 Phase B 范围、不变量和延后表 | Phase A `phase_a_passed` | 高 | `awaiting_human_approval` |
| PB-002 | M2 Backlog 与 WIP | 版本化 focus/kind/supports metadata 与机械 WIP | PB-001 | 高 | `planned` |
| PB-003 | M3 滚动细化 | 稳定核心 + 当前切片的 Requirements 操作路径 | PB-002 | 极高 | `planned` |
| PB-004 | M3 滚动细化 | planning/risk/retrospective 风险比例门禁 | PB-003 | 高 | `planned` |
| PB-005 | M4 架构护栏 | guardrail registry、fitness 证据和独立 impact 分类 | PB-002 | 极高 | `planned` |
| PB-006 | M5 决定与失效 | 显式 supersede、受控幂等和更细作用域失效 | PB-004 | 极高 | `planned` |
| PB-007 | M5 决定与失效 | pending/queued 原地 dequeue/reopen 或证明保持 abandon | PB-006 | 极高 | `planned` |
| PB-008 | M6 人类与兼容面 | 同步 Skills、模板、文档和升级盘点 | PB-005, PB-007 | 中 | `planned` |
| PB-009 | M7 证明闭环 | 全量回归与风险缩放/WIP 证明 | PB-008 | 极高 | `planned` |

默认执行顺序：

```text
PB-001 -> PB-002
       -> PB-003 -> PB-004
       -> PB-005
       -> PB-006 -> PB-007
       -> PB-008 -> PB-009
```

PB-003/004 与 PB-005 只有在写入面、契约和验收被证明完全独立后，才能通过计划 revision 申请有限并行。Phase B 初始只允许一个任务处于 `in_progress`。

### 5.1 单任务阶段内容

PB-002 至 PB-009 每个任务都使用同一阶段合同。PB-001 是人类计划冻结任务，以批准 exact Plan ID/revision/normative fingerprint 作为终态，不建立 Developer lane。

| 任务阶段 | 必须写入的内容 | 退出条件 |
| --- | --- | --- |
| Plan / authorization | Plan ID、revision、normative fingerprint、approved plan commit、依赖 closeout refs、Requirements baseline、目标结果、scope in/out、allowed paths、acceptance IDs、难度与恢复边界 | 人类明确授权当前任务或所在里程碑 |
| Coordinator | 创建 exact task record/delivery contract；验证前置依赖、基线 commit、架构引用、护栏、证据 scope 和负例矩阵；claim lane/resources | preflight 零警告，且任务合同与本计划无差异 |
| Developer | 只修改 allowed paths；先完成最小端到端结果；记录精确命令、平台、表面、负例、剩余风险和 Evidence Contract v1 claims | exact delivery snapshot 形成，focused/full 验证与绕过负例通过 |
| Independent Reviewer | 从 snapshot 重建范围与合同；核对每个 claim/scope/command/fingerprint；给出 `confirmed/narrowed/rejected/unverified`；检查 V3/V4 回归和跨模块失效 | 所有 claim `confirmed`，无未处理 P0–P3 finding，Review 绑定当前 snapshot |
| Gate / integration | 重放 task-specific gate、通用 gate、集成前绕过检查、target ancestry/queue/claim 校验 | 只有当前 Review、contract、decision/checkpoint 和 integration candidate 全部匹配时才集成 |
| Closeout | 封存 task record、Developer/Review/gate/integration evidence、closeout fingerprint、bundle/manifest 和全部引用哈希 | task record 终态、runtime claim/queue/lane 按策略释放；本计划只追加 sealed task/closeout ref，不镜像实时状态 |

任务授权后发现计划外 allowed path、acceptance 或依赖时，必须停在当前阶段，不得用 Developer/Reviewer 备注替代第 8.3 节的计划变更流程。

## 6. 任务卡片

### PB-001：冻结 Phase B 范围

- 目标：把 Phase A 延后项收窄为可实现的 Phase B 契约，关闭 Schema 开发前的语义歧义。
- 交付：批准本计划的精确 Plan ID/revision/normative fingerprint；冻结 Backlog metadata 形状、WIP 默认值、Requirements 滚动边界、registry 形状、supersede 规则和 dequeue/reopen 安全阈值。
- 验收：每个术语有唯一定义；无隐式 Phase C 远端治理扩展；无新的主状态机或 checkpoint 命令。
- 主要文件：`V4_PHASEB_PLAN.md`。
- 完成门：人类批准精确 Plan ID、revision 和 normative fingerprint；批准状态回写不改变 fingerprint material。

### PB-002：Backlog 版本化 metadata 与机械 WIP

- 目标：让 focus / kind / supporting 关系成为可机械验证的 Backlog 事实，而不破坏现有列 parser。
- 交付：版本化 metadata 区块或只追加列；STATUS 从 durable metadata 派生；缺省 WIP=1 的未确认方向 core slice 约束。
- 验收：插入或重排现有列失败；supporting 缺 `focus_slice_id` 或 `supports_task_id` 失败；超过 WIP 失败；task contract 与 Backlog metadata 不一致失败。
- 主要文件：Backlog 模板、`workflow_common.py`、`workflow_check.py`、STATUS 派生、相关测试。
- 完成门：现有 9 列索引语义的负例全部通过，V3 Backlog 继续可读。

### PB-003：滚动 Requirements 操作路径

- 目标：用现有 fingerprint/impact 支持“稳定核心 + 当前切片”，把未来候选隔离在合同之外。
- 交付：切片晋升操作、稳定 REQ ID 保留、无活动任务时的默认晋升路径，以及过宽影响的停止条件。
- 验收：未来候选进入同一 approved Brief 的未确认 Must 实体时失败；未确认内容不能进入 Developer scope、公共契约或数据模型；活动任务继续要求精确 `analysis_id` 的 continue 决定。
- 主要文件：Requirements 模板/文档、`workflow_check.py`、`workflow_state.py` impact/continue、相关测试。
- 完成门：不修改 V1 fingerprint 算法；若操作验证证明仍然过宽，停止并提交计划 revision，不静默放宽 gate。

### PB-004：风险比例门禁

- 目标：让 `planning.level`、task `kind` 和 `risk` 真正改变主链成本。
- 交付：effective tier 计算；small/no-trigger 允许 retrospective `not_required`；medium/high-risk 或流程问题强制复盘；high-risk 缺 ExecPlan/decision/恢复计划失败。
- 验收：`core_slice` 不能降低高风险底线；同一 task 命中多行时取最高要求；small 任务仍保留授权、范围、验证和独立 Review。
- 主要文件：V4 Schema、`workflow_check.py` gate、complete-task、相关测试。
- 完成门：每种档位都有正路径和绕过负例。

### PB-005：Guardrail registry 与 fitness

- 目标：把重复出现的护栏提升为带稳定 ID 的项目级 registry，并用独立检查证明 impact。
- 交付：project-owned registry；source/owner/version/fingerprint；`none` / `within_guardrails` / `changes_guardrail` 独立分类；代表性 fitness fixture。
- 验收：缺基线或 registry fingerprint 漂移时不能授权新的 V4 core slice；`within_guardrails` 缺 check 证据失败；`changes_guardrail` 没有 accepted architecture decision 失败；不能只信任 Developer 自报 `declared_impact`。
- 主要文件：治理模板、`workflow_check.py`、`workflow_common.py`、Reviewer checklist、相关测试。
- 完成门：dependency、contract、schema compatibility、migration/rollback 至少各有代表性 fixture。

### PB-006：Decision supersede 与更细失效

- 目标：让冲突答复和部分范围变化不再依赖“最后写入者”或整单全量失效。
- 交付：显式 supersede；跨 task 幂等边界；按 decision scope / Requirements / observation / review / integration 五类证据分别失效。
- 验收：不同答复缺 supersede 时失败；同 fingerprint 重试仍然安全；无关 commit SHA 不使人类产品/架构选择失效；Requirements 影响当前切片时相关决定失效。
- 主要文件：`workflow_state.py`、`workflow_common.py`、decision/checkpoint 测试。
- 完成门：并发冲突、故障注入和跨作用域负例全部 fail closed。

### PB-007：pending/queued 原地 dequeue/reopen

- 目标：在排队后发现关键问题时，提供比重建新 task 更安全的原地恢复，同时不伪造 done。
- 交付：与 queue/claim/generation CAS 同一事务的 dequeue/reopen；或书面证明无法安全实现并保持 abandon-only。
- 验收：半状态、OID 漂移、target 已含 closeout、或 integration 已非 not_ready 时零写入失败；成功路径删除匹配 runtime queue/claim 且不把任务标为 done。
- 主要文件：`workflow_state.py`、`workflow_lane.py`、queue/recovery 测试。
- 完成门：要么原子事务通过故障注入，要么本任务只交付 abandon-only 冻结结论，不留下临时命令。

### PB-008：Skills、模板与文档同步

- 目标：让 Coordinator、Developer、Reviewer 和人类对同一 Phase B 契约使用同一语义。
- 交付：Backlog/Requirements/DECISIONS 模板；3 个 Skill；WORKFLOW/README/功能文档；升级盘点列出 registry/WIP/滚动 Requirements 状态且不猜测 focus。
- 验收：文档示例通过 Schema/checker fixture；`workflow_lane.py` 仍不承载产品语义；不新增另一个 checkpoint 命令或状态库。
- 主要文件：Skills、模板、`README.md`、`功能.md`、`install.py`、`verify_package.py`。
- 完成门：人类主路径不要求阅读 JSON、lane、generation 或 queue 细节。

### PB-009：回归与风险缩放证明

- 目标：证明 Phase B 降低了小风险流程成本，同时没有放宽 Phase A 产品门禁或 V3 外环。
- 交付：Schema/preflight、WIP、rolling Requirements、risk tier、registry/fitness、supersede、dequeue/reopen 或 abandon-only 矩阵；一个真实或受控任务证明 small 路径与 core slice 路径的成本差异。
- 验收：V3 全量回归通过；V4 Phase A 负例保持；缺失平台明确记录，不写入已验证范围。
- 验证矩阵：当前已验证的 macOS / CPython 3.9 必须通过；Python 3.12/3.13 与 Windows 若未跑，必须记录为未验证。
- 完成门：独立 Review PASS；gate PASS；integration/closeout 完成；证据持久化并校验全部引用哈希。

## 7. 里程碑门禁

| 门 | 允许进入条件 | 允许退出条件 |
| --- | --- | --- |
| G-B0 计划冻结 | Phase A `phase_a_passed` | 人类批准本计划精确 Plan ID/revision/normative fingerprint；实施授权仍可保持关闭 |
| G-B1 Backlog/WIP | G-B0 通过，PB-002 获授权 | PB-002 独立 Review PASS；固定列 parser 不变；WIP 负例 PASS |
| G-B2 滚动细化 | G-B1 通过 | PB-003/004 完成；V1 gate 未被静默放宽；风险档位负例 PASS |
| G-B3 架构护栏 | G-B1 通过 | PB-005 完成；registry/fitness/impact 分类负例 PASS |
| G-B4 决定与队列 | G-B2 通过 | PB-006/007 完成；supersede 与 dequeue/reopen 或 abandon-only 冻结结论有证据 |
| G-B5 证明闭环 | G-B3 与 G-B4 通过 | PB-008/009 完成；独立 Review、gate、integration、closeout 和证据持久化全部完成 |

不允许使用后续里程碑的临时实现反向放宽当前门禁。受阻时局部修正当前契约，范围扩展需要计划 revision。

## 8. 防漂移规则

### 8.1 规范优先级

```text
人类批准的 Requirements baseline
  > 人类批准的 V4-PHASEB exact Plan ID/revision/normative fingerprint
  > 当前已授权 task record / delivery contract
  > Developer 实现与临时工作记录
```

`改进建议.md` 是设计输入和历史理由，Phase B 实施范围以人类批准后的本计划为准。Phase A 第 2–10 节冻结语义对本阶段仍然有效，除非本计划明确取代其中被延后的条款。

### 8.2 单一状态源

1. 本文件只管计划基线，不同步任务的实时 phase/status/generation。
2. 任务授权后，task record 管理 Developer、Review、gate、integration 和 closeout 真相。
3. STATUS 和 Stop Hook 只派生展示，不写入决定或改变生命周期。
4. 安装模板不代表本仓库 Phase B 进度。

### 8.3 计划变更流程

任何人提出新字段、新命令、新状态、新集成机制、任务合并/拆分或验收放宽时，必须按以下顺序处理：

1. 暂停受影响任务，保留当前 snapshot 和证据。
2. 记录变更理由、影响的 task IDs、范围、依赖、验收、迁移和回滚影响。
3. 判定是否仍在 Phase B 冻结范围内；属于 Phase C 的变更默认延后。
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
| Backlog 模板与 parser | PB-002, PB-008 | 不得插入或重排现有固定列 |
| Requirements 模板与 impact | PB-003, PB-008 | 不静默放宽 V1 fingerprint/gate |
| V4 Schema `planning/risk/retrospective` | PB-004 | 只让现有字段改变流程成本，不另建 profile 系统 |
| 项目护栏 registry / DECISIONS 模板 | PB-005, PB-008 | 不替项目生成或批准真实基线 |
| `workflow_common.py` | PB-002, PB-005, PB-006 | 派生 focus/WIP/registry 摘要，不写产品决定 |
| `workflow_check.py` | PB-002–005, PB-007 | 只读 readiness 和防绕过 checker |
| `workflow_state.py` | PB-003, PB-004, PB-006, PB-007 | 决定、impact、dequeue/reopen 事务 |
| `workflow_lane.py` | PB-007 | 只处理 queue/claim/recovery，不加产品状态 |
| Skills 与产品文档 | PB-008 | 解释已实现语义，不先行发明新状态 |
| `install.py`, `verify_package.py` | PB-008 | 升级盘点不猜 focus、不批准基线或 registry |
| `tests/` | PB-002–009 | 每个正路径必须有绕过、并发或失效负例 |

任务需要修改表外路径时，先执行第 8.3 节的计划变更流程。

## 10. Phase B 完成定义

Phase B 只有在以下结果全部有精确证据时才可标记 `phase_b_passed`：

1. Backlog 能机械表达 kind、supports 和 focus，且现有固定列 parser 行为不变。
2. 未确认方向的 core slice 在制数量受可配置 WIP 约束；supporting 工作显式服务同一焦点。
3. 滚动 Requirements 把未来候选留在合同外；切片晋升走现有 impact，不静默放宽 V1 gate。
4. small/no-trigger 可记录 retrospective `not_required`；medium/high-risk 或流程问题仍强制复盘；高风险底线不被 core slice 降低。
5. 重复护栏进入 project-owned registry；impact 由 checker/Reviewer 独立分类，fitness 缺证据即失败。
6. 冲突决定必须显式 supersede；更细作用域失效不误伤无关的人类产品或架构选择。
7. pending/queued 后要么有原子 dequeue/reopen，要么正式保持 abandon-only；两种情况都不能伪造 closeout。
8. 人类主路径仍不要求阅读 JSON、lane、generation 或 queue 细节。
9. V3/V4 Phase A 回归保持通过；product direction、verified、integrated/done 和 released 仍然分开表达。
10. 全部 Evidence Contract v1 claim 被 Reviewer `confirmed`，独立 Review、gate、integration、closeout 和证据持久化完成。

## 11. 待人类审阅的精确内容

本轮只请审阅以下六项：

1. 9 个任务的粒度和默认串行顺序；PB-003/004 与 PB-005 只有在写入面独立时才允许申请有限并行。
2. Backlog 采用“版本化 metadata 为机械真相，可选追加显示列”的方式，而不是插入或重排现有列。
3. 滚动 Requirements 先复用 V1 fingerprint/impact；只有操作验证证明过宽时才另开 revision 设计切片级 fingerprint。
4. WIP 缺省为每个产品目标 1 个未确认方向 core slice，且可配置。
5. PB-007 把 dequeue/reopen 留在 Phase B，但若无法证明原子安全，允许该任务以 abandon-only 冻结结束。
6. 计划批准与实施授权分开；批准 revision 不自动开始 PB-002。

人类批准后，下一次只将 `计划状态` 更新为 `approved_not_started`，不修改第 2–10 节，因而 normative fingerprint 保持不变。该状态回写提交作为后续 task authorization 引用的 approved plan commit；实施授权继续保持关闭，直到人类明确授权相应里程碑。
