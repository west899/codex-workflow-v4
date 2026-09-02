# Codex Workflow V4 Phase C 执行计划

> 本文件是 Phase C 唯一规划真相源，保存范围、任务、依赖、验收和变更规则。任务授权后，实时生命周期状态只从对应 task record 读取，本文件不镜像 lane、Review、gate、integration 或 closeout 状态。

## 1. 控制块

| 字段 | 值 |
| --- | --- |
| Plan ID | `V4-PHASEC` |
| Revision | `2` |
| Normative fingerprint | `d0db2b72ae6abba472b794befdc7d5b2a8cdb0e3ac5e2255dd1c784579a1b35c` |
| Fingerprint material | `v1`：从 `## 2. 阶段目标与不变量` 行开始，到 `## 11. 待人类审阅的精确内容` 行之前的精确 UTF-8 字节，使用 SHA-256 |
| 计划状态 | `phase_c_passed` |
| 实施授权 | `phase_c_authorized_complete` / 用户于 2026-09-02 授权按 revision 2 一次性完成 PC-002–008；Independent Review `pass` 后于同日收口 |
| 目标分支 | `v4` |
| Phase B 基线 | `phase_b_passed` / Plan ID `V4-PHASEB` revision `2` / fingerprint `70200558cc4e063acec1dd6e37cac658e3cec888699a8cca584b13e33c53a9ae` |
| 取代 | revision `1` / fingerprint `6dfba554762ea6464ef1da857e36c83fecf255990bf2d59297d56a74d62e9cbb` |
| 设计输入 | `改进建议.md` 第 11.2、12.1、18 节阶段 C、第 21–22 节；`V4_PHASEA_PLAN.md` / `V4_PHASEB_PLAN.md` 第 3.2 节延后表 |
| 当前允许动作 | 封存 Phase C；integration/closeout 不写成 released |
| 当前禁止动作 | 改写 V3 strict-ff / remote claim / closeout 判定；并行 `in_progress`；新增 decision kind、主状态机或第二套 checkpoint 命令；把 integration/closeout 写成 released；把 branch protection 或 merge queue 当成 V4 Reviewer |

规范指纹校验命令：

```bash
awk '/^## 2\. 阶段目标与不变量$/ {capture=1} /^## 11\. 待人类审阅的精确内容$/ {capture=0} capture {print}' V4_PHASEC_PLAN.md | shasum -a 256
```

状态转换只允许：

```text
awaiting_human_review
  -> approved_not_started
  -> milestone_authorized
  -> executing
  -> phase_c_passed
```

任何验收语义、范围或依赖变化都必须先走第 8 节的变更流程。计划批准与实施授权分开；批准 revision 不自动开始 PC-002。

## 2. 阶段目标与不变量

Phase C 在 Phase A/B 已生效的产品反馈内环和滚动细化之上，再评估远端重复治理：provider receipt、受保护分支和 merge queue 能否减少本地与远端重复证明。本 revision 把评估结论冻结为一次性默认可执行路径：保持 V3 strict-ff，增加只读 provider receipt，用负例锁住 GitHub 与 V4 的语义缺口。本阶段不接受非 ff 产品集成，也不删除任何现有本地证明。

实现必须同时保持九个不变量：

1. 不改写 V3 lane、lease、remote claim、canonical delivery、strict-ff 判定和 two-phase closeout 的产品集成算法；V3 任务继续按原算法收尾。`prepare-remote-closeout` 继续要求 `merge_strategy == "ff"` 且 `result_commit == pr_head_commit`，且每条 CI check 的 `status == "success"`。
2. 不增加新的全局主状态机，也不增加第二套 checkpoint 命令。
3. 不把 branch protection 或 merge queue 自动等价为 V4 Independent Reviewer。本 revision 的等价矩阵已预填为：五缺一即不得替代 Independent Reviewer（独立性、最新 diff 批准、等价验收清单、所需检查、无未授权 bypass）。
4. GitHub `skipped` / `neutral` 对 V4 不是 success；缺 expected check source 或允许管理员 bypass 时 fail closed。
5. 不得统一假设 `head == result`。本 revision 记录 `pr_head`、`merge_group`、`result_commit`、`result_tree` 字段形状，但产品集成仍只接受 ff 且 `result_commit == pr_head_commit`；`head != result` 的证据必须失败。
6. 默认 merge 策略仍是 strict-ff。接受 squash / merge-commit / merge-queue result 作为产品集成，属于未来独立计划，不在本 revision 一次性路径内。
7. product direction、verified、integrated/done、released 继续分称。本阶段不实施发布批准与部署编排。
8. STATUS 继续只派生展示；`workflow_lane.py` 仍不承载产品语义。
9. 整阶段实施授权覆盖已冻结的 PC-002–008 串行交付。它不合并任务、不跳过独立 Review/gate/closeout，也不允许用后续未封存实现放宽当前门禁。只有满足第 5.2 节连接标准且未命中第 5.3 节停止项时，才能进入下一任务。中途不再请求人类选择路径 A/B，也不再请求分任务授权。

Phase C 的人类结果：使用者能说清当前仓库远端集成实际证明了什么；哪些 GitHub 能力看起来重复、为什么仍不能删；默认路径仍是 strict-ff；只读 receipt 不能替代 Reviewer 或放宽 CI success。

## 3. 冻结范围

### 3.1 Phase C 范围内

1. 盘点 V3 `remote_pr_ci` evidence JSON、`prepare-remote-closeout`、`confirm-closeout`、`remote-claim` / `remote-release`、strict-ff、CI `success` 语义和 closeout fingerprint 各自证明什么。
2. 对照 GitHub branch protection / rulesets、dismiss stale reviews、latest-push approval、expected check source、禁止管理员 bypass、required checks 的 `skipped`/`neutral` 成功语义、merge queue 重跑 checks 但不重跑人类 Review、以及 squash/merge/rebase/merge-queue 的 result 与 PR head 分离。
3. 写出等价矩阵，且矩阵结论必须与第 3.3 节预填行一致。
4. 增加通用 provider receipt 信封 + GitHub 形状的只读校验；默认 dry-run；非法 receipt fail closed；receipt 只能作为附加证据，不能减少或绕过现有本地证明。
5. 为 topology 与 GitHub 语义缺口增加 fail-closed fixture：`head != result`、`skipped`/`neutral`、stale approval、错误 check source、管理员 bypass、把 branch protection 当成 Reviewer。
6. 书面封存 keep-strict-ff：本 revision 不接受非 ff 产品集成，不删除本地证明。
7. Skills、WORKFLOW/README/功能文档与安装盘点同步到已实现语义。
8. 全量回归，以及上述 remote fixture。

### 3.2 明确延后

| 延后项 | 目标阶段 | Phase C 处理方式 |
| --- | --- | --- |
| 接受 squash / merge-commit / merge-queue 作为产品集成 | 独立计划 | 本 revision 对 `head != result` fail closed |
| 删除任何现有本地远端证明 | 独立计划 | 只读 receipt 只能附加，不能替代 |
| 发布批准与部署编排 | 独立计划 | integration/closeout 不写成 released |
| 第二家 provider 的完整适配器 | 独立计划 | receipt 信封保持通用；本阶段只实现 GitHub 形状 |
| 新的 decision kind 或第二套 checkpoint 命令 | 不纳入 C | 继续使用 Phase A 的四种 kind 与三种 checkpoint mode |
| 批量把活动 V3 record 改写成 V4 | 不纳入 C | V3 继续按原语义收尾 |
| 自动 push、开 PR、合并、force-update | 不纳入 C | 工作流仍不执行这些外部动作 |
| 把本仓库接到真实 GitHub merge queue 作为完成条件 | 不纳入 C | 以可复现 fixture 为准 |

### 3.3 口径冲突的冻结结论

1. Phase C 本 revision 是评估 + 只读增强，不是重写远端算法。任何改 strict-ff / remote claim / closeout 产品集成判定的实现都是范围外，必须 `stop`。
2. keep-strict-ff 是本 revision 的唯一产品集成路径，不是失败，也不跳过 PC-005/006。PC-005/006 交付负例和字段形状，不交付新的成功 merge 拓扑。
3. 仅仅启用 branch protection、required reviews 或 merge queue，并不自动等价于 V4 Independent Reviewer。
4. GitHub 可把 `skipped`/`neutral` 视为满足 required check；V4 继续要求 `status == "success"`，缺证据即失败。
5. merge queue 重跑的是 required status checks，不是人类 Review。不得把“队列重跑 CI”写成“Reviewer 已重新批准最新 diff”。
6. 现有 `prepare-remote-closeout` 要求 `merge_strategy == "ff"` 且 `result_commit == pr_head_commit`。这是本 revision 唯一受支持的产品集成拓扑。
7. 预填等价矩阵如下。PC-003 必须逐行落账，不得改写“本 revision 处置”。若盘点发现需要改处置，必须 `stop` 并走第 8.3 节。

| 矩阵 ID | 本地证明 | GitHub 对照 | 看起来重复？ | 本 revision 可否删除本地证明 | 本 revision 可否当成 Reviewer |
| --- | --- | --- | --- | --- | --- |
| EQ-001 | Independent Reviewer + `record-review` | required reviews / CODEOWNERS | 部分 | 否 | 否 |
| EQ-002 | 最新 delivery snapshot / exact diff | dismiss stale reviews、latest-push approval | 仅当两项都开启且可机械证明 | 否 | 否 |
| EQ-003 | Evidence Contract 验收清单 | 无等价物 | 否 | 否 | 否 |
| EQ-004 | CI `status == success` | required checks 把 `skipped`/`neutral` 当成功 | 否，语义更松 | 否 | 否 |
| EQ-005 | 无管理员 bypass | 允许管理员 bypass | GitHub 默认可能更松 | 否 | 否 |
| EQ-006 | `result_commit == pr_head_commit` 且 ff | squash / merge / merge-queue 的 result 常不等于 head | 否 | 否 | 否 |
| EQ-007 | closeout fingerprint | 无等价物 | 否 | 否 | 否 |
| EQ-008 | remote claim / release CAS | 无等价物 | 否 | 否 | 否 |
| EQ-009 | expected check source | 未钉死 source 的 required check | 否 | 否 | 否 |

### 3.4 一次性实施授权

`phase_c_authorized_complete` 是本 revision 的实施模式。它一次覆盖 PC-002 至 PC-008，并受以下约束：

1. 8 个任务保持独立 delivery snapshot，不合成一个任务。
2. 每个任务仍须独立 Review、gate 和 closeout。
3. 始终只允许一个任务处于 `in_progress`。PC-004/005/006 共享 checker/state 写入面，本 revision 不申请并行。
4. 计划批准与整阶段实施授权仍然分开；批准本 revision 不自动开始 PC-002。
5. Coordinator 在连接标准成立且未命中停止项时，可以直接开始下一任务，不必再按任务向人类申请授权，也不必停下来选择路径 A/B。
6. 任务范围内的修复和复审可以继续；若发现需要改第 2–10 节、重开已封存任务、接受非 ff 集成、删除本地证明、或离开第 9 节路径表，必须停止。

### 3.5 Review 节奏

两种 Review 互不替代。本 revision 不再设置中途人类架构门：

| 层 | 做什么 | 何时 | 能否跳过 |
| --- | --- | --- | --- |
| Independent Reviewer | 核对当前任务 snapshot、claim、负例和 V3 回归 | 每个 PC-00x closeout 前 | 不能 |
| 人类阶段审阅 | 批准本计划 fingerprint；给出整阶段实施授权；阶段收口 | PC-001、实施授权、PC-008 | 实施授权前不能开始 PC-002 |

人类不必为 PC-002 之后的每个任务再授权。只在最后做 Independent Review 不够。中途把“是否接受 merge queue”再问一次人类，会破坏一次性执行；该问题已由第 3.3 节预填为否。

同一任务内允许不超过两轮完整 Review 的修复；第三轮前必须停止汇报。

## 4. 难度标尺

| 等级 | 判定标准 |
| --- | --- |
| 中 | 主要影响一个消费面，状态语义已由上游任务冻结 |
| 高 | 涉及远端证据对照、Schema 或多模块契约，需要大量负例 |
| 极高 | 涉及 merge topology、CAS、closeout 证据失效、provider 等价或跨版本恢复 |

整体 Phase C 难度：`XL`。当前远端 closeout 被 end-to-end、remote claim/release 和故障恢复回归覆盖。本 revision 降低实施风险的方式是禁止改产品集成判定，而不是减少 Independent Review。

## 5. 里程碑与任务总表

| ID | 里程碑 | 任务结果 | 依赖 | 难度 | 规划处置 |
| --- | --- | --- | --- | --- | --- |
| PC-001 | M1 计划冻结 | 冻结一次性默认路径、预填矩阵和延后表 | Phase B `phase_b_passed` | 高 | `approved` |
| PC-002 | M2 评估 | 盘点 V3 远端证明与 GitHub 能力的重复/缺口 | PC-001 | 高 | `planned` |
| PC-003 | M2 评估 | 把第 3.3 节预填矩阵落账为可引用文件 | PC-002 | 高 | `planned` |
| PC-004 | M3 只读 receipt | keep-strict-ff 封存 + provider receipt Schema 与只读校验 | PC-003 | 极高 | `planned` |
| PC-005 | M3 拓扑负例 | 记录 topology 字段形状；`head != result` 必须失败 | PC-004 | 极高 | `planned` |
| PC-006 | M4 反替代负例 | 证明不能用 GitHub 能力删除或替代本地证明 | PC-005 | 极高 | `planned` |
| PC-007 | M5 人类与兼容面 | 同步 Skills、模板、文档和升级盘点 | PC-006 | 中 | `planned` |
| PC-008 | M6 证明闭环 | 全量回归与 remote fixture 矩阵 | PC-007 | 极高 | `planned` |

默认执行顺序：

```text
PC-001 -> PC-002 -> PC-003 -> PC-004 -> PC-005 -> PC-006 -> PC-007 -> PC-008
```

即使获得整阶段实施授权，也只允许一个任务处于 `in_progress`，并按上表串行。不得跳过 PC-005/006，也不得把它们实现成新的成功 merge 拓扑。

### 5.1 单任务阶段内容

PC-002 至 PC-008 每个任务都使用同一阶段合同。PC-001 是人类计划冻结任务，以批准 exact Plan ID/revision/normative fingerprint 作为终态，不建立 Developer lane。

| 任务阶段 | 必须写入的内容 | 退出条件 |
| --- | --- | --- |
| Plan / authorization | Plan ID、revision、normative fingerprint、approved plan commit、依赖 closeout refs、目标结果、scope in/out、allowed paths、acceptance IDs、难度与恢复边界 | 人类明确授权当前任务、所在里程碑，或整阶段 `phase_c_authorized_complete` |
| Coordinator | 创建 exact task record/delivery contract；验证前置依赖、基线 commit、证据 scope 和负例矩阵；claim lane/resources | preflight 零警告，且任务合同与本计划无差异 |
| Developer | 只修改 allowed paths；先完成最小端到端结果；记录精确命令、平台、表面、负例、剩余风险和 Evidence Contract v1 claims | exact delivery snapshot 形成，focused/full 验证与绕过负例通过 |
| Independent Reviewer | 从 snapshot 重建范围与合同；核对每个 claim/scope/command/fingerprint；给出 `confirmed/narrowed/rejected/unverified`；检查 V3/V4 回归和跨模块失效 | 所有 claim `confirmed`，无未处理 P0–P3 finding，Review 绑定当前 snapshot |
| Gate / integration | 重放 task-specific gate、通用 gate、集成前绕过检查、target ancestry/queue/claim 校验 | 只有当前 Review、contract 和 integration candidate 全部匹配时才集成 |
| Closeout | 封存 task record、Developer/Review/gate/integration evidence、closeout fingerprint、bundle/manifest 和全部引用哈希 | task record 终态、runtime claim/queue/lane 按策略释放；本计划只追加 sealed task/closeout ref，不镜像实时状态 |

任务授权后发现计划外 allowed path、acceptance 或依赖时，必须停在当前阶段，不得用 Developer/Reviewer 备注替代第 8.3 节的计划变更流程。本包目录未自包装为 V4 工作流项目时，允许用测试夹具与计划文件证明，不宣称 package-local task record closeout。

### 5.2 任务连接标准

每个任务 closeout 必须留下只读连接包，供下一任务核验。本计划不镜像这些值。连接包至少包含：本任务 ID、未被突破的 allowed paths、delivery commit/hash/snapshot、全部 claim `confirmed` 的 Review、本任务冻结的 parser/schema/语义、剩余风险，以及连接处置 `continue` / `report_and_continue` / `stop`。

下一任务开始前，Coordinator 必须确认：

1. 前一任务已经 closeout，且连接处置不是 `stop`。
2. 下一任务仍在第 9 节路径表内，且不需要改第 2–10 节。
3. 前一任务冻结的 parser/schema/语义没有被回改。
4. 当前没有第二个 `in_progress` 任务。
5. 本任务的 focused/full 验证已通过；PC-008 才做全矩阵宣称，但每个任务不得把失败测试留到最后。

任务之间的具体连接：

| 从 | 到 | 继续前必须成立 | 停止条件 |
| --- | --- | --- | --- |
| 本 revision 获批且整阶段已授权 | PC-002 | G-C0 通过；实施授权为 `phase_c_authorized_complete` | 缺批准或实施授权 |
| PC-002 | PC-003 | 每条 V3 远端证明都写明“证明什么 / 与 GitHub 是否重复 / 缺少什么就不能删”；未改 closeout 算法 | 盘点把 GitHub 能力写成已经等价于 V4 Reviewer |
| PC-003 | PC-004 | 落账矩阵与第 3.3 节预填行一致；`skipped`/`neutral` 不是 success；禁止默认 `head == result` | 改写预填处置，或留下“启用 branch protection 即可跳过 Reviewer”的口子 |
| PC-004 | PC-005 | Schema/只读校验负例通过；`merge_strategy==ff` 与 `result==head` 行为未改；keep-strict-ff 已封存 | 只读校验变成新的 merge 算法，或 receipt 能绕过本地证明 |
| PC-005 | PC-006 | `head != result` 失败；可选 topology 字段缺省不影响 ff 成功路径 | 非 ff 证据被当成成功 |
| PC-006 | PC-007 | EQ-001–009 的反替代负例通过；未删除本地证明 | 把 branch protection 当成 Reviewer，或删除了 CI success / ff 检查 |
| PC-007 | PC-008 | 文档只解释已实现语义；升级盘点不猜远端策略、不批准 GitHub 配置 | 文档发明未实现状态、写成 released，或声称已支持 merge queue 集成 |
| PC-008 | `phase_c_passed` | 第 10 节各项均有精确证据 | 把未跑平台写成已验证，或缺少 Review/gate/closeout |

### 5.3 继续、汇报后继续、停止

整阶段授权把任务间动作分成三类。

`continue`：前一任务连接包完整、未命中停止项，Coordinator 立即开始下一任务。

`report_and_continue`：不阻断整阶段，但必须在连接包中写明结论后继续。包括：

- 某平台未跑，只记录为未验证。
- 同一任务内、allowed paths 内的修复和不超过两轮的完整 Review。
- 本包未自包装 V4，因而没有 package-local task record closeout。

`stop`：立即停止整阶段，保留当前 snapshot，向人类汇报最小判断项。包括：

- 需要修改第 2–10 节，或离开第 9 节路径表。
- 改 strict-ff / remote claim / closeout 的产品集成判定。
- 接受 `head != result` 作为产品集成成功。
- 删除任何第 3.3 节禁止删除的本地证明。
- 把 branch protection 或 merge queue 当成 V4 Reviewer。
- 把 `skipped`/`neutral` 写成 success。
- 把 closeout 写成 `released`，或把发布/部署拉进本阶段。
- 同一任务连续两轮完整 Review 仍不能 `pass`。
- 出现第二个 `in_progress`，或触及 Phase A/B 冻结语义。
- 把未验证平台写成已验证。

停止后不得用“整阶段已经授权”继续相邻任务。恢复必须先满足第 8.3 节或获得新的人类指令。

## 6. 任务卡片

### PC-001：冻结 Phase C 范围

- 目标：把远端治理收窄为可一次性执行的契约，关闭路径选择歧义。
- 交付：批准本计划的精确 Plan ID/revision/normative fingerprint；冻结预填矩阵、只读 receipt、负例范围和延后表。
- 验收：每个术语有唯一定义；无隐式发布编排；无新的主状态机或 checkpoint 命令；未把 GitHub 能力写成已经可替代 V4。
- 主要文件：`V4_PHASEC_PLAN.md`。
- 完成门：人类批准精确 Plan ID、revision 和 normative fingerprint；批准状态回写不改变 fingerprint material。

### PC-002：盘点 V3 远端证明与 GitHub 缺口

- 目标：说清当前工作流远端集成实际证明了什么，以及 GitHub 保护分支/merge queue 与这些证明的重复和缺口。
- 交付：`V4_PHASEC_INVENTORY.md`。每行至少含：本地证明 ID、代码位置、证明问题、GitHub 对照、重复等级（无/部分/表面）、缺少什么就不能删、对应矩阵 ID。
- 验收：至少覆盖 `remote_pr_ci` evidence 字段、`prepare-remote-closeout` 的 ff/`result==head`/CI success 判定、`confirm-closeout`、`remote-claim`/`remote-release`、closeout fingerprint；GitHub 侧至少覆盖 stale approval、latest-push approval、expected check source、管理员 bypass、`skipped`/`neutral`、merge queue 不重跑人类 Review、`merge_group` 与 PR head 分离。盘点本身不改算法。
- 主要文件：`V4_PHASEC_INVENTORY.md`；只读引用 `workflow_state.py`、`workflow_lane.py`、remote 测试。
- 完成门：对照表无空行；没有把缺口写成已满足。

### PC-003：落账等价矩阵与 topology 契约

- 目标：把第 3.3 节预填矩阵写成可被测试引用的文件，而不是重新发明政策。
- 交付：`V4_PHASEC_EQUIVALENCE.md`，含 EQ-001–009 全文、topology 字段定义、以及“附加 receipt ≠ 替代证明”的一句话规则。
- 验收：处置列与第 3.3 节逐字一致；Reviewer 五缺一不得替代；`skipped`/`neutral` 不是 success；禁止默认 `head == result`；缺配置保持 V3 strict-ff。
- 主要文件：`V4_PHASEC_EQUIVALENCE.md`。
- 完成门：本任务不实现 Schema，不改 closeout；若盘点想改处置，停止而不是改矩阵。

### PC-004：keep-strict-ff 封存与只读 receipt

- 目标：正式保持 V3 产品集成算法，并增加不能绕过门禁的只读 provider receipt。
- 交付：`V4_PHASEC_KEEP_STRICT_FF.md`；`provider-receipt-v1.schema.json`；只读校验器；默认 dry-run；非法 receipt 与“用 receipt 替代本地证明”的负例。
- 验收：现有 `merge_strategy==ff` 与 `result_commit == pr_head_commit` 行为保持；缺字段、未知 provider、`skipped`/`neutral`、空 checks 失败；receipt 不能让 closeout 在缺少 ff/CI success 时成功。
- 主要文件：`V4_PHASEC_KEEP_STRICT_FF.md`、`payload/.codex-workflow/schemas/provider-receipt-v1.schema.json`、`workflow_check.py` 或只读 helper、`tests/test_v4_phase_c.py`。
- 完成门：故障注入不留半状态；V3 remote 回归通过。

### PC-005：topology 字段与非 ff 负例

- 目标：让证据形状能说出实际拓扑，同时继续拒绝非 ff 产品集成。
- 交付：远程 evidence 允许可选 `merge_group` / `result_tree` 字段；缺省时 ff 成功路径不变；`head != result`、`merge_strategy != "ff"`、result 不是 target_parent 的 ff 后代一律失败。
- 验收：不得把 squash/merge-queue 伪装成 ff；不得因为填写了 `result_tree` 就让非 ff 成功。
- 主要文件：`workflow_state.py`（仅扩展校验与错误信息，不放宽判定）、Phase C 测试。
- 完成门：现有 ff-only 测试继续通过；新增非 ff 负例失败信息指向 EQ-006。

### PC-006：反替代负例

- 目标：机械证明第 3.3 节“不可删除、不可当成 Reviewer”的处置。
- 交付：EQ-001–009 各至少一条绕过负例；例如 `github_review_equivalent`、仅 branch protection、仅 merge queue、`skipped` 当 success、管理员 bypass、错误 check source。
- 验收：任何“启用了 branch protection 因此跳过 Independent Reviewer”的路径失败；未删除 CI success / ff / closeout fingerprint / remote claim 检查。
- 主要文件：`workflow_check.py`、`workflow_state.py`、Phase C 测试。
- 完成门：每条矩阵 ID 都有失败测试；没有减少现有成功路径的本地证明。

### PC-007：Skills、模板与文档同步

- 目标：让 Coordinator、Developer、Reviewer 和人类对同一 Phase C 契约使用同一语义。
- 交付：WORKFLOW/README/功能文档；Skills 中远端集成与 provider receipt 说明；升级盘点列出远端默认仍是 strict-ff，且不猜测 GitHub 配置。
- 验收：文档示例通过 Schema/checker fixture；`workflow_lane.py` 仍不承载产品语义；不新增另一个 checkpoint 命令或状态库；不把 done 写成 released；不声称已支持 merge queue 产品集成。
- 主要文件：Skills、`payload/.codex-workflow/docs/WORKFLOW.md`、`README.md`、`功能.md`、`install.py`、`verify_package.py`。
- 完成门：人类主路径不要求阅读 JSON、lane、generation 或 queue 细节即可知道远端默认仍是 strict-ff，receipt 只是附加证据。

### PC-008：回归与远端证明闭环

- 目标：证明 Phase C 完成了评估和只读增强，且没有放宽 V3 远端算法或 Reviewer。
- 交付：ff-only、remote claim/release、fault recovery 回归；non-ff / skipped / bypass / fake-reviewer fixture；`V4_PHASEC_CLOSEOUT.md`。
- 验收：V3 全量回归通过；V4 Phase A/B 负例保持；缺失平台明确记录，不写入已验证范围。
- 验证矩阵：当前已验证的 macOS / CPython 3.9 必须通过；Python 3.12/3.13 与 Windows 若未跑，必须记录为未验证。
- 完成门：独立 Review PASS；证据持久化并校验全部引用哈希。

## 7. 里程碑门禁

| 门 | 允许进入条件 | 允许退出条件 |
| --- | --- | --- |
| G-C0 计划冻结 | Phase B `phase_b_passed` | 人类批准本计划精确 Plan ID/revision/normative fingerprint；实施授权仍可保持关闭 |
| G-C1 评估落账 | G-C0 通过，且整阶段已授权 | PC-002/003 完成；矩阵与第 3.3 节一致 |
| G-C2 只读增强 | G-C1 通过，且第 5.2 节 PC-003→PC-004 连接成立 | PC-004 完成；ff 行为未改；receipt 不能替代本地证明 |
| G-C3 负例锁 | G-C2 通过 | PC-005/006 完成；非 ff 与反替代负例 PASS |
| G-C4 证明闭环 | G-C3 通过，且第 5.2 节进入 PC-007 的连接成立 | PC-007/008 完成；独立 Review、gate 和证据持久化全部完成 |

不允许使用后续里程碑的临时实现反向放宽当前门禁。受阻时局部修正当前契约，范围扩展需要计划 revision。

在 `phase_c_authorized_complete` 下，Coordinator 可以在当前门退出条件与第 5.2 节连接标准同时成立时进入下一门，不必再取得人类的分里程碑授权。第 5.3 节 `stop` 除外。

## 8. 防漂移规则

### 8.1 规范优先级

```text
人类批准的 V4-PHASEC exact Plan ID/revision/normative fingerprint
  > 第 3.3 节预填等价矩阵
  > 当前已授权 task record / delivery contract
  > Developer 实现与临时工作记录
```

`改进建议.md` 是设计输入和历史理由，Phase C 实施范围以人类批准后的本计划为准。Phase A/B 第 2–10 节冻结语义对本阶段仍然有效。本 revision 明确不取代 V3 strict-ff 产品集成判定。

### 8.2 单一状态源

1. 本文件只管计划基线，不同步任务的实时 phase/status/generation。
2. 任务授权后，task record 管理 Developer、Review、gate、integration 和 closeout 真相。
3. STATUS 和 Stop Hook 只派生展示，不写入决定或改变生命周期。
4. 安装模板不代表本仓库 Phase C 进度。
5. GitHub UI、branch protection 页面和合作式 JSON 都不是安全信任根。

### 8.3 计划变更流程

任何人提出新字段、新命令、新状态、新集成机制、任务合并/拆分、接受非 ff 集成或验收放宽时，必须按以下顺序处理：

1. 暂停受影响任务，保留当前 snapshot 和证据。
2. 记录变更理由、影响的 task IDs、范围、依赖、验收、迁移和回滚影响。
3. 判定是否仍在 Phase C 冻结范围内；属于非 ff 产品集成、删除本地证明、发布编排或其他阶段的变更默认延后。
4. 向人类展示原计划与提议计划的精确差异。
5. 获得人类批准后增加 revision，保留修订记录并提交 Git。
6. 使所有受影响 contract/decision/receipt/review fingerprint 按矩阵失效，再恢复实施。

自由文本备注、临时测试或 Developer 便利性不能自动扩大范围。

### 8.4 证据约束

1. 所有验收 claim 遵守 Evidence Contract v1：有限 scope、精确 target、claim-command 闭包、fingerprint 和 Reviewer assessment。
2. 每个任务只声明实际观察到的平台、命令和表面，不使用未经证明的全局表述。
3. 任何 Reviewer assessment 为 `narrowed`、`rejected` 或 `unverified` 时，Review 不得 pass。
4. 每个里程碑封存 Developer evidence、Review evidence、gate 结果和引用哈希。
5. provider receipt 只作为合作式外部证据；测试不能假装证明填写者在现实中一定是人类，也不能把 GitHub 页面截图当成机械证明。

## 9. 文件责任边界

| 路径 | 允许任务 | 边界 |
| --- | --- | --- |
| `V4_PHASEC_PLAN.md` | PC-001 | 批准回写只改第 1、11、12 节，不改第 2–10 节 |
| `V4_PHASEC_INVENTORY.md` | PC-002 | 只读盘点，不改算法 |
| `V4_PHASEC_EQUIVALENCE.md` | PC-003 | 落账第 3.3 节，不改处置 |
| `V4_PHASEC_KEEP_STRICT_FF.md` | PC-004 | 封存 keep-strict-ff，不留下临时非 ff 命令 |
| `payload/.codex-workflow/schemas/` | PC-004 | 只增加 receipt Schema，不改 task-record-v3 字节 |
| `workflow_check.py` | PC-004, PC-006, PC-008 | 只读 readiness 和防绕过 checker |
| `workflow_state.py` | PC-005, PC-006 | 只扩展可选字段与失败信息，不放宽 ff/CI 判定 |
| `workflow_lane.py` | 无，除非第 8.3 节批准 | 不加产品状态；remote claim 算法不动 |
| `workflow_common.py` | PC-004, PC-007 | 派生远端摘要，不写产品决定 |
| Skills 与产品文档 | PC-007 | 解释已实现语义，不先行发明 merge queue 集成 |
| `install.py`, `verify_package.py` | PC-007 | 升级盘点不猜 GitHub 配置、不批准保护规则 |
| `tests/` | PC-004–008 | 每个正路径必须有绕过、拓扑或反替代负例 |
| `V4_PHASEC_CLOSEOUT.md` | PC-008 | 映射第 10 节，不是运行时真相源 |

任务需要修改表外路径时，先执行第 8.3 节的计划变更流程。

## 10. Phase C 完成定义

Phase C 只有在以下结果全部有精确证据时才可标记 `phase_c_passed`：

1. V3 远端证明与 GitHub 能力的对照表完整，每条都写明能否删除及缺少什么就不能删。
2. 等价矩阵与第 3.3 节预填行一致；Reviewer 独立性未被 branch protection 或 merge queue 自动替代。
3. keep-strict-ff 已封存；产品集成算法未改；只读 receipt 不能替代本地证明。
4. 默认 merge 仍是 strict-ff；`head != result` 失败。
5. `skipped`/`neutral`、管理员 bypass、stale approval 和错误 check source 在 V4 路径上 fail closed。
6. 未实施发布批准与部署编排；integration/closeout 仍不写成 released。
7. 人类主路径仍不要求阅读 JSON、lane、generation 或 queue 细节即可知道远端默认仍是 strict-ff。
8. V3/V4 Phase A/B 回归保持通过；product direction、verified、integrated/done 和 released 仍然分开表达。
9. 全部 Evidence Contract v1 claim 被 Reviewer `confirmed`，独立 Review、gate 和证据持久化完成。

整阶段实施授权不减少上述任何一项。缺少任一任务的封存连接包，不得标记 `phase_c_passed`。

## 11. 待人类审阅的精确内容

revision 1 第 11 节六项已于 2026-09-02 批准。本 revision 把一次性执行所需的剩余规划决定冻结如下，不再等待中途路径选择：

1. 8 个任务严格串行；`phase_c_authorized_complete` 覆盖 PC-002–008，不合并、不并行、不跳过 Independent Reviewer。
2. 中途不再设置人类架构门。第 3.3 节预填矩阵就是本 revision 的架构决定：不删除本地证明，不把 GitHub 当成 Reviewer，不接受非 ff 产品集成。
3. PC-004 同时交付 keep-strict-ff 封存和只读 receipt；receipt 只能附加，不能替代。
4. PC-005/006 必须执行，但只交付字段形状和负例，不交付新的成功 merge 拓扑，也不跳过。
5. 发布/部署仍不纳入 C。同一任务内最多两轮完整 Review，第三轮前停止汇报。
6. 计划批准与实施授权分开。人类已于 2026-09-02 给出 `phase_c_authorized_complete`。Coordinator 连续执行 PC-002–008，只在第 5.3 节 `stop` 时停下。

本文件第 1 节状态回写不改变第 2–10 节，因而实施授权打开时 normative fingerprint 保持不变。该状态回写提交作为后续 task authorization 引用的 approved plan commit。

## 12. 修订记录

| Revision | 计划状态 | 变更 |
| --- | --- | --- |
| `1` | 已批准后被本 revision 取代 | 初始冻结 8 个任务、三层 Review、中途 `G-C1`、路径 A/B |
| `2` | `phase_c_passed` | 一次性默认路径落地；Independent Review 先 `changes_requested` 后 `pass`；人类于 2026-09-02 收口 |
