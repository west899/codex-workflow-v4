# Risk-Triggered Review Checklist

Use only the sections relevant to the change. This is a prompt for investigation, not a box-ticking substitute for reasoning.

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

