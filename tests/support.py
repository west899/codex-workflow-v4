from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
V4_CORE_TEMPLATE = PACKAGE_ROOT / (
    "payload/.agents/skills/orchestrate-project-task/references/"
    "task-record-v4-core-template.json"
)


def run(
    command: list[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        command,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def install_project(target: Path, *, parallel_mode: str = "single", extra: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    return run(
        [
            sys.executable,
            "-B",
            str(PACKAGE_ROOT / "install.py"),
            str(target),
            "--project-name",
            "workflow-test",
            "--parallel-mode",
            parallel_mode,
            *(extra or []),
        ],
        cwd=PACKAGE_ROOT,
    )


def git(target: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = run(["git", *arguments], cwd=target)
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result


def configure_git(target: Path) -> None:
    git(target, "config", "user.email", "workflow-test@example.invalid")
    git(target, "config", "user.name", "Workflow Test")


def commit_all(target: Path, message: str) -> str:
    git(target, "add", ".")
    git(target, "commit", "-m", message)
    return git(target, "rev-parse", "HEAD").stdout.strip()


def create_baseline(target: Path) -> str:
    configure_git(target)
    return commit_all(target, "baseline")


def workflow_command(target: Path, script: str, *arguments: str) -> list[str]:
    return [
        sys.executable,
        "-B",
        str(target / ".codex-workflow" / "bin" / script),
        *arguments,
    ]


def canonical_json(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def normalized_markdown(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).rstrip("\n") + "\n"


def requirements_fingerprint(metadata: dict, markdown: str) -> str:
    payload = {
        key: metadata[key]
        for key in ("schema_version", "brief_id", "revision", "target_release", "requirements")
    }
    return hashlib.sha256(canonical_json(payload) + b"\0" + normalized_markdown(markdown).encode("utf-8")).hexdigest()


def closeout_fingerprint(record: dict, backlog_text: str) -> str:
    integration = record.get("integration") or {}
    state = {
        "task_id": record.get("task_id"),
        "integration": {
            key: integration.get(key)
            for key in (
                "status",
                "mode",
                "policy_id",
                "source_ref",
                "target_ref",
                "target_parent",
                "pr_head_commit",
                "result_commit",
                "merge_strategy",
                "queue_id",
                "closeout_commit",
                "pr_url",
                "ci_checks",
                "evidence",
            )
        },
        "backlog": normalized_markdown(backlog_text),
    }
    return hashlib.sha256(canonical_json(state)).hexdigest()


def approved_requirements(target: Path, *, brief_id: str = "REQ-001") -> tuple[Path, str]:
    metadata = {
        "schema_version": 1,
        "brief_id": brief_id,
        "revision": 1,
        "target_release": "MVP-1",
        "status": "approved",
        "requirements": {
            "scope": {"in": ["Deliver the observable workflow"], "out": ["Production release"]},
            "users": [{"id": "REQ-U-001", "text": "Project maintainer", "status": "confirmed", "source": "user:test"}],
            "outcomes": [{"id": "REQ-O-001", "text": "A verified result", "status": "confirmed", "source": "user:test"}],
            "flows": [{"id": "REQ-FLOW-001", "steps": ["claim", "implement", "verify"], "status": "confirmed", "source": "user:test"}],
            "capabilities": [{"id": "REQ-F-001", "priority": "must", "preconditions": [], "inputs": [], "action": "Run the workflow", "observable_result": "The gate passes", "boundaries": ["No release"], "status": "confirmed", "source": "user:test"}],
            "scenarios": [
                {"id": "REQ-S-001", "type": "happy", "applicability": "required", "na_reason": None, "na_source": None, "expected": "The task completes", "status": "confirmed", "source": "user:test"},
                {"id": "REQ-S-002", "type": "failure", "applicability": "required", "na_reason": None, "na_source": None, "expected": "Invalid state is rejected", "status": "confirmed", "source": "user:test"},
                {"id": "REQ-S-003", "type": "non_goal", "applicability": "required", "na_reason": None, "na_source": None, "expected": "No automatic deployment", "status": "confirmed", "source": "user:test"},
            ],
            "non_goals": [{"id": "REQ-NG-001", "text": "Production release", "status": "confirmed", "source": "user:test"}],
            "constraints": [],
            "open_questions": [{"id": "REQ-Q-001", "blocking": False, "status": "open", "owner": "maintainer", "resolution_target": "before release", "source": "user:test", "resolution": None, "resolved_by": None, "resolved_source": None}],
            "calibration_rounds": [{"id": "CAL-001", "result": "confirmed", "changed_requirement_ids": [], "source": "user:test"}],
        },
        "approval": {"approved_by": "test-user", "approved_at": "2026-07-11T00:00:00Z", "source": "user:test", "approved_fingerprint": None},
    }
    markdown = "\n# Approved Requirements\n\nREQ-F-001 and REQ-S-001 are confirmed by the test user.\n"
    fingerprint = requirements_fingerprint(metadata, markdown)
    metadata["approval"]["approved_fingerprint"] = fingerprint
    brief = target / ".codex-workflow" / "governance" / "requirements" / f"{brief_id}.md"
    brief.write_text(
        "<!-- CODEX_REQUIREMENTS_JSON_START -->\n"
        + json.dumps(metadata, ensure_ascii=False, indent=2)
        + "\n<!-- CODEX_REQUIREMENTS_JSON_END -->"
        + markdown,
        encoding="utf-8",
    )
    baseline = {"brief_id": brief_id, "revision": 1, "approval_fingerprint": fingerprint}
    for relative in (
        Path(".codex-workflow/governance/PROJECT.md"),
        Path(".codex-workflow/state/MVP_BACKLOG.md"),
    ):
        path = target / relative
        text = path.read_text(encoding="utf-8")
        start = "<!-- CODEX_REQUIREMENTS_BASELINE_START -->"
        end = "<!-- CODEX_REQUIREMENTS_BASELINE_END -->"
        payload = dict(baseline)
        if relative.name == "MVP_BACKLOG.md":
            payload = {
                "workflow_schema_version": 3,
                "requirements_gate_mode": "required",
                **baseline,
                "target_release": "MVP-1",
                "status": "approved",
            }
        text = text.split(start, 1)[0] + start + "\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n" + end + text.split(end, 1)[1]
        path.write_text(text, encoding="utf-8")
    return brief, fingerprint


def developer_evidence_v1(agent_id: str) -> dict:
    return {
        "evidence_contract_version": 1,
        "agent_id": agent_id,
        "scopes": [
            {
                "id": "SCOPE-001",
                "kind": "explicit_test_set",
                "targets": ["python -m unittest"],
                "observed_surfaces": [],
                "excluded_targets": [],
            }
        ],
        "commands": [
            {
                "id": "CMD-001",
                "command": "python -m unittest",
                "cwd": ".",
                "exit_code": 0,
                "expected_failure": False,
                "result": "passed",
                "scope_ids": ["SCOPE-001"],
            }
        ],
        "claims": [
            {
                "id": "CLAIM-001",
                "kind": "test_result",
                "predicate": "The explicit test set passes.",
                "scope_id": "SCOPE-001",
                "supporting_command_ids": ["CMD-001"],
            }
        ],
        "handoff": {
            "claim_ids": ["CLAIM-001"],
            "remaining_risks": [],
            "review_focus": ["Verify CLAIM-001 against its explicit test-set scope."],
        },
    }


def review_evidence_v1(agent_id: str, record: dict) -> dict:
    claims = record["developer"]["claims"]
    return {
        "evidence_contract_version": 1,
        "agent_id": agent_id,
        "snapshot_id": record["verification"]["snapshot_id"],
        "status": "pass",
        "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
        "requirement_checklist": ["AC-001 is observable"],
        "accepted_findings": [],
        "claim_assessments": [
            {
                "claim_id": claim["id"],
                "evidence_fingerprint": claim["evidence_fingerprint"],
                "assessment": "confirmed",
                "notes": "The exact claim scope and supporting command were reviewed.",
            }
            for claim in claims
        ],
        "summary": "Independent review passed.",
    }


def basic_v3_record(
    base_commit: str,
    *,
    task_id: str = "MVP-001",
    source_type: str = "user_directive",
    requirements_baseline: dict | None = None,
    allowed_paths: list[str] | None = None,
    resources: list[str] | None = None,
) -> dict:
    allowed_paths = allowed_paths or ["src/**", "tests/**"]
    resources = resources or ["path:src", "path:tests"]
    source = {
        "type": source_type,
        "reference": task_id if source_type == "mvp_backlog" else "test-directive",
        "priority_reason": "Regression test",
    }
    if requirements_baseline:
        source["requirements_baseline"] = requirements_baseline
    return {
        "version": 3,
        "generation": 0,
        "phase": "coordinator",
        "task_id": task_id,
        "status": "authorized",
        "source": source,
        "request": "Verify the V3 workflow behavior",
        "scope": {"in": ["V3 behavior"], "out": ["Production release"], "allowed_paths": allowed_paths, "resource_keys": resources},
        "planning": {"level": "small", "exec_plan": None, "steps": ["Run the focused regression test"]},
        "implementation_authorization": {"authorized": True, "authorized_by": "test-user", "authorized_at": "2026-07-11T00:00:00Z", "source": "user:test"},
        "risk": {"product_scope": False, "sensitive_data": False, "destructive_change": False, "irreversible_architecture": False, "production_release": False},
        "base_commit": base_commit,
        "acceptance": [{"id": "AC-001", "criterion": "The workflow behavior is observable", "status": "pending", "evidence": []}],
        "lane": {"lane_id": None, "mode": "single", "branch": None, "base_ref": None, "base_commit": None, "claim_id": None, "owner_generation": 0, "assignment": None, "allowed_paths": allowed_paths, "resource_keys": resources, "dependency_snapshot": {"backlog_commit": base_commit, "dependencies": []}},
        "verification": {"status": "pending", "delivery_commit": None, "delivery_hash": None, "patch_hash": None, "snapshot_id": None, "changed_paths": []},
        "developer": {"agent_id": None, "snapshot_id": None, "commands": [], "handoff": None},
        "review": {"agent_id": None, "snapshot_id": None, "status": "pending", "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0}, "requirement_checklist": [], "accepted_findings": [], "summary": None},
        "human_approvals": [],
        "integration": {"status": "not_ready", "mode": None, "policy_id": None, "source_ref": None, "target_ref": None, "target_parent": None, "pr_head_commit": None, "result_commit": None, "merge_strategy": None, "queue_id": None, "queued_at": None, "queue_priority": None, "closeout_commit": None, "closeout_state_fingerprint": None, "pr_url": None, "ci_checks": [], "evidence": []},
        "process_retrospective": {"completed": False, "completed_by": None, "completed_at": None, "questions": {"repeated_problem_found": False, "guidance_gap_found": False, "deterministic_check_candidate_found": False}, "summary": None},
        "rule_proposals": [],
        "remaining_risks": [],
    }


def configure_v4_architecture_baseline(target: Path) -> str:
    bin_path = PACKAGE_ROOT / "payload/.codex-workflow/bin"
    sys.path.insert(0, str(bin_path))
    try:
        from workflow_common import architecture_baseline_fingerprint
    finally:
        sys.path.pop(0)
    baseline = {
        "schema_version": 1,
        "baseline_id": "ARCH-BASELINE-001",
        "revision": 1,
        "status": "approved",
        "guardrails": [
            {
                "id": "ARCH-G-001",
                "statement": "The slice must preserve the approved module dependency direction.",
                "source": "user:test",
                "verification_refs": ["test:architecture-boundary"],
            }
        ],
        "approval": {
            "approved_by": "test-user",
            "approved_at": "2026-07-20T00:00:00Z",
            "source": "user:test",
            "approved_fingerprint": None,
        },
    }
    fingerprint = architecture_baseline_fingerprint(baseline)
    baseline["approval"]["approved_fingerprint"] = fingerprint
    path = target / ".codex-workflow/governance/DECISIONS.md"
    text = path.read_text(encoding="utf-8")
    start = "<!-- CODEX_ARCHITECTURE_BASELINE_START -->"
    end = "<!-- CODEX_ARCHITECTURE_BASELINE_END -->"
    text = (
        text.split(start, 1)[0]
        + start
        + "\n"
        + json.dumps(baseline, ensure_ascii=False, indent=2)
        + "\n"
        + end
        + text.split(end, 1)[1]
    )
    path.write_text(text, encoding="utf-8")
    return fingerprint


def sync_backlog_focus_from_record(target: Path, record: dict) -> None:
    """Keep installed Backlog focus metadata aligned with a V4 test record."""

    path = target / ".codex-workflow/state/MVP_BACKLOG.md"
    text = path.read_text(encoding="utf-8")
    bin_path = PACKAGE_ROOT / "payload/.codex-workflow/bin"
    sys.path.insert(0, str(bin_path))
    try:
        from workflow_common import BACKLOG_FOCUS_MARKER, read_embedded_json, replace_embedded_json
    finally:
        sys.path.pop(0)
    payload = read_embedded_json(text, BACKLOG_FOCUS_MARKER)
    contract = record.get("delivery_contract") if isinstance(record.get("delivery_contract"), dict) else {}
    item = {
        "id": record["task_id"],
        "kind": contract.get("kind") or "core_slice",
        "focus_slice_id": contract.get("focus_slice_id") or record["task_id"],
        "supports_task_id": contract.get("supports_task_id"),
        "direction_confirmed": False,
    }
    items = [
        existing
        for existing in payload.get("items", [])
        if isinstance(existing, dict) and existing.get("id") != item["id"]
    ]
    if item["kind"] == "core_slice":
        for existing in items:
            if existing.get("kind") == "core_slice":
                existing["direction_confirmed"] = True
    items.append(item)
    payload["items"] = items
    path.write_text(
        replace_embedded_json(text, BACKLOG_FOCUS_MARKER, payload),
        encoding="utf-8",
    )


def basic_v4_record(
    base_commit: str,
    *,
    task_id: str = "MVP-001",
    requirements_baseline: dict | None = None,
    architecture_fingerprint: str = "2" * 64,
    checkpoint_mode: str = "required",
    execution_mode: str = "formal",
    allowed_paths: list[str] | None = None,
    resources: list[str] | None = None,
) -> dict:
    record = copy.deepcopy(json.loads(V4_CORE_TEMPLATE.read_text(encoding="utf-8")))
    allowed_paths = allowed_paths or ["src/**", "tests/**", "docs/**"]
    resources = resources or ["path:src", "path:tests", "path:docs"]
    baseline = requirements_baseline or {
        "brief_id": "REQ-001",
        "revision": 1,
        "approval_fingerprint": "1" * 64,
    }
    record["task_id"] = task_id
    record["status"] = "in_progress"
    record["phase"] = "developer"
    record["source"] = {
        "type": "user_directive",
        "reference": "user:test-v4",
        "priority_reason": "V4 M2 regression",
        "requirements_baseline": copy.deepcopy(baseline),
    }
    record["implementation_authorization"] = {
        "authorized": True,
        "authorized_by": "test-user",
        "authorized_at": "2026-07-20T00:00:00Z",
        "source": "user:test",
    }
    record["base_commit"] = base_commit
    record["scope"]["allowed_paths"] = list(allowed_paths)
    record["scope"]["resource_keys"] = list(resources)
    contract = record["delivery_contract"]
    contract["focus_slice_id"] = task_id
    contract["dependency_refs"] = []
    contract["checkpoint"]["mode"] = checkpoint_mode
    contract["checkpoint"]["reason"] = f"M2 checkpoint mode {checkpoint_mode}."
    contract["execution_mode"] = execution_mode
    contract["architecture"]["baseline"]["fingerprint"] = architecture_fingerprint
    record["lane"] = {
        "lane_id": f"lane-{task_id}-single",
        "mode": "single",
        "branch": "main",
        "base_ref": "main",
        "base_commit": base_commit,
        "claim_id": "00000000-0000-4000-8000-000000000001",
        "owner_generation": 1,
        "assignment": {
            "assigned_owner_id": "00000000-0000-4000-8000-000000000002",
            "assignment_generation": 1,
            "assigned_at": "2026-07-20T00:00:00Z",
            "assigned_by": "test",
        },
        "allowed_paths": list(allowed_paths),
        "resource_keys": list(resources),
        "dependency_snapshot": {"backlog_commit": base_commit, "dependencies": []},
    }
    bin_path = PACKAGE_ROOT / "payload/.codex-workflow/bin"
    sys.path.insert(0, str(bin_path))
    try:
        from workflow_common import contract_fingerprint
    finally:
        sys.path.pop(0)
    record["contract_fingerprint"] = contract_fingerprint(record)
    return record


def v4_observation_receipt(record: dict) -> dict:
    verification = record["verification"]
    contract = record["delivery_contract"]
    entrypoint_ref = contract["observation"]["entrypoint_ref"]
    resolved_entrypoint = (
        "command:" + entrypoint_ref.removeprefix("project-script:")
        if entrypoint_ref.startswith("project-script:")
        else entrypoint_ref
    )
    healthcheck_ref = contract["observation"]["healthcheck_ref"]
    return {
        "receipt_version": 1,
        "snapshot_id": verification["snapshot_id"],
        "delivery_commit": verification["delivery_commit"],
        "contract_fingerprint": record["contract_fingerprint"],
        "entrypoint": {
            "reference": entrypoint_ref,
            "resolved_reference": resolved_entrypoint,
            "status": "ready",
            "checked_at": "2026-07-20T00:00:00Z",
            "expires_at": None,
        },
        "healthcheck": (
            {
                "reference": healthcheck_ref,
                "status": "passed",
                "checked_at": "2026-07-20T00:00:00Z",
            }
            if healthcheck_ref is not None
            else None
        ),
        "artifact": {
            "reference": f"git:{verification['delivery_commit']}",
            "digest": verification["delivery_hash"],
        },
        "environment": {
            "kind": "test",
            "reference": "environment:v4-m2-test",
            "configuration_fingerprint": "d" * 64,
        },
        "fixture": {
            "reference": contract["observation"]["fixture_ref"],
            "digest": "e" * 64,
            "data_class": "test",
        },
        "recipe_replayed": True,
        "observed_at": "2026-07-20T00:00:00Z",
        "observer_source": "user:test-owner",
        "requirement_ids": list(contract["requirement_ids"]),
        "acceptance_ids": list(contract["acceptance_ids"]),
        "evidence_refs": ["evidence:v4-m2-transcript"],
        "normalized_result": {
            "surface": "cli",
            "method_evidence": {
                "kind": "cli",
                "command_ref": resolved_entrypoint,
                "exit_code": 0,
                "stdout_ref": "evidence:v4-m2-stdout",
                "stderr_ref": None,
            },
            "assertions": [
                {
                    "id": "OBS-001",
                    "status": "passed",
                    "actual": "The core action completed and printed the declared result.",
                }
            ],
            "stable_output": [
                {"name": "exit_code", "value": "0"},
                {"name": "result", "value": "CORE_READY"},
            ],
            "contracts": {
                "public_api": [{"ref": "cli:observe-core-slice", "digest": "f" * 64}],
                "schemas": [],
                "dependencies": [],
            },
        },
        "real_components": ["The core action and output are executable."],
        "temporary_components": ["The regression uses test fixture data."],
        "material_changes": ["The core result is directly observable."],
        "reversible_assumptions": ["The output follows the existing CLI convention."],
        "known_limitations": ["Production release is outside this task."],
        "redaction": {"status": "not_required", "notes": []},
    }


def v4_checkpoint_request(record: dict, *, decision_id: str = "HD-001") -> dict:
    return {
        "id": decision_id,
        "kind": "product_checkpoint",
        "affected_scope": ["current_slice", "user_flow"],
        "latest_decision_point": (
            "before_review"
            if record["delivery_contract"]["checkpoint"]["mode"] == "required"
            else "before_dependency"
        ),
        "current_delivery_independent": False,
        "question": "Does this observed result match the current product direction?",
        "observation_receipt": v4_observation_receipt(record),
    }


def write_record(target: Path, record: dict) -> Path:
    path = target / ".codex-workflow" / "state" / "runs" / f"{record['task_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def record_relative(record_path: Path, target: Path) -> str:
    return record_path.relative_to(target).as_posix()
