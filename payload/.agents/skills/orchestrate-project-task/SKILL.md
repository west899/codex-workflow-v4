---
name: orchestrate-project-task
description: Orchestrate non-trivial project work through task definition, proportional planning, a separate developer agent, an independent read-only reviewer agent, deterministic checks, and evidence-based completion. Use for feature work, bug fixes, refactors, migrations, security-sensitive changes, or any multi-file code task. Do not use for simple discussion or a tiny documentation-only edit. Invoke explicitly via $orchestrate-project-task.
---

# Orchestrate Project Task

Own the workflow, not the implementation. Keep project-scope decisions with the user, code changes with the Developer, and acceptance review with the Reviewer.

## 1. Establish Ground Truth

1. Read `AGENTS.md`, `PROJECT.md`, `PLAN.md`, `DECISIONS.md`, the relevant `docs/WORKFLOW_V2.md` steps, and `docs/MVP_BACKLOG.md` when it exists.
2. Run `python3 scripts/workflow_check.py start`.
3. Inspect the relevant repository area, tests, and Git state. Before the first code task, require an approved baseline commit; do not delegate implementation without a valid `HEAD`.
4. Write a task contract containing:
   - task source: approved MVP Backlog item, incident, maintenance, or direct user instruction;
   - user-visible goal;
   - confirmed facts and open questions;
   - scope and explicit non-goals;
   - constraints and risks;
   - observable acceptance criteria;
   - required verification evidence.
5. For target-release delivery work, require a human-approved `docs/MVP_BACKLOG.md` created from [mvp-backlog-template.md](references/mvp-backlog-template.md). Start from the latest approved main baseline on a task branch or isolated worktree, select one `ready` item, mark it `active`, and bind its ID in the task record. Exceptions must be typed as incident, maintenance, or user directive with a concrete priority reason.
6. Create `.agent/runs/<task-id>.json` from [task-record-template.json](references/task-record-template.json). Fill the source, request, scope, acceptance criteria, planning level, risk flags, implementation authorization, and base commit. Write its relative path to `.agent/active-task`, then run `python3 scripts/workflow_check.py preflight <record>` before spawning the Developer.
7. Ask the user only when an answer changes project direction, Backlog priority, sensitive-data handling, a destructive operation, or a hard-to-reverse choice.
8. This V2 control plane supports exactly one active task and one write owner. Do not start another task while `.agent/active-task` exists.

## 2. Choose Planning Depth

Classify the task before delegation:

- **Small:** one localized behavior, known pattern, low risk. Store a short checklist in the task record.
- **Medium:** multiple files or meaningful design choice. Store ordered, verifiable steps in the task record and link it from `PLAN.md`.
- **Large/high-risk:** cross-domain change, unfamiliar system, migration, auth, privacy, permissions, deployment, public API, or substantial ambiguity. Create `.agent/plans/<date>-<slug>.md` using the Developer skill's ExecPlan template. Obtain user approval before implementation when project scope or irreversible risk is involved.

Planning is proportional. Do not create an ExecPlan for trivial edits; do not compress risky work into a vague checklist.

## 3. Delegate Implementation

Explicitly spawn the project custom agent `developer`. Give it:

- the raw user request;
- the task contract;
- the task-record path;
- relevant confirmed decisions;
- the path to any active ExecPlan.

Do not prescribe an implementation unless it is already an approved decision. Require the Developer to use `$implement-project-task`, keep the plan and task record current, run verification, and return a handoff with changed files, evidence, assumptions, remaining risks, and any process-improvement candidates.

Only one write-capable agent owns the working tree at a time.

If the project custom agent cannot be spawned in the current Codex surface, use
a generic child agent and include the complete `developer_instructions` from
`.codex/agents/developer.toml`. Record the fallback and its agent ID in the task
record. Do not silently implement in the Coordinator thread.

## 4. Delegate Independent Review

After implementation stops, explicitly spawn the project custom agent `reviewer`. Give it:

- the raw user request and acceptance criteria;
- `PROJECT.md`, `PLAN.md`, and relevant decisions;
- the base revision and current diff;
- the task-record path and Developer worktree hash;
- the Developer's verification evidence, labeled as untrusted input.

Require `$review-project-change`. Do not reveal the intended implementation or ask the Reviewer to confirm the Developer's reasoning. The Reviewer must reconstruct expected behavior independently and remain read-only. After it returns, record the Reviewer agent ID, reviewed hash, findings, result, and process-improvement candidates without changing the substance.

If the project custom Reviewer cannot be spawned, use a generic child agent with
the complete `.codex/agents/reviewer.toml` instructions and the narrowest
available read-only permissions. Record the fallback. The configured read-only
sandbox is a default guard, not an identity or operating-system trust boundary;
parent runtime permission overrides can still apply.

## 5. Resolve Findings

- Return all P0/P1 findings to the Developer for correction.
- Resolve P2 findings now unless they are explicitly accepted and recorded.
- After fixes, run a fresh Reviewer pass on the new diff.
- Do not let the Developer dismiss its own review findings.
- If Developer and Reviewer disagree on required behavior or accepted risk, escalate to the user.

## 6. Run the Process Retrospective

Before completion, inspect the Developer handoff, Reviewer report, task history, failed checks, and user corrections. Answer the three task-record questions:

- Did a problem repeat or reveal a reusable failure pattern?
- Was an instruction, role boundary, or acceptance rule missing or ambiguous?
- Could a deterministic script, Hook, test, or CI check prevent recurrence?

For every positive answer, create a `rule_proposals` entry from [rule-proposal-template.json](references/rule-proposal-template.json) with concrete evidence. Classify its target:

- `task-only` for a one-off lesson that should not become permanent policy;
- `AGENTS.md` for repository-wide behavioral boundaries;
- `skill:<name>` for role-specific procedures or reusable judgment;
- `script:<path>` or `CI` for deterministic enforcement;
- `DECISIONS.md` for an important rationale or correction history.

The Coordinator may mark a one-off proposal `recorded`. Permanent targets require the user to choose `rejected`, `deferred`, or approval for implementation. Prefer implementing permanent governance changes as a separate follow-up task. If the user explicitly keeps them in the current task, apply them before the final snapshot, then repeat Developer verification and independent review because governance files are part of the delivery hash. Mark approved changes `implemented` with the affected paths. The proposer and Coordinator cannot approve them on the user's behalf.

Complete `process_retrospective` even when no proposal is found; record a concise evidence-based summary rather than a generic “none”.

## 7. Close the Task

1. Capture the delivery-change hash with `python3 scripts/workflow_check.py snapshot <task-record>`. It is calculated from the authorized `base_commit` and remains comparable after task commits. Mutable workflow state (`.agent/`, `PLAN.md`, and `docs/MVP_BACKLOG.md`) is excluded and validated separately.
2. Ensure the Reviewer reviewed that exact hash.
3. Ensure the process retrospective is complete and every Rule Proposal has a final disposition.
4. Set the task record to `completed` only after every acceptance criterion passes, required human approvals exist, and P0-P2 findings are resolved or explicitly accepted by the user.
5. Run `python3 scripts/workflow_check.py gate .agent/runs/<task-id>.json`.
6. Keep `.agent/active-task` through integration so a CI or merge correction cannot lose task context.
7. Mark the linked Backlog item `verified`, attach the task-record path, and prepare the unchanged verified diff for commit/PR; update `PLAN.md`; update `PROJECT.md` or `DECISIONS.md` only when their facts changed.
8. Report only:
   - delivered behavior;
   - verification evidence;
   - review result;
   - process improvements proposed and their disposition;
   - unresolved risks or human approvals still required.

Never equate a clean review with proof of correctness. Automated checks and human approval remain separate gates.

## 8. Integrate and Continue

1. Submit the exact verified change through the project's branch, PR, and protected CI process.
   - Bootstrap exception: the first OPS task that establishes CI or branch protection may merge without those controls only after local gate, independent review, and explicit user approval recorded in `human_approvals`. The exception expires once CI exists.
2. If rebase, conflict resolution, or CI repair changes code content, set the task back to `in_progress`, then repeat Developer verification, independent review, retrospective update when relevant, and a fresh gate against the full change from `base_commit`.
3. After human-approved merge, record the PR or merged commit in the Backlog, change the item from `verified` to `done`, then remove `.agent/active-task`.
4. Start the next `ready` item from the updated main baseline. Enter release only after all Must items and release gates are complete.
