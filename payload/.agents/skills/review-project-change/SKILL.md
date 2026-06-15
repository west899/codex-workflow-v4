---
name: review-project-change
description: Independently review another agent's code changes for requirement coverage, correctness, security, privacy, regressions, failure handling, architecture, and missing tests. Use after implementation or when reviewing a branch, diff, commit, or pull request. Operate read-only and report actionable findings with file and line references. Invoke explicitly via $review-project-change.
---

# Review Project Change

Act as an independent, read-only Reviewer. Treat implementation summaries and passing tests as claims to verify, not proof.

## 1. Reconstruct Expected Behavior

Read:

- the raw user request and acceptance criteria;
- `PROJECT.md`, `PLAN.md`, and relevant `DECISIONS.md` entries;
- the linked `docs/MVP_BACKLOG.md` item when the task comes from the MVP Backlog;
- the base code and current diff;
- the task record and Developer worktree hash;
- verification evidence supplied by the Developer.

Write a requirement checklist derived from those sources before reading the Developer's rationale. Include it in the review output so the Coordinator can persist the review basis. Flag any acceptance criterion that is ambiguous or absent.

## 2. Inspect the Change in Context

1. Examine every changed file and the callers or consumers it affects.
2. Trace data, control flow, state transitions, and error paths.
3. Compare with existing patterns and architecture boundaries.
4. Run relevant tests and checks when possible.
5. Test or reason through boundary values, invalid input, retries, concurrency, authorization, and partial failure as applicable.
6. Use [review-checklist.md](references/review-checklist.md) for risk-triggered checks.

Do not limit review to style. Look for behavior that compiles and passes narrow tests but violates the real requirement.

## 3. Guard Independence

- Do not edit files or implement fixes.
- Do not accept the Developer's assumptions without repository or requirement evidence.
- Do not lower acceptance criteria because the implementation chose a narrower interpretation.
- Do not report hypothetical issues without a concrete failure path.
- Do not hide uncertainty; state what could not be verified.
- Do not issue a passing review unless `python3 scripts/workflow_check.py snapshot <task-record>` matches the Developer hash.

## 4. Report Findings

Order findings by severity:

- **P0:** immediate security incident, irreversible data loss, or unusable release.
- **P1:** correctness, authorization, privacy, major regression, or reliable production failure.
- **P2:** meaningful maintainability, resilience, performance, or test gap.
- **P3:** optional improvement with low immediate risk.

For every finding provide:

1. concise title;
2. file and tight line range;
3. failure scenario;
4. user or system impact;
5. required correction or missing test.

Findings come first. Then provide the reviewed worktree hash, requirement checklist, open questions, residual test gaps, process observations / Rule Proposal candidates, and final result (`changes_requested` or `pass`). If no findings exist, say so explicitly and still state what was not verified.

For each process candidate, identify the concrete evidence, whether the issue appears isolated or repeatable, the suggested governance target, and the smallest enforceable change. Do not turn a code defect into a new rule unless the workflow itself allowed, encouraged, or repeatedly failed to catch it. Remain read-only and do not modify governance files.

## 5. Re-review

After fixes, review the new diff from scratch against the original acceptance criteria. Do not merely check whether the cited lines changed.
