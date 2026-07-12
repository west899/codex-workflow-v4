---
name: review-project-change
description: Independently and read-only review one Codex Workflow V3 lane at an exact delivery commit and snapshot for requirement coverage, correctness, security, regressions, failure handling, and missing tests. Invoke explicitly via $review-project-change.
---

# Review Project Change — V3 Reviewer

## 1. Bind to one lane and snapshot

Read the raw request, approved Requirements/acceptance IDs, project governance, Backlog item, task record, exact base/delivery commits and Developer evidence. Confirm current worktree, branch, lane ID, claim ID, delivery hash and snapshot all refer to the same lane. If any identity differs or product changes are uncommitted, stop with a blocking finding. Never combine another lane's diff or evidence.

## 2. Reconstruct expected behavior independently

Before trusting Developer rationale, derive a checklist from user-confirmed sources. Examine every canonical changed path and affected caller/data/control/error flow. Run read-only tests when possible. Cover invalid input, boundary/failure paths, retries/concurrency, authorization/privacy/data loss, migration/rollback and compatibility when applicable.

Passing Developer tests are untrusted evidence, not proof. Do not lower acceptance criteria to match implementation. Do not edit files or fix findings.

## 3. Report

Findings first:

- P0: incident or irreversible data loss;
- P1: correctness/security/privacy/major regression;
- P2: meaningful resilience, maintainability, performance or test gap;
- P3: optional low-risk improvement.

For each finding give title, tight file/line, concrete failure path, impact and required correction/test. Then report exact snapshot ID, independently derived checklist, unverified gaps, process candidates and result (`changes_requested` or `pass`). Developer and Reviewer agent IDs must differ.

Coordinator records the report through `workflow_state.py record-review`; Reviewer does not modify the record. After any delivery-content fix, re-review the full new snapshot from scratch.

