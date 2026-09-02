# V4 Phase A 收口证据

> 本文件把 `V4_PHASEA_PLAN.md` 第 10 节完成定义映射到已有精确证据。它不是运行时真相源，也不镜像 live lane / generation。Phase A 规划真相源仍是 [V4_PHASEA_PLAN.md](V4_PHASEA_PLAN.md)。

## 1. 落账结论

| 字段 | 值 |
| --- | --- |
| Plan ID | `V4-PHASEA` |
| Revision | `1` |
| Normative fingerprint | `4a4a0eddb63c8d7a7c593235d2cd9e66c0fe8f0f97414e4014168bf47b7acbd8` |
| 计划状态 | `phase_a_passed` |
| 收口授权 | 用户于 2026-09-02 授权收口 Phase A，并只允许开始 Phase B 计划准备 |
| 产品闭环证据 | [V4_PHASEA_CORE_SLICE.md](V4_PHASEA_CORE_SLICE.md) |
| 下一规划源 | [V4_PHASEB_PLAN.md](V4_PHASEB_PLAN.md)，状态 `awaiting_human_review`，实施授权关闭 |

本轮收口补齐了 PA-010 升级盘点中的治理定制列表，以及 PA-011 文档对 `task-record-v4` 的默认路径表述。第 2–10 节冻结语义未改。

## 2. 完成定义核验

| # | 完成定义 | 证据 | 结论 |
| --- | --- | --- | --- |
| 1 | 新 V4 core slice 绑定人类确认的 focus，核心可观察结果先于非必要 supporting 工作出现 | 外部真实切片 `MVP-001` / `core_slice`；观察命令 `python src/observe_core.py` 输出 `CORE_READY`；见 core slice 证据 | 满足 |
| 2 | 人类可通过 STATUS/决策卡片进入观察，并说明真实/临时、实质变化和当前决定 | core slice STATUS 人类区含焦点、CLI 入口、观察步骤、真实/临时、产品方向 `accepted`；`tests/test_v4_status.py` 覆盖产品卡、下一动作和敏感信息省略 | 满足 |
| 3 | 影响当前切片的产品、数据、公共接口、依赖或架构决定在固化前阻断 | `tests/test_v4_workflow.py`：open blocking decision、architecture-sensitive delivery、supporting 绑定、show-before-dependency | 满足 |
| 4 | required checkpoint 在独立 Reviewer 前获得当前 receipt 与匹配的人类方向确认 | 真实切片：`request-decision HD-001 product_checkpoint` → `record-decision accepted` → `record-review`；机械负例覆盖 stale/wrong/sensitive/expired receipt | 满足 |
| 5 | 新 snapshot 后产品语义变化重新请求观察；严格技术等价只通过完整延续门禁 | `tests/test_v4_status.py` 拒绝无效延续；`tests/test_v4_workflow.py` 覆盖 `changes_requested` 全量 reset、continuation 与 observation 失效 | 满足 |
| 6 | 项目架构基线和切片护栏进入 contract fingerprint，并由 Coordinator、checker、Reviewer 独立核对 | `tests/test_v4_contract.py` 冻结基线形状与失效矩阵；`tests/test_v4_workflow.py` 覆盖 live architecture drift 与 inline guardrail | 满足 |
| 7 | product direction、technical verified、integrated/done、released 分开表达 | 真实切片同时记录 `accepted` / `passed` / `integrated`，未写成 released；STATUS 与 gate 测试禁止混称 | 满足 |
| 8 | V3/V4 closeout fingerprint 分版本，V3 兼容保持 | `tests/test_v4_closeout.py`：V3 算法不变、V4 检测 contract/decision 漂移、pending closeout 不重算；`confirm-closeout` 按 record 版本分派 schema | 满足 |
| 9 | pending/queued 后新决定 fail closed，显式 abandon 不留僵尸 queue/claim，不伪造 closeout | `tests/test_v4_workflow.py`：pending/queued 拒绝新 decision 与 refresh-base，要求 explicit abandon | 满足 |
| 10 | Evidence Contract v1 claim 全部 `confirmed`，独立 Review、gate、integration、closeout 和证据持久化完成 | 真实切片：Review generation 4、verification `passed`、closeout commit `388d2ca9b1a4ab27b257f4153a94967665cc1b9d`、fingerprint version `4` | 满足 |

## 3. 本轮收口补丁

升级计划必须列出项目自定义治理文件，且不得覆盖它们。收口时补上只读盘点：

- `install.py`：`--plan-upgrade` 增加 `governance_customizations`
- `tests/test_install.py`：模板匹配为空列表；改写 `PROJECT.md` 后列出该文件且不回写
- `README.md` / `payload/.codex-workflow/docs/WORKFLOW.md`：结构门禁覆盖 `task-record-v4`
- `verify_package.py`：拒绝仍把 schema 门禁写成 V3-only 的 README

这些补丁属于 PA-010/PA-011 冻结范围，不引入 Phase B 字段或命令。

## 4. 明确未宣称

- 未把工作流包装进本包目录自身。
- Windows / Python 3.12 / 3.13 本轮未重跑，不写入已验证范围。
- 未实施 Phase B：没有 Backlog 新列、WIP 硬限制、滚动 Requirements 新模型、guardrail registry、风险比例门禁、decision supersede 或 pending/queued 原地 reopen。
- 批准 [V4_PHASEB_PLAN.md](V4_PHASEB_PLAN.md) 不等于开始 Phase B 实现。
