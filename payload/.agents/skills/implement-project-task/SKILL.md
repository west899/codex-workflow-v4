---
name: implement-project-task
description: Plan and implement a confirmed project task with repository exploration, proportional execution planning, incremental code changes, tests, real behavior validation, recovery guidance, and an evidence-based handoff. Use only for the write-capable Developer role after scope and acceptance criteria are established. Invoke explicitly via $implement-project-task.
---

# Implement Project Task

Act as the write-capable Developer. Your output is a working, reviewable change, not code volume.

## 1. Pass the Preflight Gate

Read `AGENTS.md`, `PROJECT.md`, `PLAN.md`, `DECISIONS.md`, the linked Backlog item when applicable, the raw request, task record, and any active ExecPlan.

Do not plan implementation or edit code unless all are true:

- the Coordinator supplied a task-record path;
- the repository has a valid baseline commit;
- `implementation_authorization.authorized` is `true`, with source and author;
- scope, non-goals, and acceptance criteria are non-empty;
- project stage and confirmed facts do not prohibit the work;
- required human approval for scope, sensitive-data, destructive, or irreversible choices is present.

Run `python3 scripts/workflow_check.py preflight <task-record>`. If any condition fails, stop and return the missing gate; do not prepare code while waiting.

## 2. Explore Before Planning

Inspect the repository rather than guessing:

1. Map relevant entry points, modules, data flow, and tests.
2. Find existing patterns and ownership boundaries.
3. Check Git status and preserve unrelated changes.
4. Identify exact build, test, lint, type-check, and run commands from repository sources.
5. Verify external technical facts from primary documentation when they may have changed.

Complete exploration and persist the chosen checklist or plan in the task record before any edit, including small tasks.

## 3. Plan Proportionally

- **Small:** store a short ordered checklist with acceptance and verification in the task record.
- **Medium:** store concrete steps in the task record and link it from `PLAN.md`; each step must produce an observable intermediate result.
- **Large/high-risk:** create or maintain an ExecPlan using [exec-plan-template.md](references/exec-plan-template.md).

A useful plan names files and behavior, explains why the sequence is safe, includes tests and recovery, and can be resumed by another agent. Avoid plans that merely restate the request.

Large/high-risk classification is mandatory for any migration, auth, authorization, privacy, production deployment, public API compatibility, destructive operation, cross-domain change, or change with no safe rollback. The Developer may raise planning depth but may not lower these triggers.

## 4. Implement Depth-First

1. Build the smallest vertical slice that exercises the real path.
2. Add or update tests at the same time as behavior.
3. Follow existing architecture and dependencies before introducing new ones.
4. Parse and validate data at system boundaries.
5. Keep changes scoped; separate unrelated cleanup.
6. Maintain a runnable state at milestone boundaries.
7. Update the active plan when discoveries change the approach.

For risky changes, prefer additive and reversible transitions. Include migration, rollback, retry, compatibility, and partial-failure behavior where relevant.
Do not commit during the task unless the Coordinator or user explicitly requests it; the final worktree hash is computed against the authorized baseline commit.

## 5. Verify, Do Not Assert

Run the repository's relevant:

- focused tests, then broader tests proportional to blast radius;
- formatter, linter, and type checker;
- build or packaging step;
- real user-facing or API flow;
- security and secret checks for sensitive changes.

Inspect the final diff for accidental scope, debug code, weak error handling, missing tests, and documentation drift. Record exact commands and observed results. Mark anything not run as unverified.

Capture `python3 scripts/workflow_check.py snapshot <task-record>` after the final edit. It hashes the complete delivery change from the authorized `base_commit`, including task commits, while excluding mutable workflow state (`.agent/`, `PLAN.md`, and `docs/MVP_BACKLOG.md`). Store the hash, command, exit code, and concise result in the task record. Do not set review fields.

## 6. Handoff

Return:

1. **Behavior delivered**
2. **Files and interfaces changed**
3. **Verification evidence**
4. **Assumptions and deviations**
5. **Remaining risks**
6. **Reviewer focus areas**
7. **Process observations / Rule Proposal candidates**

For each process candidate, state the problem, concrete task evidence, whether it is a first occurrence or repeated pattern, the suggested target (`task-only`, `AGENTS.md`, a Skill, script/Hook/CI, or `DECISIONS.md`), and the proposed change. Explicitly say when no reusable process issue was observed.

Do not edit `AGENTS.md`, project Skills, workflow scripts, Hooks, or CI merely because you discovered a process issue. The Coordinator records and triages the proposal, and the user approves any permanent rule.

Do not describe the change as complete if acceptance evidence is missing. Do not perform the independent review yourself.
