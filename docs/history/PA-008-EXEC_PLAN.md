# PA-008 执行规划：产品优先 STATUS 与 Stop Hook

> 本文件是 PA-008 的实施规划，防止会话丢失。Phase A 规范真相源仍是根目录 `V4_PHASEA_PLAN.md`（Plan ID `V4-PHASEA` revision `1`）。本文件不改验收语义，不新增命令或主状态。

## 0. 授权绑定

| 字段 | 值 |
| --- | --- |
| 任务 | PA-008 |
| 里程碑 | G-A3 人类与兼容面（本任务只做 STATUS / Stop Hook） |
| 人类授权 | 2026-08-24 用户选择 `1`：接受 M2 Review PASS，开始 PA-008 |
| M2 封口提交 | `9f91d9362a93a8d805687001e7cd5c6a1e9fa077` |
| 父提交 | `f4a9a06bbc1d016002cb36c15e1dde1af61e5a5f` |
| 计划指纹 | `4a4a0eddb63c8d7a7c593235d2cd9e66c0fe8f0f97414e4014168bf47b7acbd8` |
| 当前禁止 | PA-009–PA-012；新 decision 命令；新主状态机；安装升级；Skills/文档大同步；真实 closeout |

## 1. 目标

让人类不读 JSON、lane、generation、queue 也能看到：

1. 当前核心结果
2. 当前 focus core slice
3. 可观察入口与步骤
4. 真实部分 vs 临时/模拟/延期
5. 本次实质变化
6. 当前 blocking decisions
7. 下一条安全动作

STATUS 和 Stop Hook **只派生展示**，不写入决定、不改生命周期、不批准、不集成。

## 2. 现状（必须从这里改，不能另起一套）

- `workflow_status_snapshot` / `render_workflow_status` 只汇总 Requirements、Backlog 计数、task `status/phase/verification/integration`。人类摘要不能回答“现在能试什么、哪里是临时的、还等哪个决定”。
- `codex_stop_hook.py` 只按 V3 生命周期提示（in_progress / gate / verified / queued / closeout）。V4 的 `decision_log`、checkpoint、observation 入口不会出现。
- `tests/test_stop_hook.py` 只有 coordinator 无 lane 与坏 pointer。没有产品下一动作、没有四类观察入口、没有脱敏负例。

设计输入：`改进建议.md` 第 13、15、23 节。实施范围以 `V4_PHASEA_PLAN.md` PA-008 卡片为准。

## 3. 允许修改的路径

| 路径 | 职责 |
| --- | --- |
| `payload/.codex-workflow/bin/workflow_common.py` | 只读派生产品摘要、决策卡片字段、红线脱敏；`render_workflow_status` 人类区改成产品优先，技术细节折叠 |
| `payload/.codex-workflow/bin/codex_stop_hook.py` | 复用同一派生函数，输出**一条**可执行下一动作；不写文件、不调 apply、不跑 integration |
| `tests/test_stop_hook.py` | V4 下一动作、不批准/不改状态、脱敏 |
| `tests/test_v4_workflow.py` 或新建 `tests/test_v4_status.py` | STATUS 人类摘要顺序、四类 fixture、V3 回归 |

禁止：改 `workflow_lane.py` 承载产品语义；新增 checkpoint 命令；改 Schema；改 closeout；改 install。

## 4. 派生规则（只读 record / Brief / 架构基线）

对每个 V4 task 从现有字段派生，禁止人类同步维护第二份文字：

| 人类字段 | 来源 |
| --- | --- |
| 当前核心结果 | `delivery_contract` / 当前 focus 的 acceptance 可观察结果 |
| 当前焦点 | `delivery_contract.focus_slice_id`；supporting 不得把 STATUS 写成“核心已完成” |
| 可观察入口 | 当前 observation recipe / receipt：UI preview、API、CLI、data proof 四类之一；只引用有效入口，不保存可变 runtime URL |
| 真实与临时 | receipt / `known_placeholders` / exploratory vs formal |
| 本次实质变化 | 最近 delivery 的 path_classes / contract_diff 人类可述部分 |
| 产品方向 | 当前 product_checkpoint：`awaiting_human` / `accepted` / `changes_requested` / `not_required` / `deferred` / `stopped` |
| 待决定 | `decision_log` 中 derived blocking 且未 resolve 的 ID；卡片含 2–3 个已有 options，不发明新 option |
| 架构与护栏 | live architecture baseline + 切片护栏是否通过（只读 checker 结果语义，不在 Hook 里 apply） |
| 技术交付 | 折叠区：status / phase / verification / integration；默认隐藏 fingerprint、generation、lease、queue |

V3 task：保持现有技术摘要，不伪造 focus/checkpoint。STATUS 必须同时可读 V3 与 V4，互不改写历史。

## 5. Stop Hook 合同

输入：现有 stdin event（`cwd`、`stop_hook_active`）。

输出：现有 JSON 形状（`continue` / `systemMessage` 或一次 `decision: block`）。

下一动作必须是**单一**可执行句，优先顺序：

1. 有 blocking open decision → 等待 `HD-xxx`（带 decision id）
2. required checkpoint 缺当前 receipt / 方向确认 → 先观察再决定
3. `changes_requested` → 回到 Developer，不要报 verified/done
4. 否则沿用现有 V3 生命周期提示（gate / mark-verified / prepare-integration / queue / closeout / release）

硬约束：

- 不调用 `record-decision` / `record-review` / `queue` / `confirm-closeout` / git push
- 不把 `stop_hook_active` 二次 block 变成批准
- 输出中不得出现 token、cookie、PII、秘密、签名 query、JWT

## 6. 脱敏

对 STATUS 人类区、折叠区引用的入口、Stop Hook 文本走与 observation receipt 相同的敏感检测（已有 `_validate_v4_redacted_receipt` 规则：JWT / email / phone / secret / token prefix / 敏感赋值 / 签名 URL）。命中则显示受控引用或省略，不把原文写进 STATUS/Hook。

## 7. 实施切片（串行，每片可独立 Review）

1. **派生 helper**：`workflow_common.py` 增加纯函数，从一条 V4 record 得到产品摘要 dict；V3 返回“无产品卡片，仅技术状态”。零 I/O 单测。
2. **STATUS 渲染**：人类区按第 4 节 1–8 再技术折叠；JSON 区块仍是机器真相，字段可追加派生摘要但不删除现有 snapshot 键（避免无故打破 `status_fingerprint` 消费者时，在测试中钉死新旧键）。
3. **Stop Hook**：同一 helper；lane pointer 存在时优先产品下一动作，否则保持 coordinator 无 lane 行为。
4. **四类 fixture**：UI / API / CLI / data 各一条，断言单一下一动作且无敏感泄漏。
5. **回归**：现有 `test_stop_hook`、STATUS sync、V3 lane Stop Hook 文案、doctor/status 指纹仍通过。

## 8. 验收清单（对照 PA-008 卡片）

- [ ] 人类摘要先于技术细节
- [ ] 出现 focus、入口、真实/临时、实质变化、blocking decisions
- [ ] supporting 完成不会宣称核心完成
- [ ] Stop Hook 给出精确 decision ID 或下一命令，且不批准、不改状态、不集成
- [ ] UI/API/CLI/data 四类 fixture 各生成单一可执行下一动作
- [ ] 不泄露 token / cookie / PII / 签名 URL
- [ ] V3 任务与 V3 测试不回退
- [ ] 独立 Review PASS 后才允许谈 PA-009

## 9. 明确不做

- 不改 `decision_log` Schema，不新增 state 子命令
- 不把 Backlog 新列、WIP 硬限制做成机械门禁（Phase B）
- 不在 STATUS 里启动服务或写入 preview 地址
- 不把 Evidence Contract v1 / 多 Python / Windows 矩阵提前到本任务（PA-012）
- 不把 `problem.md` 或授权脚本混进交付

## 10. 建议的下一步

规划已落盘。开始写代码前只需人类再说一次「开始实现 PA-008」。实现时只改第 3 节路径，完成后待命独立 Review。
