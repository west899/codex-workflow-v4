# Developer evidence — load before record-developer

Open the Evidence Contract in `.codex-workflow/docs/WORKFLOW.md` only when sealing a delivery, not at session start.

```text
py -3 .codex-workflow/bin/workflow_state.py record-developer <record> --delivery-commit HEAD --evidence-json <file>
py -3 .codex-workflow/bin/workflow_state.py record-developer <record> --delivery-commit HEAD --evidence-json <file> --apply
```

Evidence must follow Evidence Contract v1 in `.codex-workflow/docs/WORKFLOW.md`:

- give every command a unique `id`, normalized repository-relative `cwd`, exact command, exit code, expected-failure flag, result and non-empty `scope_ids`;
- declare only `canonical_delivery_paths`, `declared_path_call_graph`, `explicit_command_set`, `explicit_test_set`, `explicit_runtime_surfaces` or `repository_tree` scopes with exact targets; enumerate non-empty `observed_surfaces` for runtime scopes and make canonical-delivery targets exactly match changed paths;
- never use `all`, `*`, `repository_wide` or `all_dry_runs` as an aggregate target; a full canonical product-tree scope uses `repository_tree` target `.`, explicitly excludes `mutable_workflow_control`, and runs supporting commands from `cwd="."`;
- bind every claim to one declared scope and actual supporting commands; do not submit a fingerprint because `record-developer` binds the normalized closure to the sealed snapshot and generates `evidence_fingerprint`;
- keep `handoff` to exactly `claim_ids`, `remaining_risks` and `review_focus`.

Command success does not justify a broader claim than its referenced scope. Do not write review fields, verification passed, integration or Backlog state.
