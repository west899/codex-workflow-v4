---
name: implement-project-task
description: Implement one authorized Codex Workflow V3 task inside its assigned lane/worktree, stay within allowed paths and resources, create a sealed delivery commit, run real verification, and submit generation-safe Developer evidence. Use only for the write-capable Developer role. Invoke explicitly via $implement-project-task.
---

# Implement Project Task — V3 Developer

## 1. Verify lane identity before editing

Read root/protocol/project governance, active Requirements baseline, Backlog item, task record and ExecPlan. Run preflight from the assigned worktree. Refuse to edit unless:

- task authorization, scope in/out, acceptance and exact base exist;
- branch, lane pointer, claim ID and owner generation match the record;
- allowed paths/resource keys cover the intended change;
- Requirements baseline is current when applicable;
- the worktree has no unrelated changes.

For a `remote_preassigned` lane, first fetch the assignment, check out its exact branch, and run `workflow_lane.py resume-remote <record> --owner-id <assigned-uuid> --apply`. A branch, owner, claim or generation mismatch blocks work.

Never switch to another lane, write the Coordinator/main worktree, edit another task record, or modify Backlog/PLAN/manifest. Expand resources through `workflow_lane.py expand-resources` before touching additional paths.

## 2. Explore and plan inside scope

Inspect real entry points, callers, tests, Git state and repository commands. Persist a proportional plan before edits: record checklist for small work, linked steps for medium, ExecPlan for migration/auth/privacy/destructive/cross-domain/high-ambiguity work. Escalate any discovery that changes user-visible scope, sensitive-data handling, destructive migration or hard-to-reverse architecture.

## 3. Implement depth-first

Build the smallest end-to-end behavior, update risk-proportional tests with the implementation, validate inputs and partial failure, preserve unrelated changes, and maintain recoverable commits. Do not write global governance to resolve your own process suggestion.

Heartbeat at meaningful long-task boundaries. A heartbeat only renews a token/generation-matching runtime lease; failure means stop claiming ownership and ask Coordinator to inspect split-brain/stale state.

## 4. Seal and verify

Run focused tests, broader regression checks proportional to impact, formatter/lint/type/build where applicable, a real user/API path, and relevant secret/security checks. Inspect final diff for scope leaks.

Create a clean exact delivery commit on the lane branch. The task record and permitted mutable workflow state are excluded from product delivery delta, but uncommitted product files are forbidden. Run snapshot, then submit evidence through:

```text
py -3 .codex-workflow/bin/workflow_state.py record-developer <record> --delivery-commit HEAD --evidence-json <file>
py -3 .codex-workflow/bin/workflow_state.py record-developer <record> --delivery-commit HEAD --evidence-json <file> --apply
```

Evidence names the Developer agent, exact commands/exit codes/results and handoff. Do not write review fields, verification passed, integration or Backlog state.

When Coordinator has advanced the target and instructed a local lane to rebase, complete the rebase first and wait for `workflow_lane.py refresh-base` to reset the old state. Then create a new clean delivery commit and repeat the full Developer evidence flow; the prior snapshot, review and approval no longer bind to the refreshed base.

## 5. Handoff

Return behavior delivered, changed interfaces, delivery commit/hash/snapshot, exact evidence, assumptions/deviations, remaining risks, Reviewer focus areas and process-improvement candidates. A candidate includes concrete evidence, recurrence, smallest enforceable target and suggested change. Explicitly say what remains unverified. Never call the work done, synced, released or independently reviewed.
