# Risk-Triggered Review Checklist

Use only the sections relevant to the change. This is a prompt for investigation, not a box-ticking substitute for reasoning.

## Evidence Contract v1

- Does every command have a unique ID and preserve the exact command, normalized repository-relative `cwd`, exit code, expected-failure flag, result and non-empty scope IDs? Does every non-zero exit declare `expected_failure: true`?
- Is every scope one of `canonical_delivery_paths`, `declared_path_call_graph`, `explicit_command_set`, `explicit_test_set`, `explicit_runtime_surfaces` or `repository_tree`?
- Are scope targets exact, non-empty and free of the aggregate sentinels `all`, `*`, `repository_wide` and `all_dry_runs`?
- Does every `repository_tree` scope use target `.`, exclude `mutable_workflow_control`, and run supporting commands with `cwd="."`, with exact canonical product-tree identity supplied by the sealed snapshot? Do runtime scopes enumerate observed surfaces, and do canonical-delivery targets exactly match changed paths?
- Does every claim reference exactly one declared scope and non-empty actual supporting commands whose scope IDs contain that scope? Does every scope and command support at least one claim?
- Does independent canonical reconstruction over snapshot, full normalized claim, full scope and command-ID-sorted full commands reproduce every workflow-generated `evidence_fingerprint`?
- Does Developer handoff contain only `claim_ids`, `remaining_risks` and `review_focus`, with no wider free-text assertion?
- Is there exactly one `confirmed/narrowed/rejected/unverified` assessment for every Developer claim fingerprint?
- Is Review `pass` used only when every assessment is `confirmed`? Any other assessment requires `changes_requested`.

## Remote integration

- Does remote closeout still require strict-ff, `result_commit == pr_head_commit`, and CI `status == success`?
- If a provider receipt is present, is it additive only and unable to skip Independent Reviewer or local ff/CI proofs?
- Are `skipped`/`neutral`, admin bypass, and branch-protection-as-Reviewer paths rejected?

## V4 product contract

- Is `focus_slice_id` the human-confirmed core slice, and does supporting work declare `supports_task_id` without claiming the core is done?
- For `checkpoint.mode=required`, is there a current-snapshot observation receipt and an accepted product direction bound to that receipt?
- If Reviewer produced a new snapshot, did product-semantic changes request a new observation rather than reuse a stale receipt?
- If a continuation is claimed, does it satisfy the strict technical-equivalence gate?
- Do inline guardrails match the live architecture baseline, and does `changes_guardrail` have an accepted architecture decision?

## Requirement Coverage

- Does every acceptance criterion have an implementation and test or observable proof?
- Did the change silently narrow scope, alter a non-goal, or invent behavior?
- Are documentation and user-visible behavior consistent?

## Correctness and State

- Boundary values, empty state, duplicates, ordering, time zones, locale, and retries
- Invalid or stale state, partial completion, idempotence, and rollback
- Concurrent updates, race conditions, transaction boundaries, and lost updates
- Error propagation, cleanup, and resource leaks

## Security and Privacy

- Authentication versus authorization on every protected action
- Input validation, output encoding, injection, unsafe deserialization, and file paths
- Secret exposure, logging of personal data, access tokens, and production configuration
- Data minimization, retention, deletion, visibility, and cross-user isolation
- Domain-specific abuse, fraud, unsafe content, reporting, blocking, and moderation when applicable

## Interfaces and Compatibility

- API/schema compatibility and migration order
- Callers, consumers, caches, events, and background jobs
- Mobile/browser/accessibility behavior where relevant
- Dependency changes, lockfiles, licensing, and supply-chain risk

## Reliability and Operations

- Timeouts, retry policy, rate limits, backpressure, and degraded dependencies
- Structured logs, metrics, alerts, health checks, and actionable errors
- Deployment ordering, feature flags, rollback, backups, and recovery

## Tests

- Test fails before the fix and passes after it
- Assertions verify behavior rather than implementation detail
- Negative, permission, error, and integration paths are covered
- Mocks do not conceal the real boundary or incompatible behavior
