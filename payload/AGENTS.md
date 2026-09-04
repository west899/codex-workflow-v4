# AGENTS：Codex Workflow V4 入口

<!-- BEGIN CODEX WORKFLOW ENTRY -->
工作流主体、状态或脚本缺失时停止，不得绕过门禁。一个 lane 只能有一个写入者；多个 lane 可并行，但目标分支、Backlog、PLAN 和永久治理只由一个 Coordinator/Integrator 写入。未经明确授权，不得自动 push、开 PR、合并、force、删除 branch/worktree、发布或部署。

开始工作时看 `.codex-workflow/state/STATUS.md` 的下一动作；有活动任务则读该 task record。不要在动手前通读 `.codex-workflow/docs/WORKFLOW.md` 或完整协议手册。协议与 WORKFLOW 只在 claim、提交 Developer evidence、或 closeout 时由对应 skill 打开。SessionStart 会跑 `workflow_check.py start`。
<!-- END CODEX WORKFLOW ENTRY -->

