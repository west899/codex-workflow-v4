# Workflow 状态快照

> 此文件由工作流脚本生成；机器可读状态以 JSON 区块为准，不手工编辑。

> 更新时间：{{INSTALL_DATE}}｜状态指纹：`2ca4dc0a7e39a862c0805f172b5d9622c2eb2e1ca6eabe465827db5880a18e76`

<!-- CODEX_WORKFLOW_STATUS_JSON_START -->
{
  "backlog_counts": {
    "blocked": 0,
    "done": 0,
    "draft": 1,
    "ready": 0,
    "removed": 0
  },
  "generated_at": "{{INSTALL_DATE}}",
  "requirements_baseline": {
    "approval_fingerprint": null,
    "brief_id": null,
    "revision": null
  },
  "requirements_contract": {
    "managed_baseline": {
      "approval_fingerprint": null,
      "brief_id": null,
      "revision": null
    },
    "observed_baseline": null,
    "status": "not_configured"
  },
  "requirements_impacts": [],
  "schema_version": 1,
  "status_fingerprint": "2ca4dc0a7e39a862c0805f172b5d9622c2eb2e1ca6eabe465827db5880a18e76",
  "task_records": []
}
<!-- CODEX_WORKFLOW_STATUS_JSON_END -->

## 使用规则

- 所有 task、Backlog 与 Requirements 的真实状态来自受管状态文件；本快照只汇总它们。
- Requirements impact 报告出现 active task 时，必须先取得人类决定，再继续该 lane。
- 如果外部 Git 操作或人工编辑改变了受管状态，运行 `workflow_state.py sync-status --apply` 重新生成。
