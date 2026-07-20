# Codex Workflow V4 阶段 0 试运行记录

> 本文件是阶段 0 的人类可读工作记录，不是新的工作流真相源，不替代 V3 task record、Reviewer、gate、integration 或 closeout，也不证明已经具备 V4 的机械防绕过能力。

## 1. 当前状态

- 状态：`phase0_passed`
- 正式落账日期：`2026-07-20`
- 当前产品证据：`MVP-DOC-001` 已完成 snapshot-bound Review、gate、pilot integration 和 closeout，封存产品树已正式集成到本地 `v4`
- 生命周期真相源：精确 snapshot、Review、gate、integration 和 closeout 状态读取隔离 V3 Pilot 的 `MVP-DOC-001` task record、closeout bundle、持久化 manifest 和目标 ref；本文件只记录阶段判定
- Phase A：`standby / not_authorized`；V4 Schema、V4 task record、decision 命令、强制 product checkpoint 和强制 gate 均未开始
- 已确认：隔离 self-hosting pilot、`DOC-001 doctor`、会话未观察时采用 `0 + WARN`
- 阶段门：task record、closeout bundle、持久化 manifest 与目标 ref 已共同证明退出条件；核验材料见第 8 节

已批准的精确 Requirements 基线：

- Brief：`REQ-V4-PILOT-001`
- Revision：`1`
- Target release：`V4-PHASE0`
- Fingerprint：`ae131690e43ea04013b63ab438b7cccf33e45d48550cfa694f4f091bf641f8a2`（已批准并通过 gate）

## 2. V3 基线

基线采集时间：2026-07-13 11:10:03 +08:00

| 项目 | 基线事实 |
| --- | --- |
| 源仓库 | `E:\Github\codex-workflow-v2_-` |
| 分支 | `v4`，跟踪 `origin/v4` |
| 提交 | `671f8b4b87d29e39beea0ff089dcd93f9e4cd0ac` |
| 本地/远端差异 | `0/0` |
| 工作区 | 基线验证前干净 |
| 完整验证命令 | `python -B verify_package.py` |
| 完整验证结果 | `PACKAGE_OK (Ran 48 tests in 268.761s)` |

这组结果只锁定 V3 包在试运行前的已有行为。它不是阶段 0 的真实产品证据，也不能替代后续的人类产品观察。

## 3. 为什么不能直接在当前 checkout 完成阶段 0

当前仓库根目录没有已安装工作流的 `.codex-workflow`，只有供安装器复制的 `payload/.codex-workflow`。安装器还明确拒绝把当前包安装到包目录自身。因此，当前 checkout 可以保存最终 V4 改动，却不能单独提供阶段 0 要求的 V3 Developer、Reviewer、gate、integration 和 closeout 外环。

严格试运行需要以下二者之一：

1. 一个已经安装 V3 的真实产品项目；或
2. 当前包目录之外的 self-hosting pilot clone，在其中建立独立 V3 基线并试运行真实的工作流产品功能。

合成 fixture 只能补测真实功能没有自然触发的决定阻断或架构越界，不能替代完整试运行。

## 4. 推荐试点卡片

### 4.1 唯一焦点结果

候选 ID：`DOC-001`

候选结果：用户执行一条只读 `doctor` 命令，即可分别看清：

- 已安装的工作流包是否可用；
- 项目治理文件是否有效；
- Hook 是否已经正确配置；
- 当前 worktree 是否存在有效的最近启动观察记录，并明确它不证明当前会话身份；
- 此刻唯一建议的下一步是什么。

用户不需要阅读 JSON、fingerprint、lane、lease 或安装 manifest。

### 4.2 最小观察方式

在确认后的 V3 pilot 项目中只运行一条命令：

```text
python .codex-workflow/bin/workflow_check.py doctor
```

最小观察路径：

1. fresh install、尚无启动观察时运行命令；
2. 确认包、治理、Hook 配置和启动观察记录被分开陈述；
3. 若记录缺失，检查并信任项目 Hooks，启动或恢复新的 Codex 会话后在该会话重放；不得手工运行 `start` 伪造 Hook 执行证明；
4. 验证状态变化只反映观察事实，没有写入 tracked 文件；
5. 分别验证 package-owned 文件损坏和合法 project-owned 治理修改；
6. 确认输出始终给出唯一、可执行的下一步。

### 4.3 真实、临时与延后部分

真实部分：

- 对已安装 V3 包、治理、Hook 配置和启动观察进行只读诊断；
- 人类可直接执行的命令和输出；
- 真实安装项目中的观察结果。

临时部分：

- 阶段 0 的人工产品确认及证据记录；
- 尚未由 V4 Schema 或 gate 强制的决定卡片；
- 若真实功能未自然触发决定或越界，使用的受控 fixture。

明确延后：

- task-record-v4；
- `delivery_contract` 和 `decision_log`；
- `request-decision` / `record-decision`；
- contract/observation fingerprint；
- product checkpoint 强制门禁；
- STATUS 和 Stop Hook 的完整 V4 产品摘要。

## 5. 待人类确认的实质决定

### D-001：试点环境和焦点功能

影响：决定阶段 0 的证据是否真实、是否能复用完整 V3 外环，以及是否会触碰现有产品仓库。

| 选项 | 说明 | 主要代价 |
| --- | --- | --- |
| A（推荐） | 在当前包目录外建立隔离的 self-hosting pilot clone，安装 V3，试运行 `DOC-001 doctor`，验证后再把确认过的改动带回 `v4` | 需要维护一个临时 pilot 环境，但不迁移现有产品仓库 |
| B | 选择并迁移一个现有真实产品项目到 V3，再在该项目开发一个产品功能 | 真实性最高，但会扩大范围并引入迁移风险 |
| C | 不做阶段 0，直接实现阶段 A | 与最新版改进建议冲突，不接受 |

建议：选择 A。

决定：用户于 2026-07-13 回复“确认 A/A”，选择 A。

### D-002：启动观察记录缺失时的退出语义

影响：同时影响人工理解和脚本调用；实现前必须决定。

| 选项 | 行为 | 权衡 |
| --- | --- | --- |
| A（推荐） | 包、治理和 Hook 配置均有效、仅缺少启动观察记录时返回 `0 + WARN`，并给出检查/信任 Hooks、启动或恢复新会话后重放的下一步 | 不把观察记录缺失误报为包损坏；自动化需要读取分项状态 |
| B | 启动观察记录缺失就返回非零 | 自动化更严格，但容易让用户误以为需要重装，混淆“可用”与“存在启动观察记录” |

建议：选择 A；只有包、治理或 Hook 配置真正无效时返回非零。

决定：用户于 2026-07-13 回复“确认 A/A”，选择 A。

### D-003：第四轮 Review 后的处理方式

影响：决定是否继续扩大修复范围，以及当前提交能否被误解为已经通过 Reviewer 或阶段 0。

决定：用户于 2026-07-13 明确要求“不进行修复，对当前状态提交commit，然后提交到V4分支”。

执行口径：

1. 不继续修改 `doctor` 实现或测试来处理第四轮 Review 的 P1/P2。
2. 在 pilot task record 中保留 `changes_requested`、generation 9 和精确 snapshot 绑定，不运行 gate、integration 或 closeout。
3. 只把 delivery `431c2ca...` 的两个产品路径压成一个本地主仓库 `v4` 检查点提交；pilot 治理、lane 和 task record 不进入主仓库。
4. 检查点不等于 Reviewer pass、`verified`、`done` 或 `phase0_passed`。

## 6. 已知架构边界

`DOC-001` 在阶段 0 中必须遵守：

1. `doctor` 严格只读；命令前后 tracked 文件和 runtime 状态不得变化。
2. 包完整性、治理有效性、Hook 配置和启动观察记录必须分别报告，不能折叠成单一“健康/不健康”；Hook configured 不等于 Hook 已执行。
3. 缺少 heartbeat 不等于需要重装。
4. 历史 heartbeat 不得表述成“当前会话已可信”。
5. 不输出 token、lease 或其他运行时敏感数据。
6. 不修改 V3 Schema、task record、STATUS、lane、remote claim、queue、integration 或 closeout。
7. 不新增阶段 A 的 V4 字段和命令。
8. package-owned 文件损坏必须失败；合法的 project-owned 治理修改不得被误报为 package drift。
9. `workflow_check.py` 自身或启动必需模块无法加载属于 bootstrap failure；doctor 的 package 检查从命令成功启动后开始。
10. 普通 Python 命令运行 doctor 也不得生成 `__pycache__`、pyc 或其他 runtime 写入。

其中第 2、3、4 条将作为受控架构越界检查的目标，且必须在 Reviewer 或 integration 前发现。

## 7. 阶段 0 证据日志

以下表格只记录事件与人类可读结论；V3 的技术状态继续由现有真相源管理。

| 序号 | 事件 | 证据 | 结论 |
| --- | --- | --- | --- |
| E-001 | 冻结试运行前 V3 包基线 | `python -B verify_package.py`；48 tests；268.761s；exit 0 | 通过，仅证明 V3 基线 |
| E-002 | 确认当前包 checkout 不具备已安装 V3 外环 | 根目录无 `.codex-workflow`；安装器拒绝包内目标 | 必须使用外部 pilot |
| E-003 | 人类确认试点、焦点与退出语义 | 用户回复“确认 A/A” | 已完成 |
| E-003A | 建立外部 self-hosting pilot 与 V3 基线 | `E:\Github\codex-workflow-v4-pilot`；`c280393 chore: establish V3 pilot baseline` | 已完成 |
| E-003B | 生成初版 Requirements snapshot | `REQ-V4-PILOT-001` revision 1；`414d8c99dce2a5d39ab7c17784ff417bb5f3c3d38a1f9f1ce1c4e209ad12a937` | 红队发现当前会话、自检 bootstrap 和阶段 0 验收过度承诺，已撤回 |
| E-003C | 红队修正后生成精确 Requirements snapshot | `REQ-V4-PILOT-001` revision 1；`5fe10ba9d75f4a189be08a13df30ef9766e3f7a3490fa4bd9d144f07b8b45f2a` | snapshot PASS；语义检查只剩预期的 approval 错误；待人类精确批准 |
| E-003D | 用户精确批准 `5fe10b...45f2a` 后首次写回 gate | 批准后修改了 Brief 中“尚未批准”的 Markdown，fingerprint 变为 `eb4a22...`；Backlog parser 也拒绝 `DOC-001` task ID | gate 正确失败；暴露批准文字自失效和 task ID 隐式约束 |
| E-003E | 修正批准稳定性和 task ID 兼容 | 批准状态只写 JSON approval；稳定正文不再随批准变化；产品 focus 保持 `DOC-001`，V3 task 改为 `MVP-DOC-001` | 新 snapshot `ae131690e43ea04013b63ab438b7cccf33e45d48550cfa694f4f091bf641f8a2` PASS；manual PASS；待人类精确批准 |
| E-003F | 用户精确批准稳定 fingerprint | 用户回复批准 `REQ-V4-PILOT-001` revision 1 / `V4-PHASE0` / `ae1316...f8a2`；approval-only 写回后 snapshot 保持一致 | requirements-gate、manual、status 全部 PASS；治理基线提交 `d8d721f` |
| E-003G | 建立并 claim V3 task/lane | task `MVP-DOC-001`；产品 focus `DOC-001`；授权提交 `92cc95f`；lane `lane-MVP-DOC-001-6ee891d0`；branch `codex/task/MVP-DOC-001-6ee891d0` | preflight PASS；Developer 写入范围仅 workflow_check.py 与 test_workflow_check.py |
| E-004 | 首次可观察结果 | delivery `781422d997e50a76459cf973ab7a422b56f6cb03`；snapshot `1f23a277c58737256f6af20e5caf984e174a9a3ce1fd04b6eff2d35f1b657bdc`；exact temp install 普通 Python 输出 `PACKAGE PASS / GOVERNANCE PASS / HOOK CONFIG PASS / STARTUP OBSERVATION WARN`、唯一 next action、exit 0、无 pycache/pyc | 已完成；核心命令可直接运行，58 tests 全量回归 PASS |
| E-004A | P1 修复后的新可观察结果 | delivery `861e932570b6cfaf7d43af565ad5b2c1768a40a0`；delivery hash `ea6a29d48d56af63fbb4c65a2549b47127558b4d6a206398a18e95b614d0302e`；snapshot `46deff6f00788a9a65707e0bda5574c756f1a180c263bfe293bda217bf784e94`；四类非法 UTF-8 由 traceback 改为对应域 `INVALID`、四域输出和唯一 next action 保持；普通 Python 正常路径仍为四域 `PASS/PASS/PASS/WARN`、exit 0、无 pycache/pyc | Developer 完成；18 focused 与 62 full tests PASS，等待人类重新观察 |
| E-004B | 第二个 P1 修复后的新可观察结果 | delivery `4510c1562797387bab422cca128bab20d5b7c25f`；delivery hash `1a2d43acddb0e8496730ff24ce24c697804fe4fdb71bc12f9debfe3cba3ecd36`；snapshot `52d94ee9f7a8c6c07de9e8fb9aa5ed3568711f72d08cb0b49ee7295b45065490`；非法 UTF-8 `layout.json` 只在 doctor discovery 边界转为四域 `UNKNOWN`、唯一 next action、非零且无 traceback，非 doctor 继续抛出；普通 Python正常路径仍为四域 `PASS/PASS/PASS/WARN`、exit 0、无 pycache/pyc | Developer 完成；19 focused 与 63 full tests PASS，等待人类重新观察 |
| E-004C | 第三轮系统性 Review 后的统一边界结果 | delivery `431c2caebadb9740e3623b70ef21910d8461b4d4`；delivery hash `660758a8ce094aa0b943485f61887790bf7718d99aab9425d4077bc9fc4aeaa3`；snapshot `5c2880111694825811958e5a32fca1e07136fd7b3379981a633e9a626ae8d415`；局部 JSON ValueError/RecursionError 映射、中央动态文本单行转义、observation containment/resolve 异常隔离已统一实现；非 doctor 与编程/import failure 仍不被吞掉；普通 Python正常路径保持四域 `PASS/PASS/PASS/WARN`、exit 0、无 pycache/pyc | Developer 完成；25 focused 与 69 full tests PASS，等待人类重新观察 |
| E-004D | 第四轮 P1/P2 的共享边界修复 | `workflow_common.py` 增加明确的 `WorkflowJSONError` 与跨 Python 一致的 640 位整数资源上限；`workflow_paths.py` 集中封装 layout JSON 和 `Path.resolve` 资源异常；doctor 删除 governance/discovery 的宽泛 `ValueError/RecursionError` 捕获；新增业务异常传播与路径边界回归测试 | Developer 完成；27 focused 与 71 full tests PASS，等待形成新 snapshot、产品观察和独立 Review |
| E-004E | 第五轮 Review findings 的全局边界修复 | layout 文件读取与 JSON 解析拆为独立边界；路径解析分别产生 `WorkflowPathOSError` 与 `WorkflowPathRuntimeError`；observation location 删除裸 `RuntimeError` 捕获并恢复 `OSError=INVALID`、`RuntimeError=UNKNOWN`；增加 640/641 位正负整数及读取/解析异常矩阵 | Developer 完成；29 focused 与 73 full tests PASS，等待形成新 snapshot、产品观察和再次独立 Review |
| E-004F | 第六轮符号链接环 P2 的跨版本修复 | 共享 resolver 先以 `strict=True` 探测真实路径错误，缺失叶路径再回退 non-strict；Python 3.9-3.12 的 loop `RuntimeError` 与 Python 3.13+ 的 `OSError(ELOOP)` 统一为 `WorkflowPathRuntimeError`；JSON 嵌套增加显式 256 层限制，消除解释器默认行为差异；加入真实 symlink loop 与 256/257 层 fixture | Developer 完成；Python 3.9/3.12/3.13 各 31 focused 与 75 full tests PASS，等待再次独立 Review |
| E-004G | Python 3.13 真实环境复核与证据同步 | 通过 `uv` 安装 CPython 3.13.14；真实双向 symlink loop 产生 `OSError(ELOOP)` 并稳定映射为 `WorkflowPathRuntimeError`；doctor observation 输出 `UNKNOWN` 且无 traceback；独立 Reviewer 复核运行时代码 PASS | Python 3.13.14 的 31 focused 与 75 full tests PASS；全仓无新增 `__pycache__`/`*.pyc`；证据文档 P3 已修正 |
| E-004H | 第八轮文档状态一致性修正 | 将第四轮 25 focused / 69 full 明确标为历史证据；临时状态更新为第八轮 finding 已修正且待复核；第四轮“不修复”决定明确标为已被后续用户授权取代的历史决定 | 当前工作树表达单一状态：findings 已修正，尚未形成新 snapshot，等待独立文档复核 |
| E-004I | 第九轮独立文档复核 | 完整复核顶部状态、E-004H、历史检查点、临时状态、历史决定、下一门禁和阶段 A 冻结条件 | `PASS`，无 P0-P3 findings；`git diff --check` PASS；Reviewer 未修改文件 |
| E-004J | 集成基线独立 Review | 发现普通 JSON syntax 与 resource 异常合并、observation 路径预处理绕过共享 resolver，以及本文档镜像可变生命周期状态 | 修复候选拆分 syntax/resource 类型，将 expanduser 与非法路径值纳入共享边界，并把实时生命周期交还 V3 task record |
| E-005 | 实质决定在固化前提出 | D-002 在实现前提出；用户选择仅缺启动观察记录时 `0 + WARN`；exact snapshot 真实行为为 WARN + exit 0 | 已完成并由真实行为验证 |
| E-006 | 架构越界在 Reviewer/integration 前发现 | 受控 fixture 将历史观察错误描述成“当前会话可信”，对应护栏测试按预期 exit 1；同时测试严格禁止 runtime/pycache 写入 | 已完成，发生在独立 Reviewer 前 |
| E-007 | 人类产品观察 | 用户于 2026-07-13 回复 `accepted`；接受对象为 delivery `781422d997e50a76459cf973ab7a422b56f6cb03` / snapshot `1f23a277c58737256f6af20e5caf984e174a9a3ce1fd04b6eff2d35f1b657bdc`；进入 Reviewer 前重新计算 snapshot 一致，exact install 重放仍为四域结果、唯一 next action、exit 0、无 pycache/pyc | 已完成；产品方向被接受，允许进入独立 Reviewer |
| E-007A | P1 修复后的再次人类产品观察 | 用户再次回复 `accepted`；接受对象为 delivery `861e932570b6cfaf7d43af565ad5b2c1768a40a0` / snapshot `46deff6f00788a9a65707e0bda5574c756f1a180c263bfe293bda217bf784e94`；进入第二轮 Reviewer 前复算 snapshot/preflight PASS，exact install 重放仍为四域结果、唯一 next action、exit 0、无 pycache/pyc | 已完成；新产品方向被接受，允许第二轮独立 Reviewer |
| E-007B | 第二个 P1 修复后的第三次人类产品观察 | 用户回复 `accepted；开始第三轮review`；接受对象为 delivery `4510c1562797387bab422cca128bab20d5b7c25f` / snapshot `52d94ee9f7a8c6c07de9e8fb9aa5ed3568711f72d08cb0b49ee7295b45065490`；进入第三轮 Reviewer 前复算 snapshot/preflight PASS，exact install 重放仍为四域结果、唯一 next action、exit 0、无 pycache/pyc | 已完成；第三个产品方向被接受，用户明确批准开始第三轮独立 Reviewer |
| E-007C | 系统性边界修复后的第四次人类产品观察 | 用户回复 `accepted`；接受对象为 delivery `431c2caebadb9740e3623b70ef21910d8461b4d4` / snapshot `5c2880111694825811958e5a32fca1e07136fd7b3379981a633e9a626ae8d415`；进入第四轮 Reviewer 前复算 snapshot/preflight PASS，统一边界矩阵 6/6 PASS，exact install 重放为四域结果、唯一 next action、exit 0、无 pycache/pyc | 已完成；第四个产品方向被接受，允许第四轮独立 Reviewer |
| E-008 | V3 Reviewer 到 closeout | 首轮 Reviewer 对 snapshot `1f23...` 以 P1 指出四域内非法 UTF-8 traceback；第二轮 Reviewer 对 `46deff...` 以 P1 指出 discovery/layout 非法 UTF-8 traceback；第三轮 Reviewer 对 `52d94e...` 系统性发现 JSON resource 异常、控制字符 action 注入与 observation path 异常，以 2 P1 + 1 P2 / `changes_requested` 写入 generation 7；第四轮 Reviewer `codex-doc001-reviewer-r4-20260713` 对 snapshot `5c2880...d415` 重跑 25 focused 与 69 full tests，均通过，但仍发现 discovery/governance 过宽捕获编程型 `ValueError/RecursionError` 的 P1，以及 package/governance/discovery path `RuntimeError` 可能绕过结构化四域输出的 P2；结论以 `changes_requested` 写入 generation 9 | 用户决定不修复；Review 状态已提交为 pilot lane `7966785`，未运行 gate/integration/closeout |
| E-009 | 负担与重复确认复盘 | 首次可观察结果前经历 pilot 安装、3 轮 Requirements fingerprint、2 次精确 fingerprint 人类批准、一次批准自失效、一次 task ID 兼容修正、task/lane 建立、focused + 多轮 full verification；四轮 Review 依次暴露 UTF-8 域边界、discovery/layout 边界、JSON/控制字符/observation path 矩阵，以及共享 JSON/path 边界中的异常归类问题，形成四轮 Developer/观察/Review 与四个被人类接受的 snapshot | 已记录；阶段 A 若恢复，必须把异常归类放到共享资源读取和路径解析的精确边界，避免按异常类别逐轮补丁与重复人类确认 |
| E-010 | 历史产品检查点提交到主 `v4` | pilot delivery `431c2caebadb9740e3623b70ef21910d8461b4d4` 的当时产品树被压成主仓库提交 `5121cc2c0a7edd1850db820cbe9f7a539ad1116a`；范围精确为 `payload/.codex-workflow/bin/workflow_check.py` 与 `tests/test_workflow_check.py` | 当时的本地 `v4` 产品检查点已完成；不包含 pilot 治理/control-plane 文件，也不改变当时的 `changes_requested` 结论 |
| E-011 | Phase 0 正式落账 | `MVP-DOC-001` generation 16 的 task record 显示 `completed / passed / review pass / integrated`；pilot closeout 提交 `9e1642b42142ddf8533894ba291e86d737319de1`；closeout bundle SHA-256 `6748928484ced15e87e194387c93b4e339b6985710f1ae64f444c045451157f1`；封存产品树正式集成提交 `2388d2231e7ac69240fc550848883545cd4d4352` 是当前 `v4` 的祖先；P3 证据范围修正已持久化 | 阶段 0 退出条件已满足，状态记录为 `phase0_passed`；Phase A 进入待命且未获实施授权 |

### 历史产品检查点（第四轮后，当时带已知审查问题）

直接执行：

```powershell
Set-Location C:\Users\31286\AppData\Local\Temp\codex-v4-doc001-exact-431c2ca-20260713a
python .codex-workflow\bin\workflow_check.py doctor
```

真实工作：exact delivery 安装后的四域只读诊断、固定优先级唯一下一步、缺启动观察记录时 `0 + WARN`、普通 Python 不写 runtime/字节码缓存；中央渲染保证已覆盖的动态文本不能伪造域标题或第二个 action。历史证据：第四轮 Review 对当时 snapshot 运行 25 个 focused tests 和 69 个完整包测试，均通过。

临时部分：尚无真实 SessionStart 记录；Pilot 路径、snapshot、Review、gate、integration 与 closeout 属于外部控制面状态，由 `MVP-DOC-001` task record 保存。

当前限制：manifest 只证明合作式安装记录一致性；Hook configured 不证明 Hook 已执行；历史启动记录不证明当前/唯一会话；doctor 自身或启动依赖无法加载属于外部 bootstrap failure。

后续共享边界修复将普通 JSON syntax、显式 resource limit 与编程型异常分开传播；doctor 涉及的 manifest、governance、Hooks、observation 和 layout JSON 在各自资源边界转换为明确异常。共享路径解析同时覆盖 expanduser、非法路径值、操作系统错误、运行时解析错误和 `ELOOP` 差异。封存交付的测试事实写入 task record 的 Developer evidence；其 snapshot-bound Review 结论也只从该 record 读取。

历史决定：用户对旧 delivery `781422d...` / snapshot `1f23...` 回复过 `accepted`。首轮 Reviewer 随后提出非法 UTF-8 的 P1；旧决定保留为历史，没有自动沿用。

历史决定：用户分别对 delivery `781422d...` / snapshot `1f23...` 和 delivery `861e932...` / snapshot `46deff...` 回复过 `accepted`；两次 Reviewer 随后各自发现 P1。这些决定作为产品方向历史保留，没有自动沿用。

历史决定：用户对 delivery `4510c15...` / snapshot `52d94e...` 回复 `accepted；开始第三轮review`。第三轮 Reviewer 随后系统性发现 2 个 P1 和 1 个 P2；该决定作为产品方向历史保留，没有自动沿用。

历史决定（已被后续授权取代）：用户先对 delivery `431c2ca...` / snapshot `5c2880...` 回复 `accepted`，随后在第四轮 Reviewer 返回 1 个 P1、1 个 P2 后要求不修复并按当时状态提交到本地 `v4`，因而当时只形成带已知问题的检查点。2026-07-18 至 2026-07-19，用户后续明确授权全局修复、多轮独立 Review 和 Python 3.13 复核；带已知问题的旧检查点不再代表产品候选。后续每个 snapshot 与流程结论由对应 V3 task record 保存。

## 8. 阶段 0 退出与阶段 A 待命

### 8.1 退出条件核验

以下各项全部有真实证据后，才允许冻结阶段 A 最小规格：

- 人类确认唯一 focus core slice 和架构边界；
- 一个真实功能完成授权、实现、直接观察、人类决定、Reviewer、integration 与 closeout；
- 人类无需读取机器内部状态即可复现结果并说明真实、临时、限制和待决定事项；
- 至少一次实质决定在相关实现固化前提出；
- 至少一次已知架构越界在 Reviewer 或 integration 前被发现；
- 核心结果先于非必要 supporting 工作；
- 填写负担、理解成本和重复确认均被记录；
- 阶段 A 的字段和门禁已经依据试运行结果删减，而不是照搬提案。

核验结论：

- E-004 至 E-007C 证明真实功能可执行、人类可直接观察，且真实部分、临时部分与限制已被记录。
- D-002 与 E-005 证明实质决定在相关语义固化前提出。
- E-006 证明已知架构越界在 Reviewer/integration 前被发现。
- E-009 记录了核心结果顺序、填写负担、理解成本和重复确认，并已用于收窄阶段 A 设计。
- E-011 将最终 task record、closeout bundle、持久化 manifest、正式集成目标 ref 和证据范围修正绑定到同一阶段判定。

上述退出条件均有对应证据，阶段 0 正式判定为 `phase0_passed`。

### 8.2 正式落账证据

| 证据 | 封存值 |
| --- | --- |
| Pilot task record | `/Users/xy/codex-workflow-v4-doc001-gate-pilot-183c69b/.codex-workflow/state/runs/MVP-DOC-001.json`；status `completed`；verification `passed`；Review `pass`；integration `integrated`；generation `16` |
| Pilot closeout | commit `9e1642b42142ddf8533894ba291e86d737319de1`；bundle SHA-256 `6748928484ced15e87e194387c93b4e339b6985710f1ae64f444c045451157f1` |
| Persistence manifest | `/Users/xy/codex-workflow-v4-evidence/MVP-DOC-001/MVP-DOC-001-persistence-manifest.json`；SHA-256 `2f9bd4690dc98d904e9f94152053fd9398d25ca95b5bc18bf0c54a40b9d03f3c` |
| Formal integration | `v4` result commit `2388d2231e7ac69240fc550848883545cd4d4352`；sealed product match `true`；evidence SHA-256 `dcb5dec5fb0e8a59152c7910ff5f05f33c78391c1e4eab7eb5fc90c9b891bcb6` |
| P3 scope correction | `MVP-DOC-001-evidence-scope-correction-addendum.json`；SHA-256 `d3db67bddd9eaaf6c974d99690e7a9f129d159b2f8e9a01de0b7544889a45442`；历史证据保持不变 |
| Global verification | 正式 `v4` 在 Python 3.9.6、3.12.13 和 3.13.14 各通过 `86/86` package tests |

### 8.3 Phase A 待命边界

- 状态：`standby / not_authorized`。
- 不新增 task-record-v4 Schema、`delivery_contract`、`decision_log`、decision 命令、product checkpoint 或 V4 gate。
- 不建立 Developer lane，不执行 Phase A 实现、integration 或 closeout。
- 下一次状态转换需要人类明确授权启动 Phase A；授权前只允许保存和核验本阶段的封存证据。
