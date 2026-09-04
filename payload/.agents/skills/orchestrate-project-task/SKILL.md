---
name: orchestrate-project-task
description: Coordinate Codex Workflow V4 from requirements calibration through a focus core slice, human product decisions, isolated lanes, independent review, verification, serial integration, and two-phase closeout. Use for non-trivial feature, bug, refactor, migration, or release-scope work. Invoke explicitly via $orchestrate-project-task.
---

# Orchestrate Project Task — Codex Workflow V4

New work uses `task-record-v4`. Keep existing V3 records on the V3 closeout path. Do not invent a second checkpoint command or product state machine. `workflow_lane.py` never carries product semantics.

Own scope, state and integration; do not impersonate Developer or Reviewer.

## 1. Establish ground truth

1. Read the root AGENTS entry block and `.codex-workflow/state/STATUS.md`. Do not read `.codex-workflow/docs/WORKFLOW.md` or the full protocol handbook before the current phase needs them.
2. Run `py -3 .codex-workflow/bin/workflow_check.py start`. Run `manual` and `status` when coordinating or authorizing, not as a session-start handbook dump.
3. Preserve unrelated changes. If the current directory has a lane pointer, resume that exact lane; do not silently claim another task.
4. Before the first implementation, require a human-approved Git baseline. Never infer project facts from the template. Load PROJECT / PLAN / DECISIONS only when authorizing or checking architecture. For small/no-trigger user-directive work, use `$orchestrate-lite` / `lite-authorize` instead of Brief 2A.

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

### Approved Requirements changed

When an approved Brief changes revision/fingerprint or is replaced by a new Brief, do not hand-edit PROJECT/Backlog baseline or task JSON. Run `requirements-impact <revised-brief> --json`, review the stable-ID diff and every action, then dry-run and apply `apply-requirements-impact` with the exact approved fingerprint. This writes the report, updates both baselines, blocks unstarted affected/unmapped Backlog items, preserves done history, and refreshes STATUS.

An active task in the report is stopped, not implicitly approved to continue. After the Coordinator commits the impact files and the lane is manually rebased (use `refresh-base` when its base changed), obtain a human JSON decision bound to the report `analysis_id` with `decision: continue`, approver/time/source and a rationale that the task contract remains valid. Only then run `resolve-requirements-impact <record> --analysis-id <id> --decision-json <file> --apply`. It binds the new baseline and invalidates all old delivery/review/acceptance/approval/integration evidence, so delegate the full Developer → Reviewer → gate chain again. For stop or rewrite, leave the task blocked and follow lane recovery/abandon rather than resolve.

## 3. Create and authorize one V4 task contract

Select a durable `ready` Backlog item or record a typed exception. For new work create `state/runs/<task-id>.json` from [task-record-v4-core-template.json](references/task-record-v4-core-template.json) (supporting work from [task-record-v4-supporting-template.json](references/task-record-v4-supporting-template.json)). Historical V3 tasks already in progress keep [task-record-template.json](references/task-record-template.json) until V3 closeout.

Before authorization, confirm with the human:

- the current `focus_slice_id` and that supporting work only serves that focus;
- the observation recipe (UI / API / CLI / data) and what is real vs temporary;
- any blocking product or architecture decision card (2–3 existing options, no invented options);
- the project architecture baseline is approved; do not generate or approve a baseline yourself.
- Backlog focus metadata lists `kind` / `focus_slice_id` / `supports_task_id` and stays within the unconfirmed-direction core-slice WIP;
- future candidates stay out of the approved Must Requirements contract;
- pending/queued recovery is abandon-only (no dequeue/reopen, no forged done).

Fill source and requirements baseline, raw request, scope in/out, allowed paths, conservative resource keys, acceptance criteria, risk flags, planning depth, exact base commit, `delivery_contract`, and implementation authorization.

- Small: short verifiable steps in the record.
- Medium: ordered record steps and a PLAN summary.
- Large/high-risk: ExecPlan under `state/plans/`; get user approval for scope or irreversible choices.

Run preflight before delegation. `requirements-v1`、`task-record-v3`、`task-record-v4`（含 `lane-v1`）、`developer-evidence-v1`、`review-evidence-v1` 和 `remote-claim-v1` 都是强制结构门禁：Brief/record/evidence/claim 在对应读写边界会 fail closed 校验，错误时修复源数据或受管 schema/实现并补测试，绝不通过手改 JSON 绕开。All later JSON writes use state/lane commands and generation CAS; never hand-edit the record to manufacture a state.

## 4. Choose single or isolated lane

Before `claim` / `adopt` / `resume-remote`, follow [claim-lane.md](references/claim-lane.md). Do not load `.codex-workflow/docs/WORKFLOW.md` or protocol lane invariants until that claim step.

`parallel.mode=single` preserves the simple path. For approved local parallelism, `parallel.mode=local_worktree` must pass the manual lock probe. Give each Developer only its lane worktree, record, allowed paths and resource keys. Recovery, remote claim, and rebuild steps stay in [claim-lane.md](references/claim-lane.md).

## 5. Delegate Developer and Reviewer

Spawn the project `developer` in the assigned worktree with the raw request, record, requirements/acceptance IDs, constraints and ExecPlan. Require `$implement-project-task`. It must create one clean delivery commit, then `record-developer` using [developer-evidence.md](../implement-project-task/references/developer-evidence.md); open `.codex-workflow/docs/WORKFLOW.md` only at that evidence step.

For a V4 task with `checkpoint.mode=required`, do not start independent Review until the current snapshot has a matching observation receipt and a human product-direction decision. Show STATUS / the decision card, not raw JSON. `changes_requested` returns the lane to Developer; a new snapshot needs a new observation.

Spawn a different project `reviewer` against that same worktree, delivery commit and snapshot. Give Developer evidence as untrusted input. Reviewer uses `$review-project-change`, remains read-only, reconstructs the acceptance checklist independently, and records one `confirmed/narrowed/rejected/unverified` assessment for every exact `evidence_fingerprint`. Coordinator records its report using `record-review`; `pass` is allowed only when every claim is `confirmed`.

Return P0/P1 and unaccepted P2 findings to the same lane Developer. Any fix that changes delivery content invalidates the old snapshot and requires a new Developer evidence + full Reviewer pass.

Keep already integrated legacy evidence read-only. For any unfinished task with pre-Contract Developer or Review evidence, return to the appropriate evidence step and record Contract v1 again before Review, `complete-task`, gate or integration. Never infer claims, scopes or fingerprints from an old free-text handoff.

## 6. Complete local verification

Record acceptance and process retrospective with `complete-task`; every Rule Proposal needs a final disposition. Then:

```text
py -3 .codex-workflow/bin/workflow_check.py gate <record>
py -3 .codex-workflow/bin/workflow_state.py mark-verified <record> --apply
```

Report `verified`, not done. Multiple verified lanes may coexist.

## 7. Prepare serial integration

For local bootstrap, first record the user's exact task/target/snapshot/delivery-hash approval. Confirm the enabled policy has a unique ordered allowlist, its `expires_after_task` is a Backlog task in that list, the current task is at or before that cutoff, and the cutoff task is not durable `done`. Preparation, integration preflight, queue admission, and local closeout enforce the same rule without record/queue/closeout mutation on failure. After expiry, select remote PR/CI:

```text
py -3 .codex-workflow/bin/workflow_state.py prepare-integration <record> --mode local_bootstrap|remote_pr_ci --apply
py -3 .codex-workflow/bin/workflow_lane.py queue <lane-id> --apply
```

For local worktree lanes, queue priority orders serial closeout; lower values run first and only the queue head may prepare local closeout. Queue admission compares each sealed delivery snapshot's actual changed paths against every queued lane, including case and Unicode-equivalent paths under the cross-platform canonical form. A conflict stops the later lane before its tracked integration state changes. Commit the prepared/queued task record to its lane branch before the Integrator performs the external ff-only product integration. Only one Integrator may advance the target branch. For an external integration spanning commands, acquire `workflow_lane.py lock-acquire integrator --apply`, preserve its token/generation, heartbeat it at long boundaries, pass both values to prepare/confirm closeout, then release it. A stale Coordinator/Integrator lease must be inspected and explicitly taken over with known old token/generation plus human approval; never delete its runtime JSON or wait for TTL to clear it. The workflow never auto-pushes, opens/merges PRs, resolves conflicts, force-updates refs, releases or deploys. Main advancement, rebase, conflict resolution or CI repair that changes content returns the task to Developer and invalidates verification. After a local lane has been manually rebased onto the advanced target, require a clean worktree and run:

```text
py -3 .codex-workflow/bin/workflow_lane.py refresh-base <lane-id> --base main --apply
```

`refresh-base` removes the obsolete queue entry and resets the base-bound Developer, Review, verification, approval, acceptance and integration state. Delegate a new delivery commit and the complete evidence chain before queueing again.

## 8. Two-phase closeout

After verified, follow [closeout.md](references/closeout.md). Load the two-phase closeout chapters in `.codex-workflow/docs/WORKFLOW.md` only at this closeout step. Closeout is not `released` and must not treat GitHub review as Independent Reviewer.

## 9. Report exact distinctions

Report behavior, exact tests, review, integration/remote evidence, recovery state, process proposals and remaining risk. Distinguish verified vs local done vs remote-synced vs released. Also report `workflow_check.py status` separately: a generated STATUS snapshot is current only when its fingerprint and current Requirements contract both match. Never present a cooperative lock, Hook, local JSON or Agent identity as a security trust root.
