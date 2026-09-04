---
name: orchestrate-lite
description: Authorize a small/no-trigger Codex Workflow V4 change from a short card. Use for typo, docs, or narrow test fixes with no product/data/architecture risk. Invoke explicitly via $orchestrate-lite. Do not use for core_slice product direction, Brief 2A, or high-risk work.
---

# Orchestrate Lite — small / no-trigger authorize

Do not invent a second checkpoint command. Do not skip Independent Reviewer, sealed snapshot, or strict-ff closeout. Do not auto-push, open a PR, merge, or release.

## When to use

- `planning.level` would be small and every risk flag is false.
- Source is a user directive, incident, or maintenance fix — not a new target-release Brief.
- You can name one acceptance criterion and concrete docs/test paths (no `*`/`**`, no `src/` product code).

Otherwise use `$orchestrate-project-task`.

## Authorize

Do not ask the human to write JSON. Translate their request into flags, dry-run, then `--apply` after they confirm. `--source` defaults to `user:lite-authorize`; `--scope-in` / `--scope-out` have safe defaults.

```text
py -3 .codex-workflow/bin/workflow_state.py lite-authorize \
  --task-id MVP-LITE-001 \
  --request "Fix the README typo." \
  --acceptance "README.md no longer contains the typo." \
  --allowed-path README.md \
  --authorized-by <human>

py -3 .codex-workflow/bin/workflow_state.py lite-authorize \
  --task-id MVP-LITE-001 \
  --request "Fix the README typo." \
  --acceptance "README.md no longer contains the typo." \
  --allowed-path README.md \
  --authorized-by <human> \
  --apply
```

`--card-json` remains optional for scripted cards. Extra JSON fields fail closed. Do not mix flags with `--card-json`.

The command derives a schema-valid V4 **governance** record with `checkpoint.mode=not_required`. It refuses globs, product-code paths, a dirty tree, and another live task on the same branch. If a live Brief exists, pass `--requirement-id` explicitly.

- `parallel.mode=single`: occupies the current worktree only if it is clean and unoccupied, then run preflight from this tree.
- `parallel.mode=local_worktree`: do **not** preflight here. Run `workflow_lane.py claim` first, then preflight from the assigned worktree.

```text
py -3 .codex-workflow/bin/workflow_check.py preflight .codex-workflow/state/runs/<task-id>.json
```

Spawn `$implement-project-task` in the occupied/claimed tree. After a clean delivery commit, `$review-project-change` still required. Closeout stays the existing two-phase path and is not this skill.
