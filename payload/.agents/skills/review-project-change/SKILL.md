---
name: review-project-change
description: Independently and read-only review one Codex Workflow V4 lane at an exact delivery commit and snapshot for requirement coverage, correctness, security, regressions, failure handling, and missing tests. Invoke explicitly via $review-project-change.
---

# Review Project Change — V4 Reviewer

## 1. Bind to one lane and snapshot

Read the raw request, approved Requirements/acceptance IDs, project governance, Backlog item, task record, exact base/delivery commits and Developer evidence. Confirm current worktree, branch, lane ID, claim ID, delivery hash and snapshot all refer to the same lane. If any identity differs or product changes are uncommitted, stop with a blocking finding. Never combine another lane's diff or evidence.

## 2. Reconstruct expected behavior independently

Before trusting Developer rationale, derive a checklist from user-confirmed sources. Examine every canonical changed path and affected caller/data/control/error flow. Run read-only tests when possible. Cover invalid input, boundary/failure paths, retries/concurrency, authorization/privacy/data loss, migration/rollback and compatibility when applicable.

Passing Developer tests are untrusted evidence, not proof. Do not lower acceptance criteria to match implementation. Do not edit files or fix findings.

For a V4 record also reconstruct: current `focus_slice_id`, whether supporting work claims core completion, whether a required product checkpoint is accepted for this snapshot, whether any continuation is current, whether architecture guardrails match the live baseline, and whether `declared_impact` matches independently classified paths plus fitness evidence. Reject a Review `pass` that treats an unaccepted required checkpoint as product direction confirmed.

Reconstruct every Evidence Contract v1 `evidence_fingerprint` from the sealed snapshot, full normalized claim, full referenced finite scope and full supporting commands sorted by command ID. Reject aggregate scope targets `all`, `*`, `repository_wide` and `all_dry_runs`; a full canonical product-tree scope requires `repository_tree` target `.`, exclusion `mutable_workflow_control`, and supporting commands with `cwd="."`, while exact product-tree identity comes from the snapshot. Confirm that Developer handoff contains only `claim_ids`, `remaining_risks` and `review_focus` and that its claim IDs exactly match the submitted claims.

## 3. Report

Findings first:

- P0: incident or irreversible data loss;
- P1: correctness/security/privacy/major regression;
- P2: meaningful resilience, maintainability, performance or test gap;
- P3: optional low-risk improvement.

For each Developer claim, report the exact `claim_id + evidence_fingerprint` and one assessment:

- `confirmed`: the statement, scope and supporting commands support the claim as written;
- `narrowed`: evidence supports only a smaller scope; state the exact supported targets or surfaces in `notes`;
- `rejected`: evidence or implementation conflicts with the claim;
- `unverified`: the authorized read-only review cannot confirm it.

Use `pass` only when every claim assessment is `confirmed`. Any `narrowed`, `rejected` or `unverified` assessment requires `changes_requested`, even when no independent P0-P3 finding exists. For each independent finding give title, tight file/line, concrete failure path, impact and required correction/test. Then report exact snapshot ID, independently derived checklist, residual gaps and result. Developer and Reviewer agent IDs must differ.

Coordinator records the report through `workflow_state.py record-review`; Reviewer does not modify the record. After any delivery-content fix, re-review the full new snapshot from scratch.
