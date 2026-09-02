# V4 Phase C keep-strict-ff 封存

> PC-004 交付。本 revision 产品集成算法保持 V3 strict-ff。

## 封存结论

`prepare-remote-closeout` 继续要求：

1. `merge_strategy == "ff"`
2. `result_commit == pr_head_commit`
3. `pr_head_commit` 等于已验证 `delivery_commit`
4. result 是 `target_parent` 的 fast-forward 后代
5. 配置的 target ref 含 result
6. 每条 CI check 的 `status == "success"`（`skipped` / `neutral` 失败）

只读 provider receipt 可以附加，不能让上述任一项缺失时 closeout 成功，也不能替代 Independent Reviewer。

附加 receipt 还必须：

- `commit_sha` 与 `reviews[].commit_sha` 等于当前 `pr_head_commit`，`snapshot_id` 等于已验证 snapshot
- `protection.admin_bypass_allowed` 为 false（EQ-005）
- `protection.dismiss_stale_reviews` 与 `require_last_push_approval` 为 true（EQ-002）
- `substitutes_reviewer` 为 false

`prepare-remote-closeout` 另外要求 task `review.status == pass`。这是 Independent Reviewer 的本地证明，不是 GitHub required reviews。合作式旗标（如 `github_review_equivalent`）只是额外拒绝，不能代替 `record-review`。

非 ff 产品集成、删除本地证明、把 branch protection 或 merge queue 当成 Reviewer，均不在本 revision。没有半套 adapter 命令接受 squash / merge-commit / merge-queue result。

## 命令

```text
py -3 .codex-workflow/bin/workflow_check.py provider-receipt <receipt.json>
py -3 .codex-workflow/bin/workflow_state.py prepare-remote-closeout <record> --evidence-json <file>
```

receipt 校验默认只读。closeout 仍默认 dry-run，`--apply` 才写入。
