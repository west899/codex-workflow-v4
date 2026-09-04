# Two-phase closeout — load after verified

Open these only when preparing or confirming closeout. They are not session-start reading.

1. Integration modes and two-phase closeout: `.codex-workflow/docs/WORKFLOW.md`.
2. Closeout must stay fail-closed: Independent Reviewer pass, sealed snapshot, strict-ff evidence, no GitHub-review substitution, closeout is not `released`.

- Local bootstrap: external ff-only product integration, then `prepare-local-closeout` from a clean target worktree.
- Remote: external push/PR/CI/human merge, fetch, validate strict-ff evidence JSON, then `prepare-remote-closeout`. Optional `provider-receipt` is additive only.

```text
py -3 .codex-workflow/bin/workflow_state.py prepare-local-closeout <record> --target-ref <ref> --result-commit <sha>
py -3 .codex-workflow/bin/workflow_state.py prepare-remote-closeout <record> --evidence-json <file>
py -3 .codex-workflow/bin/workflow_state.py confirm-closeout <record> --target-ref <ref> --closeout-commit <sha>
py -3 .codex-workflow/bin/workflow_state.py confirm-closeout <record> --target-ref <ref> --closeout-commit <sha> --apply
```

Use `reconcile` after an interrupted closeout. Confirm releases matching runtime claims and never deletes branch/worktree. For `remote_claimed`, `remote-release` must bind a fresh `--expected-claim-oid`.

The workflow never auto-pushes, opens or merges PRs, or deploys.
