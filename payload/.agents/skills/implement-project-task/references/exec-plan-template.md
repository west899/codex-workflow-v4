# ExecPlan Template

Use this template for large, ambiguous, cross-domain, or high-risk work. Save active plans under `.agent/plans/<date>-<slug>.md`.

An ExecPlan must be self-contained enough that a fresh agent can resume from the repository and this file alone. Keep it current during implementation.

## Required Sections

```md
# <Action-oriented task title>

Status: proposed | approved | in-progress | blocked | completed
Owner: developer
Updated: <ISO date/time>
Approved by: <human or coordinator>
Approved at: <ISO date/time>
Approval source: <user message, decision ID, or task-record authorization>

## Purpose and Observable Outcome

Explain what the user can do after this change and how to observe it.

## Confirmed Inputs

List requirement or decision IDs and relevant repository facts. Label assumptions explicitly.

## Scope and Non-Goals

State what changes and what intentionally does not.

## Context and Code Map

Name relevant files, entry points, data flow, tests, and non-obvious terms.

## Plan of Work

Describe ordered milestones. Each milestone must leave a working, independently verifiable result.

## Progress

- [ ] <timestamp> Step; expected evidence; actual result; current blocker/next action

## Validation and Acceptance

For every acceptance criterion, record:

| Criterion | Command or flow | Expected | Actual | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| AC-001 | ... | ... | ... | pending | ... |

Include `python3 scripts/workflow_check.py start`, `snapshot <task-record>`, and final `gate <task-record>` as workflow evidence.

## Risks, Recovery, and Rollback

Cover partial failure, retries, migrations, compatibility, backups, and safe reversal where relevant.

## Surprises and Discoveries

Record unexpected behavior with concise evidence.

## Decision Log

- Decision:
  Rationale:
  Date/author:

## Outcomes and Retrospective

Compare delivered behavior with the original purpose. Record gaps and follow-up work.
```

The Developer may create and update a proposed plan, but must not change `proposed` to `approved` without an external approval source.

## Quality Test

Reject the plan if it:

- relies on chat history or undefined context;
- lists edits without user-visible purpose;
- lacks exact verification and expected results;
- hides assumptions;
- omits recovery for risky steps;
- cannot be resumed after the original agent disappears.
- lacks an approval source or actual verification results.
