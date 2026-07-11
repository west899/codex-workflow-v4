# ExecPlan: <task title>

> Task: `<task-id>`｜Lane: `<lane-id>`｜Branch: `<branch>`｜Snapshot: pending

Use for migrations, auth/privacy/permissions, public compatibility, destructive or cross-domain changes, and work whose safe recovery is not obvious. Save under `.codex-workflow/state/plans/<date>-<slug>.md`.

## Goal and observable acceptance

- User-visible result:
- Requirements / acceptance IDs:
- Explicit non-goals:
- Allowed paths and resource keys:

## Ground truth

- Exact base commit:
- Relevant entry points, data/control flow and tests:
- Confirmed facts:
- Assumptions and how to verify them:
- Unrelated work to preserve:

## Risks and authorization

| Risk | Failure/impact | Prevention | Recovery | Required human approval |
| --- | --- | --- | --- | --- |

## Ordered milestones

Each milestone must leave a runnable or recoverable state and name its observable proof.

1. <milestone, files, behavior, proof>
2. <milestone, files, behavior, proof>

## Verification

- Focused tests:
- Broader regression checks:
- Lint/type/build/package:
- Real user/API flow:
- Security/secret/risk checks:
- Final clean delivery commit:
- `workflow_check.py snapshot <record>`:
- Developer evidence JSON:
- Independent Reviewer focus:

## Failure and recovery

- Safe retry boundary:
- Partial-write handling:
- Migration rollback/compatibility:
- Rescue branch/patch strategy:
- Conditions that invalidate the snapshot:

Do not prescribe automatic push, PR, merge, force, worktree/branch deletion, release or deployment. Any scope/resource expansion returns to Coordinator before editing.

