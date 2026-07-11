#!/usr/bin/env python3
"""CAS-protected state transitions and two-phase closeout for Workflow V3."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from workflow_common import (
    WorkflowDataError,
    canonical_delivery,
    closeout_state_fingerprint,
    fault_injection,
    git,
    is_ancestor,
    load_record,
    local_bootstrap_policy_gate,
    read_embedded_json,
    rev_parse,
    snapshot_id,
    unlock_ready_dependencies,
    update_backlog_status,
    utc_now,
    validate_workflow_schema,
)
from workflow_lock import (
    AdvisoryLock,
    LockUnavailable,
    PersistentRoleLock,
    PersistentRoleLockError,
    resource_key_digest,
    role_lock_guard_path,
    validate_role_lock_access,
)
from workflow_paths import WorkflowPathError, WorkflowPaths, atomic_write_json, atomic_write_text


BASELINE_MARKER = "CODEX_REQUIREMENTS_BASELINE"
Mutation = Callable[[dict[str, Any]], None]


class StateError(ValueError):
    pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Mutate Codex Workflow V3 state.")
    sub = result.add_subparsers(dest="command", required=True)

    def record_command(name: str) -> argparse.ArgumentParser:
        item = sub.add_parser(name)
        item.add_argument("record")
        item.add_argument("--expected-generation", type=int)
        item.add_argument("--apply", action="store_true")
        return item

    developer = record_command("record-developer")
    developer.add_argument("--evidence-json", required=True)
    developer.add_argument("--delivery-commit", default="HEAD")

    review = record_command("record-review")
    review.add_argument("--review-json", required=True)

    complete = record_command("complete-task")
    complete.add_argument("--acceptance-json", required=True)

    record_command("mark-verified")

    approval = record_command("record-approval")
    approval.add_argument("--approval-json", required=True)

    integration = record_command("prepare-integration")
    integration.add_argument("--mode", choices=("local_bootstrap", "remote_pr_ci"), required=True)

    def add_integrator_lease_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--integrator-token")
        command.add_argument("--integrator-generation", type=int)

    local = record_command("prepare-local-closeout")
    local.add_argument("--target-ref", required=True)
    local.add_argument("--result-commit", required=True)
    add_integrator_lease_arguments(local)

    remote = record_command("prepare-remote-closeout")
    remote.add_argument("--evidence-json", required=True)
    add_integrator_lease_arguments(remote)

    confirm = record_command("confirm-closeout")
    confirm.add_argument("--target-ref", required=True)
    confirm.add_argument("--closeout-commit", required=True)
    add_integrator_lease_arguments(confirm)

    reconcile = record_command("reconcile")
    reconcile.add_argument("--target-ref", required=True)
    add_integrator_lease_arguments(reconcile)

    invalidate = record_command("invalidate-integration")
    invalidate.add_argument("--reason", required=True)
    return result


def read_json(path_value: str, *, name: str) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"Unable to read {name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StateError(f"{name} must contain a JSON object.")
    return payload


def record_lock_path(paths: WorkflowPaths, record_path: Path) -> Path:
    key = hashlib.sha256(paths.relative(record_path).encode("utf-8")).hexdigest()
    return paths.shared_runtime / "locks" / "records" / f"{key}.lock"


def _print_projection(record_path: Path, before: dict[str, Any], after: dict[str, Any]) -> None:
    print(
        json.dumps(
            {
                "apply": False,
                "record": record_path.as_posix(),
                "generation": {"before": before.get("generation"), "after": after.get("generation")},
                "status": {"before": before.get("status"), "after": after.get("status")},
                "phase": {"before": before.get("phase"), "after": after.get("phase")},
                "verification": after.get("verification", {}).get("status"),
                "integration": after.get("integration", {}).get("status"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def mutate_record(
    paths: WorkflowPaths,
    relative: str,
    expected_generation: int | None,
    apply: bool,
    mutation: Mutation,
) -> tuple[Path, dict[str, Any]]:
    paths.ensure_runtime()
    path, initial = load_record(paths, relative)
    if initial.get("version") != 3:
        raise StateError("State transitions only support V3 task records.")
    expected = initial.get("generation") if expected_generation is None else expected_generation
    if not isinstance(expected, int):
        raise StateError("Task record generation is invalid.")
    with AdvisoryLock(record_lock_path(paths, path), timeout=2):
        _, current = load_record(paths, relative)
        if current.get("generation") != expected:
            raise StateError(
                f"Generation conflict: expected {expected}, found {current.get('generation')}."
            )
        updated = copy.deepcopy(current)
        mutation(updated)
        if updated == current:
            print("STATE_NOOP")
            return path, current
        updated["generation"] = expected + 1
        validate_workflow_schema(
            paths,
            "task-record-v3.schema.json",
            updated,
            label="Updated task record",
        )
        if apply:
            atomic_write_json(path, updated)
            reread = json.loads(path.read_text(encoding="utf-8"))
            if reread != updated:
                raise StateError("Task record reread did not match the requested mutation.")
            print(f"STATE_APPLIED generation={updated['generation']}")
        else:
            _print_projection(path, current, updated)
        return path, updated


def _require_gate(paths: WorkflowPaths, relative: str) -> None:
    result = subprocess.run(
        [sys.executable, str(paths.tracked("bin") / "workflow_check.py"), "gate", relative],
        cwd=paths.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise StateError((result.stderr or result.stdout).strip())


def _policy(paths: WorkflowPaths) -> dict[str, Any]:
    policy = paths.layout.get("integration_policy")
    if not isinstance(policy, dict) or not isinstance(policy.get("policy_id"), str):
        raise StateError("layout integration_policy is invalid.")
    return policy


def _role_lock_context(
    paths: WorkflowPaths,
    role: str,
    action: str,
    *,
    apply: bool,
    token: str | None = None,
    generation: int | None = None,
) -> PersistentRoleLock | AdvisoryLock:
    if not apply:
        validate_role_lock_access(
            paths.shared_runtime, role, token=token, generation=generation
        )
        return AdvisoryLock(role_lock_guard_path(paths.shared_runtime, role), timeout=2)
    return PersistentRoleLock(
        paths.shared_runtime,
        role,
        action=action,
        token=token,
        generation=generation,
        release_on_exit=token is None,
    )


def _queue_entry_for_record(paths: WorkflowPaths, record: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Read the exact runtime queue entry bound to a tracked task record."""

    integration = record.get("integration") or {}
    lane = record.get("lane") or {}
    queue_id = integration.get("queue_id")
    if not isinstance(queue_id, str):
        raise StateError("Queued integration has no queue ID.")
    try:
        uuid.UUID(queue_id)
    except ValueError as exc:
        raise StateError("Queued integration has an invalid queue ID.") from exc
    path = paths.shared_runtime / "queue" / f"{queue_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"Queued integration has no readable queue entry: {exc}") from exc
    expected = {
        "queue_id": queue_id,
        "lane_id": lane.get("lane_id"),
        "task_id": record.get("task_id"),
        "claim_id": lane.get("claim_id"),
        "owner_generation": lane.get("owner_generation"),
        "queue_priority": integration.get("queue_priority"),
        "queued_at": integration.get("queued_at"),
    }
    if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
        raise StateError("Runtime queue entry does not match the task lane token, generation, or priority.")
    return path, payload


def _queue_sort_key(payload: dict[str, Any]) -> tuple[int, datetime, str]:
    priority = payload.get("queue_priority")
    queued_at = payload.get("queued_at")
    queue_id = payload.get("queue_id")
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise StateError("Runtime queue entry has an invalid priority.")
    if not isinstance(queued_at, str) or not isinstance(queue_id, str):
        raise StateError("Runtime queue entry has an invalid ordering key.")
    try:
        queued_time = datetime.fromisoformat(queued_at.replace("Z", "+00:00"))
        uuid.UUID(queue_id)
    except ValueError as exc:
        raise StateError("Runtime queue entry has an invalid timestamp or ID.") from exc
    return priority, queued_time, queue_id


def _require_queue_head(paths: WorkflowPaths, record: dict[str, Any]) -> None:
    _, current = _queue_entry_for_record(paths, record)
    entries: list[dict[str, Any]] = []
    for path in sorted((paths.shared_runtime / "queue").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"Runtime queue entry is unreadable: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise StateError(f"Runtime queue entry is invalid: {path}")
        _queue_sort_key(payload)
        entries.append(payload)
    if not entries:
        raise StateError("Runtime queue unexpectedly has no entries.")
    head = min(entries, key=_queue_sort_key)
    if head.get("queue_id") != current.get("queue_id"):
        raise StateError(
            "Local closeout requires the queue head; "
            f"queue {head.get('queue_id')} for lane {head.get('lane_id')} precedes this task."
        )


def record_developer(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    evidence = read_json(args.evidence_json, name="developer evidence")
    _, current = load_record(paths, args.record)
    commit = rev_parse(paths, args.delivery_commit)
    delivery = canonical_delivery(paths, current.get("base_commit"), commit)
    snap = snapshot_id(current, delivery["delivery_hash"])

    def mutation(record: dict[str, Any]) -> None:
        if record.get("phase") not in {"developer", "coordinator"}:
            raise StateError("Developer evidence can only be recorded from developer/coordinator phase.")
        for field in ("agent_id", "commands", "handoff"):
            if field not in evidence:
                raise StateError(f"Developer evidence is missing {field}.")
        record["developer"] = {
            "agent_id": evidence["agent_id"],
            "snapshot_id": snap,
            "commands": evidence["commands"],
            "handoff": evidence["handoff"],
        }
        record["verification"] = {
            "status": "pending",
            "delivery_commit": commit,
            "delivery_hash": delivery["delivery_hash"],
            "patch_hash": delivery["patch_hash"],
            "snapshot_id": snap,
            "changed_paths": delivery["changed_paths"],
        }
        record["status"] = "in_progress"
        record["phase"] = "review"

    mutate_record(paths, args.record, args.expected_generation, args.apply, mutation)


def record_review(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    review = read_json(args.review_json, name="review evidence")

    def mutation(record: dict[str, Any]) -> None:
        if record.get("phase") != "review":
            raise StateError("Review evidence requires task.phase=review.")
        expected = (record.get("verification") or {}).get("snapshot_id")
        if review.get("snapshot_id") != expected:
            raise StateError("Review evidence snapshot does not match the sealed delivery.")
        required = {"agent_id", "snapshot_id", "status", "findings", "requirement_checklist", "accepted_findings", "summary"}
        missing = sorted(required - set(review))
        if missing:
            raise StateError("Review evidence is missing: " + ", ".join(missing))
        record["review"] = {key: review[key] for key in sorted(required)}
        record["phase"] = "coordinator"

    mutate_record(paths, args.record, args.expected_generation, args.apply, mutation)


def complete_task(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    acceptance = read_json(args.acceptance_json, name="acceptance evidence")

    def mutation(record: dict[str, Any]) -> None:
        updates = acceptance.get("acceptance")
        retrospective = acceptance.get("process_retrospective")
        if not isinstance(updates, list) or not isinstance(retrospective, dict):
            raise StateError("Acceptance JSON requires acceptance array and process_retrospective object.")
        by_id = {item.get("id"): item for item in updates if isinstance(item, dict)}
        for item in record.get("acceptance", []):
            update = by_id.get(item.get("id"))
            if not update:
                raise StateError(f"Acceptance evidence is missing {item.get('id')}.")
            item["status"] = update.get("status")
            item["evidence"] = update.get("evidence")
        record["process_retrospective"] = retrospective
        if "rule_proposals" in acceptance:
            record["rule_proposals"] = acceptance["rule_proposals"]
        if "remaining_risks" in acceptance:
            record["remaining_risks"] = acceptance["remaining_risks"]
        record["status"] = "completed"
        record["phase"] = "coordinator"

    mutate_record(paths, args.record, args.expected_generation, args.apply, mutation)


def mark_verified(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    _require_gate(paths, args.record)

    def mutation(record: dict[str, Any]) -> None:
        verification = record.get("verification") or {}
        verification["status"] = "passed"
        record["verification"] = verification
        record["phase"] = "integration"
        record["integration"] = {
            "status": "not_ready", "mode": None, "policy_id": None,
            "source_ref": None, "target_ref": None, "target_parent": None,
            "pr_head_commit": None, "result_commit": None, "merge_strategy": None,
            "queue_id": None, "queued_at": None, "queue_priority": None,
            "closeout_commit": None, "closeout_state_fingerprint": None,
            "pr_url": None, "ci_checks": [], "evidence": [],
        }

    mutate_record(paths, args.record, args.expected_generation, args.apply, mutation)


def record_approval(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    approval = read_json(args.approval_json, name="human approval")
    required = {"kind", "task_id", "target_ref", "snapshot_id", "delivery_hash", "approved_by", "approved_at", "source"}
    missing = sorted(required - set(approval))
    if missing:
        raise StateError("Human approval is missing: " + ", ".join(missing))

    def mutation(record: dict[str, Any]) -> None:
        verification = record.get("verification") or {}
        expected = {
            "task_id": record.get("task_id"),
            "snapshot_id": verification.get("snapshot_id"),
            "delivery_hash": verification.get("delivery_hash"),
        }
        for field, value in expected.items():
            if approval.get(field) != value:
                raise StateError(f"Approval {field} is not bound to the current task snapshot.")
        approvals = record.setdefault("human_approvals", [])
        identity = (approval["kind"], approval["target_ref"], approval["snapshot_id"])
        if not any(
            isinstance(item, dict)
            and (item.get("kind"), item.get("target_ref"), item.get("snapshot_id")) == identity
            for item in approvals
        ):
            approvals.append(approval)

    mutate_record(paths, args.record, args.expected_generation, args.apply, mutation)


def prepare_integration(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    _require_gate(paths, args.record)
    policy = _policy(paths)

    def mutation(record: dict[str, Any]) -> None:
        verification = record.get("verification") or {}
        if verification.get("status") != "passed":
            raise StateError("mark-verified must pass before prepare-integration.")
        integration = record.get("integration") or {}
        if integration.get("status") not in {"not_ready", "pending"}:
            raise StateError("Integration is not in a preparable state.")
        if args.mode == "local_bootstrap":
            local_bootstrap_policy_gate(
                policy,
                paths.tracked("backlog").read_text(encoding="utf-8"),
                task_id=record.get("task_id"),
            )
            target_ref = policy.get("local_target_ref")
            approval = [
                item for item in record.get("human_approvals", [])
                if isinstance(item, dict)
                and item.get("kind") == "local_bootstrap"
                and item.get("task_id") == record.get("task_id")
                and item.get("target_ref") == target_ref
                and item.get("snapshot_id") == verification.get("snapshot_id")
                and item.get("delivery_hash") == verification.get("delivery_hash")
            ]
            if not approval:
                raise StateError("Local bootstrap requires exact snapshot-bound human approval.")
        else:
            target_ref = policy.get("remote_target_ref")
        integration.update(
            {
                "status": "pending",
                "mode": args.mode,
                "policy_id": policy["policy_id"],
                "source_ref": f"refs/heads/{record['lane']['branch']}",
                "target_ref": target_ref,
            }
        )
        record["integration"] = integration

    mutate_record(paths, args.record, args.expected_generation, args.apply, mutation)


def _ensure_clean_for_closeout(paths: WorkflowPaths) -> None:
    changed = subprocess.run(
        ["git", "-C", str(paths.root), "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()
    if changed:
        raise StateError("Closeout requires a clean integration worktree before state preparation.")


def _prepare_closeout_commit(
    paths: WorkflowPaths,
    relative: str,
    expected_generation: int | None,
    apply: bool,
    evidence: dict[str, Any],
    *,
    integrator_token: str | None = None,
    integrator_generation: int | None = None,
) -> None:
    paths.ensure_runtime()
    record_path, initial = load_record(paths, relative)
    expected = initial.get("generation") if expected_generation is None else expected_generation
    if not isinstance(expected, int):
        raise StateError("Task generation is invalid.")
    backlog_path = paths.tracked("backlog")
    with _role_lock_context(
        paths,
        "integrator",
        "prepare-closeout",
        apply=apply,
        token=integrator_token,
        generation=integrator_generation,
    ) as integrator_lock:
        if isinstance(integrator_lock, PersistentRoleLock):
            integrator_lock.heartbeat()
        with AdvisoryLock(record_lock_path(paths, record_path), timeout=2):
            _, current = load_record(paths, relative)
            if current.get("generation") != expected:
                raise StateError(f"Generation conflict: expected {expected}, found {current.get('generation')}.")
            _ensure_clean_for_closeout(paths)
            updated = copy.deepcopy(current)
            integration = updated.get("integration") or {}
            integration.update(evidence)
            integration["status"] = "integrated"
            integration["closeout_commit"] = None
            updated["integration"] = integration
            updated["generation"] = expected + 1
            backlog = backlog_path.read_text(encoding="utf-8")
            backlog = update_backlog_status(
                backlog,
                str(updated.get("task_id")),
                "done",
                integration=str(evidence.get("result_commit") or "integrated"),
            )
            backlog, unlocked = unlock_ready_dependencies(backlog)
            fingerprint = closeout_state_fingerprint(updated, backlog)
            updated["integration"]["closeout_state_fingerprint"] = fingerprint
            validate_workflow_schema(
                paths,
                "task-record-v3.schema.json",
                updated,
                label="Updated task record",
            )
            if not apply:
                print(
                    json.dumps(
                        {
                            "apply": False,
                            "task_id": updated.get("task_id"),
                            "generation": updated["generation"],
                            "closeout_state_fingerprint": fingerprint,
                            "unlocked": unlocked,
                            "commit_message": f"workflow: close {updated.get('task_id')}",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return
            atomic_write_json(record_path, updated)
            atomic_write_text(backlog_path, backlog)
            git(paths, "add", "--", paths.relative(record_path), paths.relative(backlog_path))
            result = git(paths, "commit", "-m", f"workflow: close {updated.get('task_id')}", check=False)
            if result.returncode != 0:
                raise StateError(
                    "Closeout state was preserved but commit failed; repair Git and rerun: "
                    + (result.stderr or result.stdout).strip()
                )
            closeout_commit = rev_parse(paths, "HEAD")
            journal = {
                "task_id": updated.get("task_id"),
                "record": paths.relative(record_path),
                "closeout_commit": closeout_commit,
                "closeout_state_fingerprint": fingerprint,
                "target_ref": evidence.get("target_ref"),
                "prepared_at": utc_now(),
                "confirmed": False,
            }
            atomic_write_json(
                paths.shared_runtime / "audit" / f"closeout-{updated.get('task_id')}.json",
                journal,
            )
            if isinstance(integrator_lock, PersistentRoleLock):
                integrator_lock.heartbeat()
            print(f"CLOSEOUT_PREPARED commit={closeout_commit} fingerprint={fingerprint}")


def prepare_local_closeout(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    # Keep queue admission and closeout preparation in one runtime critical section.
    with _role_lock_context(paths, "coordinator", "prepare-local-closeout", apply=args.apply):
        _, record = load_record(paths, args.record)
        expected_generation = record.get("generation") if args.expected_generation is None else args.expected_generation
        if not isinstance(expected_generation, int):
            raise StateError("Task generation is invalid.")
        integration = record.get("integration") or {}
        verification = record.get("verification") or {}
        if integration.get("mode") != "local_bootstrap" or integration.get("status") not in {"pending", "queued"}:
            raise StateError("Local closeout requires pending or queued local_bootstrap integration.")
        local_bootstrap_policy_gate(
            _policy(paths),
            paths.tracked("backlog").read_text(encoding="utf-8"),
            task_id=record.get("task_id"),
        )
        lane = record.get("lane") or {}
        if integration.get("status") == "queued":
            _require_queue_head(paths, record)
        elif lane.get("mode") == "local_worktree":
            raise StateError("Local worktree lanes must enter the integration queue before closeout.")
        if integration.get("target_ref") != args.target_ref:
            raise StateError("Target ref differs from the prepared integration policy.")
        target_oid = rev_parse(paths, args.target_ref)
        result_oid = rev_parse(paths, args.result_commit)
        if target_oid != result_oid:
            raise StateError("--result-commit must equal the current exact target ref.")
        delivery_commit = verification.get("delivery_commit")
        if not isinstance(delivery_commit, str) or not is_ancestor(paths, delivery_commit, result_oid):
            raise StateError("Target ref does not contain the exact verified delivery commit.")
        _prepare_closeout_commit(
            paths,
            args.record,
            expected_generation,
            args.apply,
            {
                "target_ref": args.target_ref,
                "target_parent": result_oid,
                "result_commit": result_oid,
                "pr_head_commit": delivery_commit,
                "merge_strategy": "ff",
                "ci_checks": [],
                "evidence": [{"kind": "local_ancestry", "verified_at": utc_now()}],
            },
            integrator_token=args.integrator_token,
            integrator_generation=args.integrator_generation,
        )


def prepare_remote_closeout(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    evidence = read_json(args.evidence_json, name="remote integration evidence")
    required = {"target_ref", "target_parent", "pr_head_commit", "result_commit", "merge_strategy", "pr_url", "ci_checks"}
    missing = sorted(required - set(evidence))
    if missing:
        raise StateError("Remote evidence is missing: " + ", ".join(missing))
    if evidence.get("merge_strategy") != "ff":
        raise StateError("V3.0 remote closeout only supports strict ff product integration.")
    checks = evidence.get("ci_checks")
    if not isinstance(checks, list) or not checks or any(
        not isinstance(item, dict) or item.get("status") != "success" for item in checks
    ):
        raise StateError("Every required remote CI check must be recorded as success.")
    _, record = load_record(paths, args.record)
    integration = record.get("integration") or {}
    verification = record.get("verification") or {}
    if integration.get("mode") != "remote_pr_ci" or integration.get("status") not in {"pending", "merged_pending_closeout"}:
        raise StateError("Remote closeout requires pending remote_pr_ci integration.")
    if integration.get("target_ref") != evidence.get("target_ref"):
        raise StateError("Remote evidence target ref differs from policy.")
    result_commit = rev_parse(paths, str(evidence["result_commit"]))
    pr_head = rev_parse(paths, str(evidence["pr_head_commit"]))
    target_parent = rev_parse(paths, str(evidence["target_parent"]))
    if pr_head != verification.get("delivery_commit"):
        raise StateError("Remote PR head is not the exact verified delivery commit.")
    if result_commit != pr_head:
        raise StateError("Strict ff integration requires result_commit == pr_head_commit.")
    if not is_ancestor(paths, target_parent, result_commit):
        raise StateError("Remote result is not a fast-forward descendant of target_parent.")
    if not is_ancestor(paths, result_commit, rev_parse(paths, str(evidence["target_ref"]))):
        raise StateError("Configured target ref does not contain the remote result commit.")
    evidence = dict(evidence)
    evidence["evidence"] = evidence.get("evidence", []) + [
        {"kind": "remote_ff", "verified_at": utc_now()}
    ]
    _prepare_closeout_commit(
        paths,
        args.record,
        args.expected_generation,
        args.apply,
        evidence,
        integrator_token=args.integrator_token,
        integrator_generation=args.integrator_generation,
    )


def _show_json_at(paths: WorkflowPaths, reference: str, relative: str) -> dict[str, Any]:
    result = git(paths, "show", f"{reference}:{relative}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise StateError(f"Target task record is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise StateError("Target task record must be an object.")
    validate_workflow_schema(
        paths,
        "task-record-v3.schema.json",
        payload,
        label="Target task record",
    )
    return payload


def _show_text_at(paths: WorkflowPaths, reference: str, relative: str) -> str:
    return git(paths, "show", f"{reference}:{relative}").stdout


def _release_claims(paths: WorkflowPaths, record: dict[str, Any]) -> list[str]:
    lane = record.get("lane") or {}
    integration = record.get("integration") or {}
    claim_id = lane.get("claim_id")
    owner_generation = lane.get("owner_generation")
    task_id = record.get("task_id")
    released: list[str] = []
    registry_path = paths.shared_runtime / "registry" / "lanes" / f"{lane.get('lane_id')}.json"
    lane_worktree: Path | None = None
    if registry_path.is_file():
        try:
            registry_payload = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"Refusing to release unreadable lane registry: {exc}") from exc
        if isinstance(registry_payload.get("worktree"), str):
            lane_worktree = Path(registry_payload["worktree"])
    candidates = [
        paths.shared_runtime / "claims" / f"{task_id}.json",
        registry_path,
    ]
    for key in lane.get("resource_keys", []):
        candidates.append(paths.shared_runtime / "resources" / f"{resource_key_digest(key)}.json")
    validated_candidates: list[Path] = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise StateError(f"Refusing to remove unreadable runtime claim: {candidate}")
        if payload.get("claim_id") != claim_id or payload.get("owner_generation") != owner_generation:
            raise StateError(f"Runtime claim token/generation mismatch: {candidate}")
        validated_candidates.append(candidate)
    queue_id = integration.get("queue_id")
    queue_to_remove: Path | None = None
    queue_path: Path | None = None
    if queue_id is not None:
        if not isinstance(queue_id, str):
            raise StateError("Integrated task queue ID is invalid.")
        try:
            uuid.UUID(queue_id)
        except ValueError as exc:
            raise StateError("Integrated task queue ID is invalid.") from exc
        queue_path = paths.shared_runtime / "queue" / f"{queue_id}.json"
        if queue_path.is_file():
            try:
                queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise StateError(f"Refusing to remove unreadable runtime queue entry: {queue_path}")
            expected_queue = {
                "queue_id": queue_id,
                "lane_id": lane.get("lane_id"),
                "task_id": task_id,
                "claim_id": claim_id,
                "owner_generation": owner_generation,
            }
            if not isinstance(queue_payload, dict) or any(
                queue_payload.get(field) != expected for field, expected in expected_queue.items()
            ):
                raise StateError(f"Runtime queue token/generation mismatch: {queue_path}")
    pointer = paths.lane_runtime / "lane.json"
    if lane_worktree is not None and lane_worktree.is_dir():
        try:
            pointer = WorkflowPaths.discover(lane_worktree).lane_runtime / "lane.json"
        except (WorkflowPathError, OSError):
            pass
    if pointer.is_file():
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise StateError("Refusing to remove an unreadable lane pointer.")
        if payload.get("claim_id") == claim_id and payload.get("owner_generation") == owner_generation:
            pointer_to_remove = pointer
        else:
            pointer_to_remove = None
    else:
        pointer_to_remove = None
    if queue_path is not None and queue_path.is_file():
        queue_to_remove = queue_path
    for index, candidate in enumerate(validated_candidates):
        candidate.unlink()
        released.append(str(candidate))
        if index == 0:
            fault_injection("confirm-after-first-release")
    if queue_to_remove is not None:
        queue_to_remove.unlink()
        released.append(str(queue_to_remove))
    if pointer_to_remove is not None:
        pointer_to_remove.unlink()
        released.append(str(pointer_to_remove))
    return released


def _confirm(
    paths: WorkflowPaths,
    relative: str,
    target_ref: str,
    closeout_commit: str,
    apply: bool,
    *,
    integrator_token: str | None = None,
    integrator_generation: int | None = None,
) -> None:
    target_oid = rev_parse(paths, target_ref)
    closeout_oid = rev_parse(paths, closeout_commit)
    if not is_ancestor(paths, closeout_oid, target_oid):
        raise StateError("Target ref does not contain the exact closeout commit.")
    record_relative = paths.relative(load_record(paths, relative)[0])
    backlog_relative = paths.relative(paths.tracked("backlog"))
    target_record = _show_json_at(paths, target_ref, record_relative)
    target_backlog = _show_text_at(paths, target_ref, backlog_relative)
    integration = target_record.get("integration") or {}
    if integration.get("status") != "integrated":
        raise StateError("Target ref does not contain integrated task state.")
    expected = integration.get("closeout_state_fingerprint")
    actual = closeout_state_fingerprint(target_record, target_backlog)
    if expected != actual:
        raise StateError("Target closeout state fingerprint does not match the prepared state.")
    if not apply:
        with _role_lock_context(
            paths,
            "integrator",
            "confirm-closeout",
            apply=False,
            token=integrator_token,
            generation=integrator_generation,
        ):
            print(
                json.dumps(
                    {
                        "apply": False,
                        "target_ref": target_ref,
                        "target_commit": target_oid,
                        "closeout_commit": closeout_oid,
                        "closeout_state_fingerprint": actual,
                        "would_release_lane": target_record.get("lane", {}).get("lane_id"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        return
    with _role_lock_context(paths, "coordinator", "confirm-closeout", apply=True):
        with _role_lock_context(
            paths,
            "integrator",
            "confirm-closeout",
            apply=True,
            token=integrator_token,
            generation=integrator_generation,
        ) as integrator_lock:
            if isinstance(integrator_lock, PersistentRoleLock):
                integrator_lock.heartbeat()
            released = _release_claims(paths, target_record)
            journal_path = paths.shared_runtime / "audit" / f"closeout-{target_record.get('task_id')}.json"
            journal = {
                "task_id": target_record.get("task_id"),
                "record": record_relative,
                "target_ref": target_ref,
                "target_commit": target_oid,
                "closeout_commit": closeout_oid,
                "closeout_state_fingerprint": actual,
                "confirmed": True,
                "confirmed_at": utc_now(),
                "released": released,
            }
            atomic_write_json(journal_path, journal)
            if isinstance(integrator_lock, PersistentRoleLock):
                integrator_lock.heartbeat()
    print(f"CLOSEOUT_CONFIRMED commit={closeout_oid} released={len(released)}")


def confirm_closeout(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    _confirm(
        paths,
        args.record,
        args.target_ref,
        args.closeout_commit,
        args.apply,
        integrator_token=args.integrator_token,
        integrator_generation=args.integrator_generation,
    )


def reconcile(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    target_oid = rev_parse(paths, args.target_ref)
    record_path, _ = load_record(paths, args.record)
    relative = paths.relative(record_path)
    target_record = _show_json_at(paths, args.target_ref, relative)
    if (target_record.get("integration") or {}).get("status") != "integrated":
        raise StateError("Target ref has no integrated closeout state to reconcile.")
    # The target head is a conservative recoverable closeout witness.  It may be
    # newer than the original closeout commit, but contains the exact state.
    _confirm(
        paths,
        args.record,
        args.target_ref,
        target_oid,
        args.apply,
        integrator_token=args.integrator_token,
        integrator_generation=args.integrator_generation,
    )


def invalidate(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    def mutation(record: dict[str, Any]) -> None:
        record["status"] = "in_progress"
        record["phase"] = "developer"
        verification = record.get("verification") or {}
        verification["status"] = "invalidated"
        record["verification"] = verification
        integration = record.get("integration") or {}
        integration["status"] = "invalidated"
        evidence = integration.setdefault("evidence", [])
        evidence.append({"kind": "invalidation", "reason": args.reason, "recorded_at": utc_now()})
        record["integration"] = integration

    mutate_record(paths, args.record, args.expected_generation, args.apply, mutation)


def main() -> None:
    args = parser().parse_args()
    try:
        paths = WorkflowPaths.discover(Path.cwd())
        command = args.command
        if command == "record-developer":
            record_developer(paths, args)
        elif command == "record-review":
            record_review(paths, args)
        elif command == "complete-task":
            complete_task(paths, args)
        elif command == "mark-verified":
            mark_verified(paths, args)
        elif command == "record-approval":
            record_approval(paths, args)
        elif command == "prepare-integration":
            prepare_integration(paths, args)
        elif command == "prepare-local-closeout":
            prepare_local_closeout(paths, args)
        elif command == "prepare-remote-closeout":
            prepare_remote_closeout(paths, args)
        elif command == "confirm-closeout":
            confirm_closeout(paths, args)
        elif command == "reconcile":
            reconcile(paths, args)
        elif command == "invalidate-integration":
            invalidate(paths, args)
    except (
        StateError, PersistentRoleLockError, WorkflowDataError, WorkflowPathError,
        LockUnavailable, OSError, subprocess.CalledProcessError,
    ) as exc:
        print(f"[workflow-state] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
