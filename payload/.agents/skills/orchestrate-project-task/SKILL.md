---
name: orchestrate-project-task
description: Coordinate Codex Workflow V3 from requirements calibration through isolated local or remote lanes, independent review, deterministic verification, serial integration, and two-phase closeout. Use for non-trivial feature, bug, refactor, migration, or release-scope work. Invoke explicitly via $orchestrate-project-task.
---

# Orchestrate Project Task — V3

Own scope, state and integration; do not impersonate Developer or Reviewer.

## 1. Establish ground truth

1. Read root AGENTS and every file it points to, then the active Requirements Brief, Backlog, relevant task records, code, tests and Git state.
2. Run `py -3 .codex-workflow/bin/workflow_check.py start` and `manual`.
3. Preserve unrelated changes. If the current directory has a lane pointer, resume that exact lane; do not silently claim another task.
4. Before the first implementation, require a human-approved Git baseline. Never infer project facts from the template.

## 2. Run the Requirements 2A feedback loop

For target-release work, create a Requirements Brief from [requirements-brief-template.md](references/requirements-brief-template.md) before approving Backlog:

1. Preserve the user's raw goal and source.
2. Restate users, scenario, outcome, inputs/actions/outputs, core flow, scope, non-goals and risks as confirmed / assumption / unconfirmed.
3. Ask the user to correct it; retain stable REQ IDs and record changed IDs per calibration round.
4. Provide happy, boundary or failure, and explicit non-goal examples.
5. Close every blocking question. Non-blocking questions need owner and resolution target.
6. Run `requirements-snapshot`; ask the user to approve exact brief ID, revision, target release and fingerprint.
7. Write the approval and run `requirements-gate`; copy the same baseline into PROJECT and Backlog.

Do not select a technical stack or start Developer during calibration. Incident, maintenance and explicit direct tasks may use a narrower task contract, but still need scope, non-goals, acceptance, risk and authorization.

## 3. Create and authorize one task contract

Select a durable `ready` Backlog item or record a typed exception. Create `state/runs/<task-id>.json` from [task-record-template.json](references/task-record-template.json). Fill source and requirements baseline, raw request, scope in/out, allowed paths, conservative resource keys, acceptance criteria, risk flags, planning depth, exact base commit and implementation authorization.

- Small: short verifiable steps in the record.
- Medium: ordered record steps and a PLAN summary.
- Large/high-risk: ExecPlan under `state/plans/`; get user approval for scope or irreversible choices.

Run preflight before delegation. All later JSON writes use state/lane commands and generation CAS; never hand-edit the record to manufacture a state.

## 4. Choose single or isolated lane

`parallel.mode=single` preserves the simple path. For approved local parallelism, `parallel.mode=local_worktree` must pass the manual lock probe. Claim one task per branch/worktree:

```text
py -3 .codex-workflow/bin/workflow_lane.py claim <task-id> --base main
py -3 .codex-workflow/bin/workflow_lane.py claim <task-id> --base main --apply
```

Review the dry-run paths and claims first. Do not claim conflicting task/resource/path/branch/worktree or exceed max lanes. Use `adopt` only for an intentional existing branch; dirty adoption requires the printed diff token. Give each Developer only its lane worktree, record, allowed paths and resource keys. Multiple Developers may run concurrently only in separate worktrees.

When local registry/claims/queue state is missing or unreadable, run `workflow_lane.py rebuild` first. It reconstructs only pointer-and-record-backed local lanes, marks every reconstructed owner stale, and requires an explicit `recover --takeover` before work resumes. The same recovery applies when queue or `refresh-base` has updated its task record but has not finished runtime writes: use `rebuild --apply`, inspect the reconstructed state, then use `recover <lane-id> --takeover --apply`. When a target ref already contains closeout state and confirm stops while releasing runtime claims, run `workflow_state.py reconcile <record> --target-ref <ref> --apply` instead.

For different machines, prefer `remote_preassigned`: Coordinator writes random owner UUID, generation, claim and unique branch, then explicitly commits/pushes assignment. The assigned machine fetches, checks out that exact branch, then runs `workflow_lane.py resume-remote <record> --owner-id <assigned-uuid> --apply`; branch, owner, claim and generation mismatches must stop work. Optional `remote_claimed` requires atomic multi-ref support. Its heartbeat refuses an expired lease. Stale takeover requires a clean checkout of the exact remote task ref, a new owner UUID, `--approved-by`, and `--approval-ref`; it atomically advances the task ref, claim and resource refs. An active owner can run `remote-handoff` with a target owner UUID; the recipient fetches the updated task branch and runs `resume-remote`. Every failed CAS freezes that lane and preserves local commits. These commands do not grant product push/merge permission.

## 5. Delegate Developer and Reviewer

Spawn the project `developer` in the assigned worktree with the raw request, record, requirements/acceptance IDs, constraints and ExecPlan. Require `$implement-project-task`. It must create one clean delivery commit, then use `record-developer` with exact commands and handoff evidence.

Spawn a different project `reviewer` against that same worktree, delivery commit and snapshot. Give Developer evidence as untrusted input. Reviewer uses `$review-project-change`, remains read-only and reconstructs the acceptance checklist independently. Coordinator records its report using `record-review`.

Return P0/P1 and unaccepted P2 findings to the same lane Developer. Any fix that changes delivery content invalidates the old snapshot and requires a new Developer evidence + full Reviewer pass.

## 6. Complete local verification

Record acceptance and process retrospective with `complete-task`; every Rule Proposal needs a final disposition. Then:

```text
py -3 .codex-workflow/bin/workflow_check.py gate <record>
py -3 .codex-workflow/bin/workflow_state.py mark-verified <record> --apply
```

Report `verified`, not done. Multiple verified lanes may coexist.

## 7. Prepare serial integration

For local bootstrap, first record the user's exact task/target/snapshot/delivery-hash approval, and confirm the task is in the enabled allowlist. For the standard path select remote PR/CI:

```text
py -3 .codex-workflow/bin/workflow_state.py prepare-integration <record> --mode local_bootstrap|remote_pr_ci --apply
py -3 .codex-workflow/bin/workflow_lane.py queue <lane-id> --apply
```

For local worktree lanes, queue priority orders serial closeout; lower values run first and only the queue head may prepare local closeout. Queue admission compares each sealed delivery snapshot's actual changed paths against every queued lane, including case and Unicode-equivalent paths under the cross-platform canonical form. A conflict stops the later lane before its tracked integration state changes. Commit the prepared/queued task record to its lane branch before the Integrator performs the external ff-only product integration. Only one Integrator may advance the target branch. The workflow never auto-pushes, opens/merges PRs, resolves conflicts, force-updates refs, releases or deploys. Main advancement, rebase, conflict resolution or CI repair that changes content returns the task to Developer and invalidates verification. After a local lane has been manually rebased onto the advanced target, require a clean worktree and run:

```text
py -3 .codex-workflow/bin/workflow_lane.py refresh-base <lane-id> --base main --apply
```

`refresh-base` removes the obsolete queue entry and resets the base-bound Developer, Review, verification, approval, acceptance and integration state. Delegate a new delivery commit and the complete evidence chain before queueing again.

## 8. Two-phase closeout

- Local bootstrap: external ff-only product integration, then `prepare-local-closeout` from a clean target worktree.
- Remote: external push/PR/CI/human merge, fetch, validate strict-ff evidence JSON, then `prepare-remote-closeout` from the latest target baseline; its closeout-only commit goes through an external PR/CI/human merge.

Prepare writes done/integration evidence/dependency unlock into one closeout commit and records a canonical state fingerprint. The prepared SHA is printed and kept in local audit because a Git-tracked record cannot contain its own commit SHA. It does not release claims. After the configured target ref contains that exact commit:

```text
py -3 .codex-workflow/bin/workflow_state.py confirm-closeout <record> --target-ref <ref> --closeout-commit <sha>
py -3 .codex-workflow/bin/workflow_state.py confirm-closeout <record> --target-ref <ref> --closeout-commit <sha> --apply
```

Use `reconcile` after an interrupted closeout. Only confirm releases matching runtime claims. It never deletes branch/worktree. Output next-ready candidates, but do not auto-claim them.

## 9. Report exact distinctions

Report behavior, exact tests, review, integration/remote evidence, recovery state, process proposals and remaining risk. Distinguish verified vs local done vs remote-synced vs released. Never present a cooperative lock, Hook, local JSON or Agent identity as a security trust root.
