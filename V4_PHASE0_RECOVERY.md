# V4 Phase 0 恢复重启计划

> 状态：`revised_waiting_execution_authorization`
>
> 本文件只是人类可读的执行计划和事件索引，不承担 task、Requirements、Review、gate、integration 或 closeout 的事实源职责。执行期的机器真相继续由 V3 task record、Requirements fingerprint、Git commit 和工作流门禁管理。
>
> 当前只完成计划修订。尚未创建恢复分支、新 Pilot、Requirements、task、lane，也尚未开始修复、Review、集成或推送。

## 1. 当前结论

- 正式远端基线为 `origin/v4@30b15e24ae5025345850ec8afd391ef62e55e4b5`。
- 当前本机 `v4` 与 `origin/v4` 一致，工作区在计划修订前为干净状态。
- 旧 Pilot、generation、claim、Review approval、snapshot 和 runtime 缺少可恢复的连续证据，全部退出当前执行链。
- `V4_PHASE0_PILOT.md` 保留为历史摘要，其中的旧哈希和结论不得继续充当新 task 的正式证据。
- 旧任务保持 `changes_requested`、verification pending、integration not_ready，不得补写为 verified、done 或 phase0_passed。
- Phase 0 恢复只修复并验证 `doctor`；Phase A 在新 Pilot 完成 Reviewer pass、gate、integration 和 closeout 之前保持冻结。

### 1.1 恢复前测试基线

- macOS / Python 3.9.6：69 项中 7 项失败，已在当前本机重放。
- macOS / Python 3.12.13：69 项中 5 项失败，来自恢复计划输入；执行开始时必须用精确解释器路径重放并记录。
- 已知失败聚焦跨版本 JSON 整数资源边界、嵌套深度、P1 异常误归类以及 P2 路径解析异常绕过结构化输出。
- 69 只是恢复前的回归基数。完成验收使用“新增后的完整测试总数全部通过”，不将最终数量固定为 69。

## 2. 执行不变量

1. 旧 Pilot 中的 task ID、generation、claim、approval、snapshot、Review 和 runtime 不得复用。
2. 每个新阶段只在获得用户对当前对象、范围和动作的明确指令后开始。
3. Developer 完成后汇报并停止；人类观察完成后停止；每轮 Review 完成后汇报并停止。
4. V3 尚无逐轮 `review_authorization` 机械门禁。恢复期使用用户明确指令、本文件事件引用和强制停止纪律执行该边界。
5. 每个人工停止点必须先建立可校验的异机备份，且不得为了备份而推进 lane `HEAD` 并破坏 sealed delivery 身份。
6. Pilot 不得向正式远端的 `main` 或 `v4` Push。备份使用独立 Git bundle、受控存储或单独的只读备份远端。
7. `V4_PHASE0_RECOVERY.md` 只记录事件 ID、证据引用和人类决定来源，不复制 task record 中的可变状态。
8. 正式 `v4` 在单独授权的最终快进之前保持在已确认基线；远端前进时立即停止并重建基线。

## 3. 正式恢复分支

1. 执行前先 Fetch，并确认 `origin/v4` 仍精确等于 `30b15e24ae5025345850ec8afd391ef62e55e4b5`。
2. 从该精确提交创建 `codex/v4-phase0-recovery`。
3. 将本计划作为恢复分支的第一个独立文档提交，不与后续产品导入混合。
4. 在用户明确授权远端备份后，Push 同名 recovery 分支。该操作不更新 `origin/v4`。
5. 后续只在 recovery 分支上维护正式计划与产品导入提交。

## 4. 全新外部 Pilot

### 4.1 路径与 Git 拓扑

- Pilot 路径：`/Users/codex-workflow-v4-pilot-recovery-30b15e2`。
- 从正式远端精确 checkout `origin/v4@30b15e2`。
- 从 `30b15e2` 创建无 upstream 的本地 `main`；不让本地 `main` 跟踪 `origin/main` 或 `origin/v4`。
- 将正式远端改名为 `upstream`，为它设置无效 Push URL，并设置 `push.default=nothing`。
- 在环境允许时使用只读凭据或服务端权限；本地 pre-push Hook 仅作附加防护。
- 正式开始前记录 `git remote -v`、本地分支 upstream 状态，并执行一次无产品变更的 Push 拒绝验证。

### 4.2 V3 安装基线

1. 从记录了精确提交的包工作树运行安装器，目标指向外部 Pilot，并启用 `local_worktree`。
2. 安装基线阶段只显式配置：

   ```text
   parallel.mode = local_worktree
   integration_policy.local_target_ref = refs/heads/main
   integration_policy.local_bootstrap.enabled = false
   remote.mode = disabled
   ```

3. local bootstrap allowlist 依赖已存在的 Backlog task，因此不在空治理模板阶段提前启用。
4. 运行安装检查、manual、status 和完整包基线验证，记录实际 Python 解释器路径与版本。
5. 将 V3 安装文件、Pilot layout 和初始治理文件提交到 Pilot `main`，形成干净的安装基线 commit。
6. 后续先建立已提交的 Requirements/Backlog 治理基线，再创建引用该 commit 的授权 task record。claim `--base main` 会将 lane 中的最终 `base_commit` 绑定到已包含授权 task record 的 Pilot `main` commit，不直接绑定原始 `30b15e2`。

### 4.3 备份与停止点保护

- 安装基线、授权基线、Developer 交接、每轮 Review、prepared/queued 和 closeout 都是必须备份的停止点。
- 每个停止点建立包含 Pilot `main`、task branch 和当前审计 checkpoint ref 的 Git bundle，执行 `git bundle verify` 和 `git bundle list-heads`，再记录 bundle SHA-256。
- bundle 必须复制到另一设备或受控存储；仅留在 Pilot 所在磁盘不算异机备份。
- `record-developer`、`record-review`、acceptance 和 approval 在 gate 前可能使 task record 处于未提交状态。此时 lane `HEAD` 必须保持 exact sealed delivery commit，不得用普通 commit 推进 `HEAD`。
- gate 前的停止点使用不移动 `HEAD` 的 Git checkpoint ref 保存受跟踪状态，例如使用 `git stash create` 产生 checkpoint commit，再将它写入专用 `refs/archive/v4-phase0/...` 后收入 bundle。创建后必须确认 lane 工作树未被清理且 `HEAD` 未变。
- prepared/queued task record 按 V3 流程提交到 task branch；该阶段后 bundle 直接包含该 commit。
- `.git/codex-workflow-v3/` 只做加密、只读的历史审计归档。它可能包含 owner、token、本机路径和 session 信息，不得上传到普通 Git 远端。
- 恢复时从 tracked record、branch 和 worktree 执行 `rebuild` dry-run，检查后再 `--apply`。stale lane 只能在明确人工批准后执行 `recover --takeover` dry-run/apply，不复活旧锁、heartbeat 或 claim。

## 5. 重建治理合同

### 5.1 身份

- Pilot：`v4-phase0-recovery-macos-30b15e2`。
- Requirements：`REQ-V4-PILOT-002`。
- Target release：`V4-PHASE0-RECOVERY`。
- Focus：`DOC-002`。
- Task：`MVP-DOC-002`。
- 来源文本：`supersedes unavailable historical pilot MVP-DOC-001; no prior generation, claim, approval, or snapshot reused`。

### 5.2 授权基线

1. 重新编写只覆盖 `DOC-002` 恢复的 Requirements，包含可观察结果、边界、失败样例、非目标和两套 Python 兼容性验收。
2. 生成 Requirements snapshot，向用户展示精确 brief ID、revision、target release 和 fingerprint。
3. 只有在用户批准精确 fingerprint 后，才写回 approval 并通过 requirements gate。
4. 将同一 Requirements baseline 写入 PROJECT 和 Backlog，将 `MVP-DOC-002` 置为唯一可执行的 `ready` 项。
5. 在 Backlog 中已存在且只存在一个 `MVP-DOC-002` 后，将 Pilot `layout.json` 的 local bootstrap 配置为：

   ```text
   enabled = true
   allowed_task_ids = [MVP-DOC-002]
   expires_after_task = MVP-DOC-002
   require_ff_only = true
   ```

6. 同步 STATUS，将 Requirements、PROJECT、Backlog、STATUS 和 layout 提交为干净的治理基线 commit。
7. 建立新 task record，完整写入 scope in/out、allowed paths、resource keys、acceptance、risk、planning、指向上一治理基线 commit 的 exact base commit 和 implementation authorization。
8. 再次同步 STATUS，将已授权 task record 和 STATUS 提交为授权基线 commit。
9. 运行 manual、status 和 task preflight，确认授权基线工作树干净。
10. 对 `MVP-DOC-002` 执行 claim dry-run/apply，使用 `--base main` 将 lane `base_commit` 绑定到授权基线 commit，并创建 Developer worktree。

## 6. 实现合同

### 6.1 允许路径

Developer 允许修改：

- `payload/.codex-workflow/bin/workflow_check.py`
- `payload/.codex-workflow/bin/workflow_common.py`
- `payload/.codex-workflow/bin/workflow_paths.py`
- `tests/test_workflow_check.py`
- `tests/support.py`，仅当双解释器测试支持确有需要时使用，并在交接中说明必要性。

本阶段选择在 `workflow_paths.py` 中放置依赖无关的 JSON 解析原语与类型异常，因为 `workflow_common.py` 已单向依赖 `workflow_paths.py`。`workflow_paths.py` 不得反向导入 `workflow_common.py`。

如果实现证明必须新增独立 `workflow_json.py` 才能保持边界清晰，Developer 必须停止、说明新文件的依赖位置、安装/验证影响和新 allowed paths，待用户重新授权后才能继续。

### 6.2 JSON 解析边界

定义独立异常层级：

```text
WorkflowJsonError(Exception)
├─ WorkflowJsonSyntaxError
└─ WorkflowJsonResourceError
```

- 这些异常不继承 `WorkflowDataError`、`WorkflowPathError` 或 `ValueError`。
- loader 接收已经在各领域文件读取边界解码的 `str`。UTF-8/BOM 和 `OSError` 继续由 manifest、Hooks、observation、layout 和 governance 各自的读取边界处理。
- JSON 语法错误在 layout 边界转换为原有 `WorkflowPathError`，在 Requirements、Schema 和嵌入 JSON 边界转换为原有 `WorkflowDataError`。
- JSON 资源限制保留为 `WorkflowJsonResourceError`。doctor 只在精确的数据领域边界将它映射为 `INVALID`；非 doctor 命令保留 fail-fast traceback。
- doctor discovery 和 governance 中宽泛的 `except ValueError`、`except RecursionError` 或外围 `RuntimeError` 捕获必须移除，只捕获已定义的数据或路径异常。

### 6.3 跨版本资源限制

- 最大 JSON 整数位数为 640。
- 使用 `json.loads(..., parse_int=...)` 执行限制，去除可选负号后计数；639 和 640 位通过，641 和 5000 位进入 `WorkflowJsonResourceError`。
- 不依赖 `PYTHONINTMAXSTRDIGITS`，不调用全局 `sys.set_int_max_str_digits`。
- 最大 JSON 容器嵌套深度为 128，根对象或根数组计为第 1 层。
- 在 `json.loads` 之前执行字符串感知的词法深度扫描，正确忽略 JSON 字符串内的 `{`、`[`、`}`、`]`，并正确处理转义引号与反斜杠。
- 128 层通过，129 层在调用标准库 decoder 前进入 `WorkflowJsonResourceError`。
- 不依赖解析后遍历来保护 decoder，不修改全局 recursion limit。
- 本 Phase 0 边界只覆盖整数位数和容器嵌套深度；总文件大小、字符串长度和容器元素数不在本 task 范围内。

### 6.4 路径解析边界

- 定义独立 `WorkflowPathResolutionError(RuntimeError)`，不继承 `WorkflowPathError`。
- 提取紧贴 `Path.resolve()` 的 helper。helper 先显式 `except RecursionError: raise`，然后只将该次 `resolve()` 直接产生的 `RuntimeError` 包装为 `WorkflowPathResolutionError`。
- `Path` 构造、`expanduser()`、属性访问和外围业务逻辑产生的 `RuntimeError` 不进入该 helper。
- doctor 可达的 layout discovery、tracked path、shared/lane runtime、Schema 引用和 observation worktree 解析都必须走同一精确 helper。
- doctor 将 `WorkflowPathResolutionError` 映射为相应领域的 `UNKNOWN`；非 doctor 保留明确失败与 traceback。

### 6.5 doctor 可达资源

以下资源在 doctor 路径上统一使用新 loader 和精确解析边界：

- layout
- install manifest
- Hooks
- startup observation
- PROJECT/Backlog baseline 嵌入 JSON
- Requirements brief metadata
- Requirements Schema 及其本地 `$ref`

完成后 doctor 输出继续严格包含四个且仅四个诊断域，以及一个且仅一个 `PRIMARY NEXT ACTION`。动态文本必须保持单行安全转义。

### 6.6 明确延后

本阶段不新增：

- V4 Schema 或 task-record-v4
- decision/request-decision/record-decision
- product checkpoint 机械门禁
- V4 gate
- STATUS 或 Stop Hook 的 V4 语义
- V3/V4 迁移
- 总 JSON 字节、字符串或容器元素限额

## 7. 验证矩阵

### 7.1 JSON 边界

- 整数：639、640、641、5000 位，包含正数和负数。
- 嵌套：127、128、129 层，覆盖对象、数组和混合容器。
- 词法扫描：字符串内括号、转义引号、奇偶反斜杠、非对称括号和非法 JSON。
- 资源类型：layout、manifest、Hooks、observation、PROJECT baseline、Backlog baseline、Requirements brief 和 Schema。
- 异常分类：syntax、resource、UTF-8、OSError、`WorkflowPathResolutionError`、编程型 `ValueError`、`RuntimeError` 和 `RecursionError`。

### 7.2 功能与安全回归

- 正常 doctor 输出为四域结果和唯一 primary action。
- 动态诊断不得伪造域标题或额外 action。
- doctor 在普通 Python 调用下不产生 tracked、runtime、`__pycache__` 或 `.pyc` 变化。
- doctor 自身或导入依赖无法加载时继续属于外部 bootstrap failure。
- 非 doctor 的普通语法/治理错误保持既有 CLI 语义；资源限制和编程型异常保持 fail-fast。
- 安装器、Schema gate、Requirements impact、lane/state、fault recovery、Stop Hook 和 end-to-end 完整回归通过。

### 7.3 双解释器

分别使用精确解释器执行同一份 checkout 与同一套命令：

- Python 3.9.6
- Python 3.12.13

每套证据记录：

- `sys.executable`
- `sys.version`
- OS 与架构
- focused test 命令、exit code 和结果
- `python -B verify_package.py` 命令、exit code、新总数和结果
- doctor 观察命令与输出摘要

`tests/support.py` 只有在两个解释器无法使用同一现有测试支持时才修改。

## 8. 执行阶段与强制停止点

Phase 0 的完整执行链共七个阶段，按 `R0 → R1 → R2 → R3 → R4 → R5 → R6` 顺序推进。每个阶段都是独立授权边界；当前阶段汇报并停止后，才能由用户明确授权下一阶段。

### 8.1 阶段 R0：恢复基线

1. 获得用户对“只创建 recovery 分支、外部 Pilot、V3 安装基线和治理草案”的明确授权。
2. 建立第 3、4、5 节的基线，不修改 doctor 产品文件。
3. 展示 Requirements snapshot 与 task contract；等待用户批准 fingerprint 和 implementation authorization。
4. 建立异机 bundle 后汇报并停止。

### 8.2 阶段 R1：Developer

1. 只在用户批准精确 Requirements、task scope、allowed paths、资源限制和 implementation authorization 后启动 Developer。
2. Developer 运行 focused tests、两套 Python 完整回归和可观察 doctor 命令，形成干净 delivery commit。
3. 运行 `record-developer`，绑定 exact delivery commit、delivery hash、patch hash 和 snapshot。
4. 保持 lane `HEAD` 在 sealed delivery commit，建立不移动 `HEAD` 的 archive checkpoint ref 和异机 bundle。
5. 汇报行为、修改路径、两套 Python 证据、假设、未验证项和当前 snapshot，然后停止。

### 8.3 阶段 R2：独立人类观察

1. 获得用户对“为当前 snapshot 准备一次独立观察”的明确授权。
2. 观察路径使用 `/Users/codex-workflow-v4-doctor-observation-<delivery-commit-or-snapshot>`，每个候选 snapshot 创建全新目录。
3. 从候选 delivery commit 的精确 worktree 安装观察项目，记录安装源 commit、manifest hash、解释器和观察命令。
4. 重放正常四域输出、整数/嵌套资源失败输出、路径解析失败和严格只读性。
5. 向用户展示真实结果、临时部分、限制与未验证空白，由用户决定 accepted、changes_requested、deferred 或 stopped。
6. 记录决定来源，建立异机备份，然后停止。

### 8.4 阶段 R3：逐轮独立 Review

1. 只有当人类接受当前产品方向，并对 exact snapshot 和 Review 范围给出明确指令时，才启动一轮独立、只读 Reviewer。
2. Reviewer 只审查当前 exact delivery commit/snapshot，重建验收清单并运行必要的只读测试。
3. Coordinator 通过 `record-review` 原样记录结果，建立不移动 lane `HEAD` 的 archive checkpoint ref 和异机 bundle。
4. 汇报 findings、结论、未验证空白和当前租约状态，然后停止。
5. `changes_requested` 时，由用户选择修复、接受、延后或停止。修复必须取得新一轮实现授权，并重新执行 R1、R2 和经授权的 R3。
6. Reviewer pass 后仍然停止，等待用户对精确验收、local bootstrap 目标和收尾链给出独立授权。

### 8.5 阶段 R4：V3 验证、集成与 closeout

1. 只有 R3 的 Reviewer 结论为 pass，且用户对 exact task、snapshot、delivery hash、target ref 和第 9 节完整收尾链给出独立授权后，才能进入 R4。
2. 按第 9 节的固定顺序执行 task completion、gate、mark-verified、approval、integration preflight、queue、FF-only 集成和 closeout。
3. 任一步失败都立即停止并汇报已完成转换、失败位置、租约状态和安全恢复入口。任何产品内容修改都使当前 snapshot、Review 和 approval 失效，必须回到经授权的 R1。
4. Pilot `main` 完成 FF-only 集成和 closeout 后，验证 closeout bundle 与加密 runtime 审计归档，汇报精确结果并停止。

### 8.6 阶段 R5：正式导入与重新验证

1. 只有 R4 完成 closeout，且用户对 Pilot verified delivery 的精确导入给出独立授权后，才能进入 R5。
2. 仅将第 10.2 节允许的产品差异导入 recovery 分支，并按第 10.3 节比较 canonical changed paths、mode、blob OID、symlink target 和 patch hash。
3. 在正式导入 commit 上重新运行 Python 3.9.6 与 3.12.13 的 focused tests、完整测试和全新 snapshot-specific 人工观察。
4. 展示导入差异、等价证明、验证结果和剩余风险，然后停止。R5 授权不包含更新或 Push `v4`。

### 8.7 阶段 R6：最终快进 `v4`

1. 只有用户接受 R5 的精确正式导入 commit，并对 `v4` fast-forward Push 给出最终独立授权后，才能进入 R6。
2. 重新 Fetch，按第 10.5 节校验远端基线、祖先关系、精确 commit 范围和工作树清洁性。任一前置条件不成立都立即停止。
3. 使用普通 fast-forward Push 推进远端 `v4`，不使用 force，不创建 PR。
4. Push 后再次 Fetch，确认本地 `v4`、`origin/v4` 和获批 commit 一致，校验第 11 节全部完成条件，汇报并停止。

### 8.8 全阶段租约与中断恢复

- lane 人工停止可能超过默认租约时间。每次恢复前先运行 lane list/status 并检查 token、generation、branch、worktree 和 task record。
- active 且 token/generation 匹配时才能 heartbeat。stale lane 不得通过 heartbeat 复活。
- stale lane 必须先 `recover <lane-id> --takeover` dry-run，展示旧所有权与新 generation，获得人工批准后再 `--apply`。
- 恢复后重新校验 task、工作树、sealed delivery 和 archive checkpoint，再继续当前已授权阶段。

## 9. 阶段 R4 详细流程：V3 验证、集成与 closeout

Reviewer pass 后，向用户展示 exact task ID、target ref、snapshot ID、delivery hash、acceptance、已知风险、local bootstrap 模式和以下完整收尾链。只有获得对该精确链的单独人工授权后才执行。

```text
record-review --apply（已完成并汇报）
→ complete-task --apply
→ workflow_check.py gate <record>
→ mark-verified --apply
→ record-approval --apply
   kind=local_bootstrap
   target_ref=refs/heads/main
   exact task/snapshot/delivery hash
→ prepare-integration --mode local_bootstrap --apply
→ workflow_check.py integration-preflight <record>
→ queue <lane-id> --priority 100 --apply
→ 在 task branch 提交 prepared/queued task record
→ 建立异机 bundle
→ lock-acquire integrator --apply，保存 token/generation
→ Pilot main worktree: git merge --ff-only <task-branch>
→ prepare-local-closeout --target-ref refs/heads/main --result-commit <exact-main-sha> --apply
→ workflow_check.py closeout-gate <record>
→ confirm-closeout dry-run
→ confirm-closeout --apply
→ lock-release integrator --apply
→ 建立 closeout bundle 和加密 runtime 审计归档
```

额外约束：

- final gate 运行时 lane `HEAD` 必须仍为 exact sealed delivery commit。
- `record-approval`、`prepare-integration` 和 `queue` 后才允许提交 prepared/queued task record；不得在 gate 前通过普通 commit 改变 lane `HEAD`。
- FF 集成前重放 `integration-preflight`，确认 queued 身份、路径和 runtime queue 仍一致。
- 获得 Integrator lease 后任何步骤失败，都不继续下一业务转换。必须保存 token/generation，报告 lock status，并按已授权的安全清理边界执行 release；无法安全 release 时保留租约并停止。
- 租约过期后只能经 `lock-status` 和新的人工批准 takeover 恢复。
- 任何内容修复、rebase 或冲突解决都会使旧 snapshot/Review/approval 失效，必须回到经授权的 R1、R2 和 R3。

## 10. 阶段 R5/R6 详细流程：正式导入与 `v4` 最终快进

### 10.1 R5 导入前提

- Pilot 已 Reviewer pass、gate、mark-verified、local integration 和 closeout。
- Pilot `main` 包含已确认的 integrated task 状态。
- closeout bundle、bundle verify 结果、bundle SHA-256 和加密审计归档已保存到异机位置。
- 用户对“从 Pilot 取出精确产品差异并导入 recovery 分支”给出单独授权。

### 10.2 R5 导入范围

只允许导入 Pilot verified delivery 中实际变更的以下产品路径：

- `payload/.codex-workflow/bin/workflow_check.py`
- `payload/.codex-workflow/bin/workflow_common.py`
- `payload/.codex-workflow/bin/workflow_paths.py`
- `tests/test_workflow_check.py`
- `tests/support.py`，仅当 Pilot verified delivery 确实修改它时导入。

Pilot 的根 `AGENTS.md`、`.gitignore`、`.agents/**`、`.codex/**`、已安装的根 `.codex-workflow/**`、governance、Requirements、Backlog、STATUS、task record、runtime、claim 和 audit 全部排除。

### 10.3 R5 差异身份与等价证明

- 记录 Pilot verified delivery commit、snapshot ID、delivery hash、patch hash 和 exact `changed_paths`。
- 从 Pilot base 到 verified delivery 导出 canonical raw entries，包含状态、旧/新路径、旧/新 mode、旧/新 blob OID 和 symlink target。
- 正式导入提交只包含精确产品路径；`V4_PHASE0_RECOVERY.md` 更新使用独立文档提交。
- 对正式导入提交计算 canonical changed-path 集合、raw entries 和 patch hash，并与 Pilot delivery 比较。
- 逐文件 SHA-256 只作额外内容校验，不代替 Git mode、blob、symlink 和 patch 证明。
- Pilot 与正式恢复分支使用不同 base、task、lane 和 branch，因此 delivery hash 和 snapshot ID 保持不同身份，不得将 Pilot Review/gate 结论写成对正式导入 commit 的直接证明。

### 10.4 R5 正式导入验证

1. 在 recovery 分支上创建只含产品差异的独立导入 commit。
2. 用 Python 3.9.6 和 Python 3.12.13 分别运行 focused tests 与完整 `verify_package.py`。
3. 从正式导入 commit 建立新的 snapshot-specific 观察项目，重放 doctor 四域、资源失败、路径失败和只读性。
4. 展示正式导入的 changed paths、canonical patch 等价结果、两套 Python 结果、观察结果和剩余风险，然后停止。
5. 只有用户对该正式导入 commit 给出单独接受后，才允许进入最终 `v4` 快进。

### 10.5 R6 `v4` 最终快进

1. 重新 Fetch 正式远端。
2. 断言 `origin/v4` 仍精确等于 `30b15e24ae5025345850ec8afd391ef62e55e4b5`。
3. 断言 `origin/v4` 是 `codex/v4-phase0-recovery` 的祖先，且恢复分支工作区干净。
4. 远端已前进时立即停止，重建基线、重做正式导入验证并取得新授权。
5. 向用户展示将要快进的精确 commit 范围和远端目标，获得最终授权。
6. 使用普通 fast-forward Push 将 recovery 分支推进到 `v4`，不使用 force，不创建 PR。
7. Push 后 Fetch 并确认本地 `v4`、`origin/v4` 和获批的精确 commit 一致。

## 11. Phase 0 完成条件

只有以下条件全部成立时，才能将恢复阶段标记为 `phase0_passed`：

- 新 Requirements fingerprint 获得人类批准并通过 gate。
- `MVP-DOC-002` 完成授权、Developer、独立人类观察和经明确授权的逐轮 Reviewer。
- 最终 Reviewer 结论为 pass，且 P0/P1 为 0，所有未解决 P2 获得精确人类接受。
- Python 3.9.6 和 3.12.13 的新完整测试总数全部通过。
- doctor 严格只读，无 tracked、runtime 或 bytecode 变化。
- doctor 只输出四个诊断域和一个 primary action，动态文本无法伪造结构。
- 非 doctor 异常传播回归通过。
- Pilot gate、mark-verified、local integration 和 closeout 完成。
- 每个关键停止点都有经 verify 的异机 bundle，审计 runtime 已加密归档。
- 正式导入 commit 的 canonical patch 与 Pilot verified delivery 等价，且在正式 recovery 分支上重做验证。
- 用户批准正式导入并授权 `v4` fast-forward Push。
- 最终本地与远端 `v4` 指向同一获批 commit。

## 12. Phase A 冻结与后续顺序

Phase 0 完成前，不实现 Phase A。Phase 0 通过后另行展示与批准以下顺序：

1. task-record-v4 与 `delivery_contract`
2. `decision_log`、`request-decision` 和 `record-decision`
3. product checkpoint、observation receipt 和 observation fingerprint
4. V4 preflight/record-review/gate 防绕过检查
5. STATUS 产品摘要、Stop Hook 决策提示和观察入口
6. V3/V4 双读、安装迁移和版本化 closeout fingerprint

## 13. 下一步授权句

开始执行时建议使用以下精确指令：

> 只执行 R0：从 `origin/v4@30b15e2` 创建 `codex/v4-phase0-recovery`，建立全新外部 Pilot、V3 安装基线和 `REQ-V4-PILOT-002` / `MVP-DOC-002` 治理草案；不修改 doctor，不授权 Developer，不启动 Review。建立异机备份后汇报并停止。
