# MVP Backlog

<!-- CODEX_REQUIREMENTS_BASELINE_START -->
{
  "workflow_schema_version": 3,
  "requirements_gate_mode": "required",
  "brief_id": "REQ-001",
  "revision": 1,
  "approval_fingerprint": "<64-hex approved fingerprint>",
  "target_release": "MVP-1",
  "status": "draft"
}
<!-- CODEX_REQUIREMENTS_BASELINE_END -->

<!-- CODEX_BACKLOG_FOCUS_START -->
{
  "workflow_schema_version": 4,
  "wip": {"unconfirmed_core_slice_limit": 1},
  "items": [
    {
      "id": "MVP-001",
      "kind": "core_slice",
      "focus_slice_id": "MVP-001",
      "supports_task_id": null,
      "direction_confirmed": false
    }
  ]
}
<!-- CODEX_BACKLOG_FOCUS_END -->

> 只有 `requirements-gate` 通过、PROJECT 与本区块逐项一致后，才能批准 ready 项。

## 发布目标与发布门

- 目标用户和可观察结果：<引用 approved REQ IDs>
- [ ] 所有 Must 项均有 integrated closeout 证据并为 durable done。
- [ ] 核心流程及适用的安全、隐私、权限、兼容、运营、恢复和回滚有证据。
- [ ] 人类负责人批准发布；done 不自动等于 released。

## 任务拆分

按可独立验收的纵向结果拆分。领取不修改 durable `ready`；claim + record 合成 effective active/verified。

| ID | 优先级 | 可观察交付结果 | 依赖 | 验收来源 | 风险 | 状态 | 阻塞类型 | 任务记录 | Lane/资源 | 集成证据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MVP-001 | Must | <用户能完成什么> | 无 | REQ-F-001 / REQ-S-001 | <风险或无> | ready | none | `.codex-workflow/state/runs/MVP-001.json` | <claim 后由 list 合成> | - |

允许 durable 状态：`draft | blocked | ready | done | removed`。阻塞类型：`dependencies | manual:<reason-id> | none`。

## 规则

1. 多个无依赖、资源不冲突的 ready 项可进入独立 lane；目标分支、Backlog、PLAN 和治理仍由唯一 Integrator/Coordinator 串行写。
2. completed/gate pass 只形成 verified；closeout commit 被 target ref 包含并 confirm 后才写 done。
3. 只有 dependencies 阻塞且全部依赖存在、无环、done 才能在 closeout 中自动解锁；其他阻塞人工处理。
4. 输出 next-ready 候选但不自动领取。

