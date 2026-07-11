# DECISIONS：决策与纠错

> 项目：{{PROJECT_NAME}}｜用途：解释“为什么这样做”｜更新：{{INSTALL_DATE}}

## 当前有效决定

| ID | 日期/状态 | 决定 | 原因 | 影响 |
| --- | --- | --- | --- | --- |
| W-001 | {{INSTALL_DATE}} 有效 | 工作流主体集中到 `.codex-workflow/`，只保留官方发现薄入口 | 区分 package、project、merge 和 runtime 所有权 | 升级不得覆盖项目治理 |
| W-002 | {{INSTALL_DATE}} 有效 | Coordinator、Developer、Reviewer、Integrator 分责 | 降低自我验证和共享状态竞态 | Reviewer 只读；目标分支单写 |
| W-003 | {{INSTALL_DATE}} 有效 | 目标版本先完成 Requirements 第 2A 步，再拆 Backlog | 防止浅层理解直接变成实现范围 | Backlog 绑定批准 fingerprint |
| W-004 | {{INSTALL_DATE}} 有效 | 区分 completed、verified、done、released | 本地证据、主分支状态和发布是不同事实 | 不得提前声称完成或上线 |
| W-005 | {{INSTALL_DATE}} 有效 | 每个非简单任务强制复盘 | 工作流根据真实失败持续改进 | 永久规则仍需人类批准 |
| W-006 | {{INSTALL_DATE}} 有效 | 本地锁、JSON、Hook 和 Agent 身份不是安全信任根 | 同一系统权限可修改合作式证据 | 正式发布依赖远端 CI/保护/人工审批 |
| W-007 | {{INSTALL_DATE}} 已替代 | V2 同时只允许一个活动任务和一个写入者 | 单指针和共享工作树无法隔离并行写入 | 由 W-009 替代 |
| W-008 | {{INSTALL_DATE}} 有效 | Stop Hook 只检查当前 lane 并给下一步 | Stop 是会话事件，不是全局调度器 | Coordinator Stop 不冒充 lane gate |
| W-009 | {{INSTALL_DATE}} 有效 | 多个本地 Developer lane 使用独立 Git worktree；目标分支仍串行集成 | 提供同机并行并保持写入隔离 | claim/resource/common-dir registry 机械互斥 |
| W-010 | {{INSTALL_DATE}} 有效 | 集成使用 prepare/confirm 两阶段 closeout | 合并成功与状态清理可能跨会话中断 | exact commit + state fingerprint 后才释放 claim |
| W-011 | {{INSTALL_DATE}} 有效 | 远端先支持预分配，再可选 atomic refs 自主领取 | 不同机器没有共享文件锁 | atomic 不可用时自动领取 fail closed |

## 纠错记录

| ID | 日期/状态 | 问题 | 纠正 | 防复发规则 |
| --- | --- | --- | --- | --- |

历史决定只追加；失效项保留并链接替代 ID。

