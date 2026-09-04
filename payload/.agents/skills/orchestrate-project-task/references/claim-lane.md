# Claim and lane isolation — load at claim time

Open these only when claiming, adopting, or resuming a lane. They are not session-start reading.

1. Lane invariants: `.codex-workflow/protocol/AGENTS.md` (lane section).
2. Claim, worktree, heartbeat, and recover steps: `.codex-workflow/docs/WORKFLOW.md` (single vs local worktree).

Then dry-run and apply:

```text
py -3 .codex-workflow/bin/workflow_lane.py claim <task-id> --base main
py -3 .codex-workflow/bin/workflow_lane.py claim <task-id> --base main --apply
```

Review the dry-run paths first. Do not claim conflicting task/resource/path/branch/worktree or exceed max lanes. Use `adopt` only for an intentional existing branch; dirty adoption requires the printed diff token. Multiple Developers may run concurrently only in separate worktrees.

When local registry/claims/queue state is missing or unreadable, run `workflow_lane.py rebuild` first, then `recover --takeover`. Interrupted closeout confirm uses `workflow_state.py reconcile`. Remote machines prefer `remote_preassigned` then `resume-remote`; optional `remote_claimed` needs atomic multi-ref support. These commands do not grant product push/merge permission.

Do not auto-push, open a PR, merge, force-update, or delete branch/worktree.
