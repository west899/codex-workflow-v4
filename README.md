# Codex Workflow V2.1 可移植包

这是一个只面向 OpenAI Codex 的项目级开发控制面，适用于 Web、移动端、桌面、CLI、库、后端、数据工程、自动化、迁移和基础设施项目。

“MVP”在本文中表示**当前准备交付的目标版本**，不要求项目一定是消费产品。

## 环境要求

- OpenAI Codex，支持项目级 `AGENTS.md`、Skills、Custom Agents 和 Hooks；目标项目必须设为 trusted，安装或更新后需在 `/hooks` 中信任项目 Hook。
- Python 3.9 或更高版本。
- Git。
- 不依赖 Node.js、第三方 Python 包或其他 AI 平台。

## 安装

包可以解压到任意位置。安装器始终从自身位置读取载荷：

```bash
python3 /任意路径/codex-workflow-v2/install.py /目标项目路径
```

可选指定项目名：

```bash
python3 /任意路径/codex-workflow-v2/install.py /目标项目路径 \
  --project-name my-project
```

安装器会：

1. 将通用治理文件、Skills、Custom Agents、Hooks 和检查脚本安装到项目根目录。
2. 从本 README 的 V2 标记区生成 `docs/WORKFLOW_V2.md`。
3. 替换本包管理的 Hook、保留其他 Hook，并同时安装 Unix 与 Windows 命令。
4. 创建或更新 `.gitignore` 中带标记的工作流托管区。
5. 目标不是 Git 仓库时执行 `git init`，但不会创建提交。
6. 运行 `python3 scripts/workflow_check.py manual`。
7. 写入 `.codex/workflow-v2-install.json`，保存版本和受管文件哈希，支持识别安全升级。

默认不会覆盖用户修改过的受管文件；已有 V2.1 manifest 且内容未被修改时可以安全升级。没有 manifest 的旧版项目首次升级会要求确认，使用 `--force` 后先备份再替换：

```bash
python3 install.py /目标项目路径 --force
```

旧文件会先备份到 `.codex-workflow-backup/<时间戳>/`；安装或自检失败时，本次文件修改会恢复。

安装完成后，在 Codex 中执行 `/hooks` 审查并信任项目 Hook，再启动一个新会话并确认以下文件已生成：

```text
.codex-log/last-session-check.json
```

如果该心跳不存在，说明 SessionStart Hook 尚未实际运行，不能依赖自动门禁；`workflow_check.py manual` 也会对缺失、损坏或超过 24 小时的心跳给出警告。

## 验证包

在安装前验证包本身；该命令会同时运行安装、检查器和 Stop Hook 的回归测试：

```bash
python3 verify_package.py
```

安装后在目标项目验证：

```bash
python3 scripts/workflow_check.py manual
```

首次代码任务前仍需要人类批准初始 Git 基线提交。

## 目录结构

```text
codex-workflow-v2/
├── README.md
├── install.py
├── verify_package.py
├── tests/
└── payload/
    ├── AGENTS.md
    ├── PROJECT.md
    ├── PLAN.md
    ├── DECISIONS.md
    ├── .agents/skills/
    ├── .codex/agents/
    ├── .codex/hooks.json
    └── scripts/
```

<!-- WORKFLOW_DOC_START -->

# AI 协作开发流程 V2

> 状态：通用模板｜当前步骤以目标项目的 `PROJECT.md` 和 `PLAN.md` 为准

## 一眼看懂

`人类负责人确认目标 → 方案/原型 → Backlog 拆分 → [单项开发/审查/gate/合并] × N → 发布门 → 人类负责人发布`

单项状态：`ready → active → verified → done`；整个 MVP 通过发布门并上线后，Backlog 状态才是 `released`。

- 四个根文件仍是权威信息源：`PROJECT.md` 管事实，`PLAN.md` 管现在，`AGENTS.md` 管永久规则，`DECISIONS.md` 管为什么。
- 本文件是操作地图，不复制四个根文件的具体内容；发生冲突时，以最新用户指令和对应权威文件为准。
- 第 1 至 2 步定义整个 MVP；第 3 步完成必要方案/原型、拆成 Backlog 并建立工程基线；每个 Backlog 项重复第 4 至 11 步的合并部分；全部发布门满足后才执行第 11 步的上线部分。
- 主 AI 默认担任 Coordinator；Developer 是唯一代码写入者；Reviewer 默认只读；人类负责人保留范围、风险、永久规则和发布决定权。Reviewer 的只读配置不是操作系统级信任边界。
- **新项目从第 1 步开始：人类负责人与主 AI 逐项回答 `PROJECT.md` 的 Q-001 至 Q-007，先确认“要解决什么问题、交付什么结果”。**
- 文件实际名称是 `AGENTS.md`（复数），不是 `AGENT.md`。

## 共同入口

每次新会话、切换 AI、恢复上下文或开始新任务，都先执行这一入口，再进入对应步骤。

| 项目 | 要求 |
| --- | --- |
| 参与者 | 当前主 AI；必要时人类负责人补充最新目标 |
| 先读 | `AGENTS.md → PROJECT.md → PLAN.md → DECISIONS.md → 本文件相关步骤 → 存在时读取 MVP_BACKLOG/活动任务 → 相关代码/测试` |
| 自动动作 | 项目 trusted 且 Hook 已在 `/hooks` 中获信任后，`SessionStart` 调用 `scripts/workflow_check.py start` 并写 `.codex-log/last-session-check.json` |
| 开工前回答 | 目标；确认事实与未知项；约束与风险；完成证据 |
| 可靠性边界 | Hook 未获信任时会被跳过；Reviewer 权限可能受父会话实时覆盖；可靠性来自入口指令、Skill、任务记录、机械 gate、CI 和人类审批的交叉检查 |
| 冲突处理 | 用户指令、文档、代码或测试矛盾时停止修改，由主 AI 向人类负责人说明冲突 |

`AGENTS.md` 必须在 AI 支持仓库指令时自动生效，但仍由各 Skill 明确要求再次读取；不能假设换一个 AI 后仅靠聊天上下文就会继续遵守。

## 计划层级

| 层级 | 文件 | 回答的问题 |
| --- | --- | --- |
| 目标范围 | `PROJECT.md` | 当前目标版本为谁解决什么问题，做什么与不做什么 |
| 交付拆分 | `docs/MVP_BACKLOG.md` | 整个 MVP 要分成哪些纵向任务，顺序、依赖和发布门是什么 |
| 当前阶段 | `PLAN.md` | 现在正在推进哪个阶段、下一步和阶段证据是什么 |
| 单项任务 | `.agent/runs/<task-id>.json` | 当前这一项的授权、范围、验收、证据、审查和复盘是什么 |
| 复杂执行 | `.agent/plans/<date>-<slug>.md` | 当前 large/high-risk 任务具体怎样实施、验证和恢复 |

所以不存在一个 Plan 自动跑完就得到 MVP：第 3 步形成整个 MVP Backlog，随后每一项分别经过第 4 至 11 步。

## 逐步流程

### 第 1 步：目标与问题发现

| 项目 | 内容 |
| --- | --- |
| 参与者 | 人类负责人 + 主 AI；不启动 Developer 或 Reviewer |
| 人类负责人做什么 | 回答真实问题、使用者、场景、现有替代方案、平台、数据和治理边界 |
| 主 AI 做什么 | 一次聚焦一个主题；把用户确认、AI 假设和未知项分开；不选技术栈、不写交付代码 |
| 读取文件 | `AGENTS.md` 提供事实纪律；`PROJECT.md` 提供现有事实和 Q-001 至 Q-006；`PLAN.md` 提供当前访谈任务；`DECISIONS.md` 防止重复旧错误 |
| 写入文件 | 用户确认的事实写 `PROJECT.md`；当前问题、状态和证据写 `PLAN.md`；发生重要方向选择或纠错才写 `DECISIONS.md` |
| 脚本/门禁 | `scripts/workflow_check.py manual` 检查根文档结构；目标、使用者和核心流程未确认前不得进入实现设计 |
| 结束产物 | 可由人类负责人明确确认的问题定义、使用者和核心场景 |

### 第 2 步：目标版本与范围

| 项目 | 内容 |
| --- | --- |
| 参与者 | 人类负责人 + 主 AI；需要专业调查时主 AI 可查证，但不能替人类负责人决定项目目标 |
| 人类负责人做什么 | 确认一句话目标、核心流程、当前版本必须做、明确不做和可接受风险 |
| 主 AI 做什么 | 把需求写成可观察行为和验收条件；列出适用的安全、隐私、权限、兼容、运行和滥用风险，不适用项注明 N/A |
| 读取文件 | `PROJECT.md` 读取已确认事实；`PLAN.md` 读取阶段门；`AGENTS.md` 防止把建议写成需求；`DECISIONS.md` 读取已有边界 |
| 写入文件 | 当前有效目标、范围和约束写 `PROJECT.md`；阶段任务和验收证据写 `PLAN.md`；关键取舍写 `DECISIONS.md` |
| 脚本/门禁 | `workflow_check.py manual`；只有 `PLAN.md` 的目标阶段门全部有证据，才能进入方案与工程基础 |
| 结束产物 | 用户确认的 MVP 范围、非目标、风险清单和每项能力的验收方式 |

### 第 3 步：方案、Backlog 与工程基线

| 项目 | 内容 |
| --- | --- |
| 参与者 | 人类负责人 + 主 AI；涉及架构、隐私或部署的选择由人类负责人批准 |
| 人类负责人做什么 | 确认任务拆分、Must/Should/Could 优先级和发布门；批准初始 Git 基线及难逆转选择 |
| 主 AI 做什么 | 先形成足以支持拆分的交互流程、原型或技术方案，再拆出纵向用户结果、依赖顺序、工程任务和上线任务 |
| 内部顺序 | 关键用户流程/必要原型 → 技术与数据边界 → 纵向 Backlog → 发布门与排序 → Git/分支/CI 基线 |
| 拆分原则 | 不按“前端一项、后端一项、数据库一项”横切；每个 MVP 项应产生可独立观察和验收的交付结果 |
| 读取文件 | 四个根文件；`mvp-backlog-template.md`；`.gitignore`；现有仓库文件；必要时读取官方技术文档 |
| 写入文件 | 从模板创建 `docs/MVP_BACKLOG.md`；当前阶段写 `PLAN.md`；重要取舍写 `DECISIONS.md` 或 ADR；必要原型/规格另建专题文件；创建经人类负责人批准的初始 Git 提交 |
| 脚本/门禁 | `workflow_check.py start/manual`；核心流程无法验收、Backlog 未获人类负责人确认、分支/CI 策略未明确或没有有效 `HEAD` 时，不能领取第一个代码任务 |
| 结束产物 | 可验证方案、人类负责人确认的完整 Backlog、第一项 `ready` 任务、可审计 Git/分支基线和发布准备计划 |

### 第 4 步：建立并授权单个任务

| 项目 | 内容 |
| --- | --- |
| 参与者 | 人类负责人 + 主 AI Coordinator；调用 `$orchestrate-project-task` |
| 人类负责人做什么 | 通常确认主 AI 从 Backlog 选择的下一项；例外任务由人类负责人直接提出或确认优先原因 |
| Coordinator 做什么 | 确认不存在活动任务后，从最新主分支基线创建任务分支/隔离工作树；选择一个 `ready` 项并标记 `active`；定义范围、风险、验收和证据；选择 small、medium 或 large |
| 读取文件 | 四个根文件；`docs/MVP_BACKLOG.md`；相关代码/测试；Orchestrator Skill；任务记录模板 |
| 写入文件 | 任务记录的 `source` 绑定 Backlog ID 或例外来源；创建 `.agent/runs/<task-id>.json` 和 `.agent/active-task`；更新 Backlog 的状态与任务记录链接；medium 任务同步 `PLAN.md` |
| 脚本/门禁 | `workflow_check.py preflight <record>` 校验任务来源、授权、基线、范围、计划、风险批准和待验收项；`.agent/active-task` 存在时不得领取另一项 |
| 结束产物 | 一份经过授权、可独立理解和可机械检查的任务合同 |

### 第 5 步：Developer 探索与执行计划

| 项目 | 内容 |
| --- | --- |
| 参与者 | 主 AI Coordinator 委派 `.codex/agents/developer.toml`；Developer 使用 `$implement-project-task`；按名调用不可用时将完整 TOML 指令注入通用子代理并记录降级 |
| 人类负责人做什么 | 只处理 Developer 新发现且会改变范围、安全或不可逆选择的问题 |
| Developer 做什么 | 先定位入口、数据流、调用方、测试和仓库惯例，再确定实现步骤；此阶段不应边猜边写 |
| 读取文件 | 四个根文件、V2 当前步骤、`docs/MVP_BACKLOG.md`、任务记录、相关代码/测试、Developer Skill；large 任务读取 ExecPlan 模板 |
| 写入文件 | small/medium 步骤写任务记录；medium 同步 `PLAN.md`；large 创建并维护 `.agent/plans/<date>-<slug>.md` |
| 脚本/门禁 | 再次运行 `preflight`；计划必须写明文件、行为、验证、风险和恢复，large/high-risk 计划需要对应批准来源 |
| 结束产物 | 另一个 AI 可以接手的执行计划；仍未越过未批准边界 |

### 第 6 步：Developer 实现与自验证

| 项目 | 内容 |
| --- | --- |
| 参与者 | Developer 是唯一写代码 Agent；Coordinator 不同时修改工作树 |
| 人类负责人做什么 | 不需要逐行指挥；只回应被升级的范围或风险问题 |
| Developer 做什么 | 做最小完整纵向功能，同时写测试；检查正常、错误、权限、隐私、兼容、回滚和部分失败路径 |
| 读取文件 | Backlog 来源项、任务记录、活动 ExecPlan、项目事实、决策、相关代码/测试和仓库命令 |
| 写入文件 | 交付代码、测试、配置和必要文档；更新任务记录中的命令结果、验收证据、handoff 和 Developer worktree hash |
| 不得写入 | 未经流程批准，不因自己的流程提案修改 `AGENTS.md`、Skills、Hook、工作流脚本或 CI；不填写 Reviewer 字段 |
| 脚本/门禁 | 项目测试、lint、类型检查、构建、真实流程和安全检查；最后运行 `snapshot <task-record>`。哈希覆盖相对授权基线的完整交付变化，但排除 `.agent/`、`PLAN.md` 和 Backlog 等可变流程状态 |
| 结束产物 | 可运行、可审查的工作树，以及包含证据、假设、风险、审查重点和流程问题候选的 Developer handoff |

### 第 7 步：Reviewer 独立审查

| 项目 | 内容 |
| --- | --- |
| 参与者 | Coordinator 委派默认只读的 `.codex/agents/reviewer.toml`；Reviewer 使用 `$review-project-change`；按名调用不可用时使用注入完整指令的通用只读子代理并记录降级 |
| 人类负责人做什么 | 通常等待；仅在需求含义或风险接受出现争议时裁决 |
| Reviewer 做什么 | 不依赖 Developer 结论，重新从需求构造检查表；检查完整 diff、调用方、失败路径、安全、隐私、回归和测试 |
| 读取文件 | 用户原始请求、四个根文件、Backlog 来源项、任务记录、基础版本、当前 diff、Developer 哈希与证据、`review-checklist.md` |
| 写入文件 | Reviewer 保持只读，只返回 findings、需求检查表、审查哈希、测试缺口、结论和流程提案候选；Coordinator 原样写入任务记录 review 字段。父会话实时权限可能覆盖子代理默认值，因此本地记录不是信任根 |
| 脚本/门禁 | `workflow_check.py snapshot <task-record>` 必须与 Developer 哈希一致；P0/P1 阻塞，P2 必须修复或由人类负责人明确接受 |
| 结束产物 | `pass` 或 `changes_requested`，并给出具体文件、失败场景、影响和修复要求 |

### 第 8 步：修复、复审与人工验收

| 项目 | 内容 |
| --- | --- |
| 参与者 | Coordinator 调度 Developer 与 Reviewer；人类负责人负责验收和风险决定 |
| 人类负责人做什么 | 检查可观察结果；批准或拒绝 P2 风险、高风险操作、敏感数据和生产发布 |
| AI 做什么 | Developer 修复 P0/P1/P2；每次改动后 Reviewer 对新哈希重新独立审查，不能只看旧问题所在行 |
| 读取文件 | 任务记录、最新 diff、原始验收条件、Reviewer findings、`PROJECT.md` 和相关决定 |
| 写入文件 | 修复代码和测试；Coordinator 更新验收证据、review 结果、风险接受与 `human_approvals` |
| 脚本/门禁 | 重跑测试、真实流程和 `snapshot <task-record>`；验收项缺证据、审查哈希过期或风险未批准时不得进入关闭 |
| 结束产物 | 同一最终哈希上的实现证据、Reviewer pass 和所需人工批准 |

### 第 9 步：流程复盘与 Rule Proposal

| 项目 | 内容 |
| --- | --- |
| 参与者 | Developer、Reviewer 提供候选；Coordinator 复盘和归类；人类负责人决定永久规则 |
| 人类负责人做什么 | 对永久规则选择批准实施、拒绝或延期；AI 不能代替人类负责人批准自己的提案 |
| Coordinator 做什么 | 检查 handoff、review、失败命令、用户纠正和任务历史；回答是否重复、是否缺指导、是否可机械化 |
| 读取文件 | 任务记录、Developer/Reviewer 输出、`rule-proposal-template.json`、现有 `AGENTS.md`、Skills、脚本、Hook 和 `DECISIONS.md` |
| 写入文件 | `process_retrospective` 和 `rule_proposals` 写任务记录；一次性问题记 `task-only`；永久规则默认建立后续任务。若用户批准在当前任务实施，则修改后必须重新 snapshot、Developer 验证和 Reviewer 审查 |
| 同步要求 | 若完整流程发生变化，同时更新本文件；若 Agent 界面或默认提示变化，更新对应 `openai.yaml` 或 Agent TOML |
| 脚本/门禁 | 最终 gate 拒绝未复盘、遗留 `proposed`、AI 自批永久规则、或声称实施但没有 `implemented_in` 的任务 |
| 结束产物 | 每项流程问题都有证据、目标载体、决策人和最终处置 |

### 第 10 步：机械门禁与任务关闭

| 项目 | 内容 |
| --- | --- |
| 参与者 | Coordinator 收口；`.codex/hooks.json` 和两个 Python 脚本执行机械检查 |
| 人类负责人做什么 | 对仍需人工承担的风险作最后决定；不接受“测试没跑但应该没问题” |
| Coordinator 做什么 | 捕获最终哈希，确认 Reviewer 审查同一哈希，完成验收、批准、复盘和提案处置，然后把任务记录标记为“实现已验证” |
| 读取文件 | 四个根文件、任务记录、代码/测试、所有验证输出和 Reviewer 结论 |
| 写入文件 | 更新任务记录状态和证据；更新 `PLAN.md`；事实变化才改 `PROJECT.md`；决定或纠错才改 `DECISIONS.md` |
| 脚本/门禁 | `workflow_check.py gate .agent/runs/<task-id>.json`；Stop Hook 对 `planned/authorized/in_progress` 只提醒并允许暂停，只有 `completed` 才运行 gate，失败时续写一次要求修复 |
| 状态含义 | 任务记录 JSON 的 `completed` 只表示本地实现、证据、审查和复盘已完成；不表示已合并或已上线 |
| 状态 | gate 通过后把 Backlog 项改为 `verified`，但保留 `.agent/active-task` 直到合并；尚不能标记 `done` |
| 结束产物 | 一个证据完整、等待 PR/CI/合并的变更；单项验证通过不代表已进入主分支，更不代表 MVP 完成 |

### 第 11 步：合并、循环、上线与观察

| 项目 | 内容 |
| --- | --- |
| 参与者 | 人类负责人掌握合并和发布权；主 AI 协助；受保护 CI/分支审批在工程阶段建立 |
| 合并是什么意思 | 将第 10 步验证过的任务提交通过 PR 和 CI 纳入受保护主分支；不是把文件随意拼接，也不等于立即发布生产 |
| 人类负责人做什么 | 审批 PR 合并；当所有发布门满足后再单独审批正式发布和风险 |
| AI 做什么 | 将未经内容修改的已验证 diff 建成提交/PR，核对 CI；合并后记录 commit/PR 并把 Backlog 项改为 `done` |
| 冲突处理 | rebase、冲突解决或 CI 修复只要改变代码内容，就把任务恢复为 `in_progress`，重新验证完整基线差异、Reviewer 审查和 gate |
| CI bootstrap | 建立 CI/分支保护的首个 OPS 任务可以在 CI 尚不存在时，凭本地 gate、独立 Reviewer 和人类负责人明确批准合并；例外必须写入人工批准，CI 建立后不得继续绕过 |
| 循环判断 | 仍有 Must/已批准的 `ready` 项：从最新主分支回到第 4 步；所有 Must 和发布门完成：继续部署 MVP |
| 上线与观察 | 检查部署、迁移、回滚、监控、告警和生产验证；故障或反馈形成新的 Backlog/例外任务，不由 AI 自行扩大产品 |
| 读取文件 | `docs/MVP_BACKLOG.md`、`PROJECT.md` 的上线底线、`PLAN.md`、`DECISIONS.md`、发布配置、CI 结果、监控和任务记录 |
| 写入文件 | Backlog 写入 `done` 和集成证据，随后删除 `.agent/active-task`；当前事实写 `PROJECT.md`；下一阶段写 `PLAN.md`；事故和关键取舍写 `DECISIONS.md` |
| 门禁 | 本地 task record 不是信任根；正式上线依赖受保护 CI、分支保护、独立 PR 审批、生产权限和可追溯日志 |
| 结束产物 | 已合并的一项任务并继续循环，或已发布且可观察、可回滚的 MVP |

## 文件汇总

### 四个根治理文件

| 文件 | 唯一职责 | 出现步骤 |
| --- | --- | --- |
| `PROJECT.md` | 保存人类负责人已确认的当前项目事实、边界、待确认项和交付底线 | 共同入口；1、2、3、4、5、6、7、8、10、11 |
| `PLAN.md` | 保存当前阶段、最多七项当前任务、阶段门、下一步和证据 | 共同入口；1、2、3、4、5、8、10、11 |
| `AGENTS.md` | 保存所有 AI 必须遵守的永久行为协议、角色边界和完成禁区 | 共同入口；1 至 11，尤其 4、5、6、7、9、10 |
| `DECISIONS.md` | 保存重要选择为何形成、替代关系和值得防复发的纠错 | 共同入口；1、2、3、4、5、7、9、10、11 |

### 人类流程说明

| 文件 | 唯一职责 | 出现步骤 |
| --- | --- | --- |
| `docs/WORKFLOW_V2.md` | 从人类负责人视角解释完整顺序、每步文件用法、代理与门禁；不保存具体项目事实 | 共同入口；1 至 11 |

### Skills 与模板

| 文件 | 唯一职责 | 出现步骤 |
| --- | --- | --- |
| `.agents/skills/orchestrate-project-task/SKILL.md` | Coordinator 建任务、委派、复审、复盘、gate 和集成交接的详细程序 | 4、7、8、9、10、11 |
| `.agents/skills/orchestrate-project-task/agents/openai.yaml` | Orchestrator Skill 的显示名、默认提示和禁止隐式调用设置 | 4 |
| `.agents/skills/orchestrate-project-task/references/task-record-template.json` | 新任务机器记录的标准字段模板 | 4 |
| `.agents/skills/orchestrate-project-task/references/rule-proposal-template.json` | 流程问题提案、证据、目标、决策和实施位置的标准模板 | 9 |
| `.agents/skills/orchestrate-project-task/references/mvp-backlog-template.md` | 把完整 MVP 拆成有优先级、依赖、状态和发布门的纵向任务模板 | 3、4、10、11 |
| `.agents/skills/implement-project-task/SKILL.md` | Developer 的开工门禁、探索、计划、实现、验证和交接程序 | 5、6、8、9 |
| `.agents/skills/implement-project-task/agents/openai.yaml` | Developer Skill 的显示名、默认提示和禁止隐式调用设置 | 5、6 |
| `.agents/skills/implement-project-task/references/exec-plan-template.md` | large/high-risk 任务可恢复、可验证的长计划模板 | 5、6、8、10 |
| `.agents/skills/review-project-change/SKILL.md` | Reviewer 独立重建需求、检查 diff、分级发现和复审程序 | 7、8、9 |
| `.agents/skills/review-project-change/agents/openai.yaml` | Reviewer Skill 的显示名、默认提示和禁止隐式调用设置 | 7 |
| `.agents/skills/review-project-change/references/review-checklist.md` | 按需求、安全、隐私、可靠性、兼容和测试触发审查 | 7、8 |

### 子代理、Hook 与脚本

| 文件 | 唯一职责 | 出现步骤 |
| --- | --- | --- |
| `.codex/agents/developer.toml` | 定义可写 Developer 的身份、边界、Skill 和交接要求 | 5、6、8、9 |
| `.codex/agents/reviewer.toml` | 定义只读 Reviewer 的身份、独立性、Skill 和报告要求 | 7、8、9 |
| `.codex/hooks.json` | 在受信任项目中执行 SessionStart 与 Stop 检查，包含 Unix/Windows 命令；首次或更新后仍需用户信任 | 共同入口；10、11 |
| `scripts/workflow_check.py` | 严格解析模式和任务记录；检查治理配置、Git 可提交文件中的秘密、来源、授权、验收、哈希、审查、批准和复盘 | 共同入口；1 至 11 |
| `scripts/codex_stop_hook.py` | 进行中允许暂停；`completed` 才执行 gate；通过但未合并时提醒只能报告 `verified` | 10、11 |
| `.gitignore` | 排除密钥、日志、缓存和本机文件，避免污染版本库或泄露 | 3、6、11 |

### 运行时与后续产生的文件

| 文件/目录 | 唯一职责 | 出现步骤 |
| --- | --- | --- |
| `.agent/active-task` | 指向唯一活动任务；从领取任务保留到合并，避免集成修复丢失上下文 | 4 至 11 |
| `.agent/runs/<task-id>.json` | 保存任务来源、授权、范围、计划、验收、命令证据、哈希、审查、人工批准和复盘 | 4 至 11 |
| `.agent/plans/<date>-<slug>.md` | 保存 large/high-risk 任务的可恢复 ExecPlan | 5、6、8、10 |
| `.codex-log/last-session-check.json` | SessionStart Hook 成功运行的本机心跳，用于确认自动检查确实生效 | 共同入口 |
| `.codex/workflow-v2-install.json` | 保存安装版本和受管文件哈希，支持幂等安装与安全升级 | 安装/升级 |
| `docs/MVP_BACKLOG.md` | 保存整个 MVP 的任务拆分、优先级、依赖、状态、任务记录链接和发布门；目标定义确认后在第 3 步创建 | 3、4、8、10、11 |
| 交付代码与测试 | 实现目标行为并提供自动回归证据；目录在技术方案确认后确定 | 6、7、8、10、11 |
| CI/部署/监控配置 | 在远端强制测试、审批、发布、回滚和生产可观察性；当前尚未建立 | 3、9、10、11 |
| `docs/decisions/` 或 ADR | 当决策表过长或技术取舍复杂时保存详细背景 | 3、9、11 |

## 使用规则

1. 人类负责人日常只需要先看本文件当前步骤，再看 `PROJECT.md` 和 `PLAN.md` 首屏；不必通读所有 Skill。
2. 主 AI 必须按共同入口读取权威文件，再根据当前步骤调用 Skill、子代理和脚本。
3. Developer 与 Reviewer 只读与自己任务相关的细节，但都要读取四个根文件和任务记录。
4. 本文件不保存任务状态；目标版本的全局状态看 `docs/MVP_BACKLOG.md`，当前阶段看 `PLAN.md`，单项执行状态看活动任务记录。
5. 流程发生永久变化时，经人类负责人确认后同步 `AGENTS.md`、对应 Skill/脚本、本文件和 `DECISIONS.md`，避免只改说明不改执行。
6. 永久治理文件属于交付哈希的一部分；当前任务内修改后必须重新验证和独立审查，不能靠排除哈希绕过。

<!-- WORKFLOW_DOC_END -->

## Codex 依据

本包采用 Codex 的仓库级 `AGENTS.md`、Skills、Custom Agents 和生命周期 Hooks。根据当前官方文档，项目级 `.codex/` 只在 trusted 项目中加载；非托管 Hook 首次或变更后必须重新信任；Stop 的 `decision: "block"` 会创建一次续写提示；父会话实时权限覆盖可能应用到子代理。

- https://developers.openai.com/codex/guides/agents-md
- https://developers.openai.com/codex/skills
- https://developers.openai.com/codex/subagents
- https://developers.openai.com/codex/hooks
- https://developers.openai.com/codex/config-reference
