# MVP Backlog

<!-- CODEX_REQUIREMENTS_BASELINE_START -->
{
  "workflow_schema_version": 3,
  "requirements_gate_mode": "required",
  "brief_id": null,
  "revision": null,
  "approval_fingerprint": null,
  "target_release": null,
  "status": "draft"
}
<!-- CODEX_REQUIREMENTS_BASELINE_END -->

> 状态：draft｜更新：{{INSTALL_DATE}}｜目标范围来源：`../../governance/PROJECT.md`

## 发布门

- [ ] 当前 Requirements Brief 已批准且 fingerprint 一致。
- [ ] 所有 Must 项均为 durable `done` 并有集成证据。
- [ ] 核心流程、安全、隐私、权限、兼容、运营、恢复和回滚有证据。
- [ ] 人类负责人批准发布；done 不自动等于 released。

## 任务拆分

领取时 durable 状态仍为 `ready`；claim + task record 合成 effective active/verified。不要把运行态写回共享 Backlog。

| ID | 优先级 | 可观察交付结果 | 依赖 | 验收来源 | 风险 | 状态 | 阻塞类型 | 任务记录 | Lane/资源 | 集成证据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MVP-001 | Must | <用户能完成什么> | 无 | REQ-F-001 / REQ-S-001 | <风险或无> | draft | none | - | - | - |

允许 durable 状态：`draft | blocked | ready | done | removed`。阻塞类型：`dependencies | manual:<reason-id> | none`。

## 排序与解锁

1. 优先验证最大交付风险和最短核心循环。
2. 多个无依赖、资源不冲突的 ready 项可以同时 claim；不得自动领取下一项。
3. closeout 只自动解锁 `blocking type=dependencies` 且所有依赖存在、无环并为 done 的项。
4. 手工阻塞、缺失/removed 依赖或依赖环必须人工处理。

