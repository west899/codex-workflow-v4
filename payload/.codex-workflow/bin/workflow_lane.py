#!/usr/bin/env python3
"""Local worktree lanes and optional Git-ref remote claims for Workflow V3."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from workflow_common import (
    WorkflowDataError,
    WorkflowJSONResourceError,
    closeout_state_fingerprint,
    current_branch,
    fault_injection,
    git,
    is_ancestor,
    is_json_integer,
    load_record,
    local_bootstrap_policy_gate,
    require_v4_action,
    task_record_schema_name,
    reset_v4_snapshot_evidence,
    rev_parse,
    utc_now,
    validate_workflow_schema,
    v4_dependency_snapshot,
    validate_v4_contract_identity,
    validate_v4_current_observation_continuations,
    validate_v4_live_architecture_baseline,
    validate_v4_live_dependencies,
    validate_v4_live_backlog_focus,
    validate_v4_planning_risk,
    validate_v4_live_focus_relationship,
    validate_v4_live_requirements_baseline,
)
from workflow_lock import (
    AdvisoryLock,
    LockUnavailable,
    PersistentRoleLock,
    PersistentRoleLockError,
    acquire_role_lock,
    canonical_resource_key,
    heartbeat_role_lock,
    read_role_lock,
    release_role_lock,
    resource_key_digest,
    role_lock_guard_path,
    role_lock_status,
    takeover_role_lock,
    validate_role_lock_access,
)
from workflow_paths import WorkflowPathError, WorkflowPaths, atomic_write_json, normalize_repo_path
from workflow_state import StateError, mutate_record


LEASE_SECONDS = 900
REBUILD_BUCKETS = (
    "registry/lanes",
    "claims",
    "resources",
    "queue",
    "heartbeats",
    "audit",
)


class LaneError(ValueError):
    pass


def _require_v4_integration_preflight(
    lane_paths: WorkflowPaths, record_relative_path: str
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(lane_paths.tracked("bin") / "workflow_check.py"),
            "integration-preflight",
            record_relative_path,
        ],
        cwd=lane_paths.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise LaneError((result.stderr or result.stdout).strip())


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Manage Codex Workflow V4 lanes.")
    sub = result.add_subparsers(dest="command", required=True)

    claim = sub.add_parser("claim")
    claim.add_argument("task_id")
    claim.add_argument("--base", default="main")
    claim.add_argument("--record")
    claim.add_argument("--worktree")
    claim.add_argument("--owner-id")
    claim.add_argument("--apply", action="store_true")

    adopt = sub.add_parser("adopt")
    adopt.add_argument("task_id")
    adopt.add_argument("--record")
    adopt.add_argument("--owner-id")
    adopt.add_argument("--adopt-existing-changes", action="store_true")
    adopt.add_argument("--confirm-diff-sha256")
    adopt.add_argument("--apply", action="store_true")

    listing = sub.add_parser("list")
    listing.add_argument("--all", action="store_true")
    listing.add_argument("--json", action="store_true", dest="as_json")

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("--lane", required=True)

    lock_status = sub.add_parser("lock-status")
    lock_status.add_argument("role", choices=("coordinator", "integrator"))

    lock_acquire = sub.add_parser("lock-acquire")
    lock_acquire.add_argument("role", choices=("coordinator", "integrator"))
    lock_acquire.add_argument("--lease-seconds", type=int, default=900)
    lock_acquire.add_argument("--owner-id")
    lock_acquire.add_argument("--apply", action="store_true")

    lock_heartbeat = sub.add_parser("lock-heartbeat")
    lock_heartbeat.add_argument("role", choices=("coordinator", "integrator"))
    lock_heartbeat.add_argument("--token", required=True)
    lock_heartbeat.add_argument("--generation", required=True, type=int)
    lock_heartbeat.add_argument("--lease-seconds", type=int, default=900)

    lock_release = sub.add_parser("lock-release")
    lock_release.add_argument("role", choices=("coordinator", "integrator"))
    lock_release.add_argument("--token", required=True)
    lock_release.add_argument("--generation", required=True, type=int)
    lock_release.add_argument("--apply", action="store_true")

    lock_takeover = sub.add_parser("lock-takeover")
    lock_takeover.add_argument("role", choices=("coordinator", "integrator"))
    lock_takeover.add_argument("--expected-token", required=True)
    lock_takeover.add_argument("--expected-generation", required=True, type=int)
    lock_takeover.add_argument("--approved-by", required=True)
    lock_takeover.add_argument("--approval-ref", required=True)
    lock_takeover.add_argument("--lease-seconds", type=int, default=900)
    lock_takeover.add_argument("--owner-id")
    lock_takeover.add_argument("--apply", action="store_true")

    expand = sub.add_parser("expand-resources")
    expand.add_argument("lane_id")
    expand.add_argument("--add", action="append", required=True)
    expand.add_argument("--expected-generation", type=int)
    expand.add_argument("--apply", action="store_true")

    queue = sub.add_parser("queue")
    queue.add_argument("lane_id")
    queue.add_argument("--priority", type=int, default=100)
    queue.add_argument("--apply", action="store_true")

    refresh = sub.add_parser("refresh-base")
    refresh.add_argument("lane_id")
    refresh.add_argument("--base", required=True)
    refresh.add_argument("--expected-generation", type=int)
    refresh.add_argument("--apply", action="store_true")

    recover = sub.add_parser("recover")
    recover.add_argument("lane_id")
    recover.add_argument("--takeover", action="store_true")
    recover.add_argument("--owner-id")
    recover.add_argument("--apply", action="store_true")

    rebuild = sub.add_parser("rebuild")
    rebuild.add_argument("--apply", action="store_true")

    release = sub.add_parser("release")
    release.add_argument("lane_id")
    release.add_argument("--abandon", action="store_true")
    release.add_argument("--apply", action="store_true")

    preassign = sub.add_parser("preassign")
    preassign.add_argument("record")
    preassign.add_argument("--owner-id", required=True)
    preassign.add_argument("--base", default="main")
    preassign.add_argument("--apply", action="store_true")

    resume_remote = sub.add_parser("resume-remote")
    resume_remote.add_argument("record")
    resume_remote.add_argument("--owner-id", required=True)
    resume_remote.add_argument("--remote")
    resume_remote.add_argument("--apply", action="store_true")

    remote_claim = sub.add_parser("remote-claim")
    remote_claim.add_argument("record")
    remote_claim.add_argument("--remote")
    remote_claim.add_argument("--lease-seconds", type=int, default=LEASE_SECONDS)
    remote_claim.add_argument("--apply", action="store_true")

    remote_heartbeat = sub.add_parser("remote-heartbeat")
    remote_heartbeat.add_argument("record")
    remote_heartbeat.add_argument("--remote")
    remote_heartbeat.add_argument("--lease-seconds", type=int, default=LEASE_SECONDS)
    remote_heartbeat.add_argument("--apply", action="store_true")

    remote_takeover = sub.add_parser("remote-takeover")
    remote_takeover.add_argument("record")
    remote_takeover.add_argument("--owner-id", required=True)
    remote_takeover.add_argument("--approved-by", required=True)
    remote_takeover.add_argument("--approval-ref", required=True)
    remote_takeover.add_argument("--remote")
    remote_takeover.add_argument("--lease-seconds", type=int, default=LEASE_SECONDS)
    remote_takeover.add_argument("--apply", action="store_true")

    remote_handoff = sub.add_parser("remote-handoff")
    remote_handoff.add_argument("record")
    remote_handoff.add_argument("--owner-id", required=True)
    remote_handoff.add_argument("--to-owner-id", required=True)
    remote_handoff.add_argument("--remote")
    remote_handoff.add_argument("--lease-seconds", type=int, default=LEASE_SECONDS)
    remote_handoff.add_argument("--apply", action="store_true")

    remote_release = sub.add_parser("remote-release")
    remote_release.add_argument("record")
    remote_release.add_argument("--remote")
    remote_release.add_argument("--expected-claim-oid")
    remote_release.add_argument("--apply", action="store_true")
    return result


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    if not normalized:
        raise LaneError("Task/lane identifier has no safe Git component.")
    return normalized


def _remote_owner_uuid(value: str, *, label: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (TypeError, ValueError) as exc:
        raise LaneError(f"{label} must be a random UUID, not a username or hostname.") from exc


def _owner_id(paths: WorkflowPaths, supplied: str | None, *, persist: bool = True) -> str:
    if persist:
        paths.ensure_runtime()
    identity_path = paths.shared_runtime / "owner-id"
    if supplied:
        owner = _remote_owner_uuid(supplied, label="Owner ID")
        if identity_path.is_file() and identity_path.read_text(encoding="utf-8").strip() != owner:
            raise LaneError("Supplied owner ID differs from this runtime's controlled owner ID.")
        if persist and not identity_path.is_file():
            identity_path.write_text(owner + "\n", encoding="utf-8")
        return owner
    if identity_path.is_file():
        try:
            return str(uuid.UUID(identity_path.read_text(encoding="utf-8").strip()))
        except ValueError as exc:
            raise LaneError("Runtime owner-id is invalid; repair it explicitly.") from exc
    owner = str(uuid.uuid4())
    if persist:
        identity_path.write_text(owner + "\n", encoding="utf-8")
    return owner


def _coordinator_lock(paths: WorkflowPaths, action: str, *, apply: bool):
    if not apply:
        validate_role_lock_access(paths.shared_runtime, "coordinator")
        return nullcontext()
    return PersistentRoleLock(paths.shared_runtime, "coordinator", action=action)


def _role_lock_preview(paths: WorkflowPaths, role: str, *, expected_token: str | None = None, expected_generation: int | None = None) -> dict[str, Any]:
    current = role_lock_status(paths.shared_runtime, role)
    status = current.get("effective_status")
    if expected_token is not None:
        if status != "stale":
            raise LaneError(f"Persistent {role} lock takeover requires a stale active lease.")
        if current.get("token") != expected_token or current.get("generation") != expected_generation:
            raise LaneError(f"Persistent {role} lock changed before takeover; token/generation CAS failed.")
        return current
    if status == "active":
        raise LaneError(
            f"Persistent {role} lock is held by token {current.get('token')} generation {current.get('generation')}."
        )
    if status == "stale":
        raise LaneError(
            f"Persistent {role} lock is stale; explicit takeover is required with "
            f"token {current.get('token')} generation {current.get('generation')}."
        )
    return current


def _validate_role_lease_seconds(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise LaneError("Persistent role lock lease_seconds must be a positive integer.")


def lock_status(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    print(json.dumps(role_lock_status(paths.shared_runtime, args.role), ensure_ascii=False, sort_keys=True))


def lock_acquire(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    _validate_role_lease_seconds(args.lease_seconds)
    current = _role_lock_preview(paths, args.role)
    if not args.apply:
        print(
            json.dumps(
                {
                    "apply": False,
                    "role": args.role,
                    "generation": int(current.get("generation", 0)) + 1,
                    "lease_seconds": args.lease_seconds,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    lease = acquire_role_lock(
        paths.shared_runtime,
        args.role,
        action="manual-acquire",
        lease_seconds=args.lease_seconds,
        owner_id=args.owner_id,
    )
    print(json.dumps({"apply": True, **lease.public()}, ensure_ascii=False, sort_keys=True))


def lock_heartbeat(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    _validate_role_lease_seconds(args.lease_seconds)
    lease = heartbeat_role_lock(
        paths.shared_runtime,
        args.role,
        token=args.token,
        generation=args.generation,
        lease_seconds=args.lease_seconds,
    )
    print(json.dumps({"heartbeat": True, **lease.public()}, ensure_ascii=False, sort_keys=True))


def lock_release(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    current = read_role_lock(paths.shared_runtime, args.role)
    if current is None or current.get("state") != "active":
        raise LaneError(f"Persistent {args.role} lock is not active.")
    if current.get("token") != args.token or current.get("generation") != args.generation:
        raise LaneError(f"Persistent {args.role} lock token/generation does not match the supplied lease.")
    if not args.apply:
        print(json.dumps({"apply": False, "role": args.role, "generation": args.generation}, ensure_ascii=False, sort_keys=True))
        return
    released = release_role_lock(
        paths.shared_runtime, args.role, token=args.token, generation=args.generation
    )
    print(json.dumps({"apply": True, **released, "effective_status": "released"}, ensure_ascii=False, sort_keys=True))


def lock_takeover(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    _validate_role_lease_seconds(args.lease_seconds)
    current = _role_lock_preview(
        paths,
        args.role,
        expected_token=args.expected_token,
        expected_generation=args.expected_generation,
    )
    if not args.apply:
        print(
            json.dumps(
                {
                    "apply": False,
                    "role": args.role,
                    "generation": int(current["generation"]) + 1,
                    "approved_by": args.approved_by,
                    "approval_ref": args.approval_ref,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    lease = takeover_role_lock(
        paths.shared_runtime,
        args.role,
        expected_token=args.expected_token,
        expected_generation=args.expected_generation,
        approved_by=args.approved_by,
        approval_ref=args.approval_ref,
        lease_seconds=args.lease_seconds,
        owner_id=args.owner_id,
    )
    print(json.dumps({"apply": True, "takeover": True, **lease.public()}, ensure_ascii=False, sort_keys=True))


def _default_record(paths: WorkflowPaths, task_id: str) -> str:
    return paths.relative(paths.tracked("runs") / f"{_safe_id(task_id)}.json")


def _record_scope(record: dict[str, Any]) -> tuple[list[str], list[str]]:
    scope = record.get("scope")
    if not isinstance(scope, dict):
        raise LaneError("Task record scope is invalid.")
    allowed = scope.get("allowed_paths")
    resources = scope.get("resource_keys")
    if not isinstance(allowed, list) or not allowed or not all(isinstance(item, str) and item for item in allowed):
        raise LaneError("Task record needs non-empty scope.allowed_paths.")
    if not isinstance(resources, list) or not resources or not all(isinstance(item, str) and item for item in resources):
        raise LaneError("Task record needs non-empty scope.resource_keys.")
    canonical = [canonical_resource_key(item) for item in resources]
    if len(canonical) != len(set(canonical)):
        raise LaneError("Task record resource keys collide after canonicalization.")
    return allowed, canonical


def _resource_conflict(left: str, right: str) -> bool:
    left = canonical_resource_key(left)
    right = canonical_resource_key(right)
    if left == right:
        return True
    for prefix in ("path:", "api:", "schema:", "service:", "config:"):
        if left.startswith(prefix) and right.startswith(prefix):
            left_value = left[len(prefix):].rstrip("/")
            right_value = right[len(prefix):].rstrip("/")
            return left_value.startswith(right_value + "/") or right_value.startswith(left_value + "/")
    return False


def _existing_resource_claims(paths: WorkflowPaths) -> list[tuple[Path, dict[str, Any]]]:
    result = []
    root = paths.shared_runtime / "resources"
    if not root.exists():
        return result
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LaneError(f"Unreadable resource claim {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise LaneError(f"Invalid resource claim object: {path}")
        result.append((path, payload))
    return result


def _assert_claims_available(paths: WorkflowPaths, task_id: str, resources: list[str], branch: str, worktree: Path) -> None:
    task_claim = paths.shared_runtime / "claims" / f"{_safe_id(task_id)}.json"
    if task_claim.exists():
        raise LaneError(f"Task already has a local claim: {task_id}")
    registry_paths = list((paths.shared_runtime / "registry" / "lanes").glob("*.json"))
    maximum = paths.layout.get("parallel", {}).get("max_local_lanes", 1)
    if not is_json_integer(maximum, minimum=1):
        raise LaneError("parallel.max_local_lanes must be a positive integer.")
    if len(registry_paths) >= maximum:
        raise LaneError(f"Local lane limit reached ({maximum}).")
    for registry_path in registry_paths:
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LaneError(f"Unreadable lane registry {registry_path}: {exc}") from exc
        if registry.get("branch") == branch:
            raise LaneError(f"Branch is already owned by lane {registry.get('lane_id')}.")
        if Path(str(registry.get("worktree", ""))).resolve() == worktree.resolve():
            raise LaneError(f"Worktree is already owned by lane {registry.get('lane_id')}.")
    for _, existing in _existing_resource_claims(paths):
        existing_key = existing.get("resource_key")
        if not isinstance(existing_key, str):
            raise LaneError("Existing resource claim has no canonical resource_key.")
        for requested in resources:
            if _resource_conflict(existing_key, requested):
                raise LaneError(
                    f"Resource conflict with lane {existing.get('lane_id')}: {requested} vs {existing_key}"
                )


def _lane_payload(
    *, task_id: str, lane_id: str, claim_id: str, owner_id: str,
    branch: str, base_ref: str, base_commit: str, worktree: Path,
    record: str, allowed_paths: list[str], resources: list[str], mode: str,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": 1,
        "task_id": task_id,
        "lane_id": lane_id,
        "claim_id": claim_id,
        "owner_id": owner_id,
        "owner_generation": 1,
        "mode": mode,
        "branch": branch,
        "base_ref": base_ref,
        "base_commit": base_commit,
        "worktree": str(worktree.resolve()),
        "record": record,
        "allowed_paths": allowed_paths,
        "resource_keys": resources,
        "created_at": now,
        "heartbeat_at": now,
        "expires_at": _future(LEASE_SECONDS),
        "state": "claimed",
    }


def _future(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_runtime_claims(paths: WorkflowPaths, payload: dict[str, Any], lane_paths: WorkflowPaths) -> None:
    task_id = payload["task_id"]
    lane_id = payload["lane_id"]
    atomic_write_json(paths.shared_runtime / "claims" / f"{_safe_id(task_id)}.json", payload)
    atomic_write_json(paths.shared_runtime / "registry" / "lanes" / f"{_safe_id(lane_id)}.json", payload)
    for resource in payload["resource_keys"]:
        resource_payload = dict(payload)
        resource_payload["resource_key"] = resource
        atomic_write_json(paths.shared_runtime / "resources" / f"{resource_key_digest(resource)}.json", resource_payload)
    pointer = {
        key: payload[key]
        for key in ("task_id", "lane_id", "claim_id", "owner_id", "owner_generation", "branch", "record")
    }
    atomic_write_json(lane_paths.lane_runtime / "lane.json", pointer)


def _require_v4_record_for_lane(paths: WorkflowPaths, record: dict[str, Any]) -> None:
    if record.get("version") != 4:
        return
    try:
        validate_v4_live_requirements_baseline(paths, record)
        validate_v4_live_architecture_baseline(paths, record)
        validate_v4_live_focus_relationship(paths, record)
        validate_v4_live_backlog_focus(paths, record)
        validate_v4_planning_risk(record)
        validate_v4_live_dependencies(paths, record)
        validate_v4_current_observation_continuations(record)
        validate_v4_contract_identity(record)
    except (
        OSError,
        WorkflowDataError,
        WorkflowPathError,
        WorkflowJSONResourceError,
    ) as exc:
        raise LaneError(str(exc)) from exc


def _load_and_require_v4_lane_record(payload: dict[str, Any]) -> tuple[WorkflowPaths, dict[str, Any]]:
    worktree = Path(str(payload.get("worktree", "")))
    if not worktree.is_dir():
        raise LaneError("Lane worktree does not exist.")
    record_relative = payload.get("record")
    if not isinstance(record_relative, str) or not record_relative:
        raise LaneError("Lane registry record path is invalid.")
    try:
        lane_paths = WorkflowPaths.discover(worktree)
        _, record = load_record(lane_paths, record_relative)
    except (
        OSError,
        WorkflowDataError,
        WorkflowPathError,
        WorkflowJSONResourceError,
    ) as exc:
        raise LaneError(str(exc)) from exc
    _require_v4_record_for_lane(lane_paths, record)
    return lane_paths, record


def _assign_record(paths: WorkflowPaths, record_relative: str, payload: dict[str, Any], *, mode: str) -> None:
    def mutation(record: dict[str, Any]) -> None:
        if record.get("task_id") != payload["task_id"]:
            raise StateError("Task ID differs between record and claim.")
        if record.get("status") not in {"authorized", "in_progress"}:
            raise StateError("Lane claim requires task.status=authorized or in_progress.")
        allowed, resources = _record_scope(record)
        assignment = {
            "assigned_owner_id": payload["owner_id"],
            "assignment_generation": payload["owner_generation"],
            "assigned_at": utc_now(),
            "assigned_by": "workflow_lane",
        }
        record["lane"] = {
            "lane_id": payload["lane_id"],
            "mode": mode,
            "branch": payload["branch"],
            "base_ref": payload["base_ref"],
            "base_commit": payload["base_commit"],
            "claim_id": payload["claim_id"],
            "owner_generation": payload["owner_generation"],
            "assignment": assignment,
            "allowed_paths": allowed,
            "resource_keys": resources,
            "dependency_snapshot": record.get("lane", {}).get("dependency_snapshot") or {
                "backlog_commit": payload["base_commit"], "dependencies": []
            },
        }
        record["base_commit"] = payload["base_commit"]
        record["status"] = "in_progress"
        record["phase"] = "developer"

    # The lane pointer is created only after this mutation.  Suppress the
    # shared STATUS write here so an isolated branch never carries a status
    # snapshot that would conflict with coordinator closeout on rebase.
    mutate_record(
        paths,
        record_relative,
        None,
        True,
        mutation,
        sync_status=False,
        allowed_versions=(3, 4),
    )


def claim(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    if paths.layout.get("parallel", {}).get("mode") != "local_worktree":
        raise LaneError("local_worktree mode is not enabled in layout.json.")
    record_relative = args.record or _default_record(paths, args.task_id)
    _, record = load_record(paths, record_relative)
    if record.get("task_id") != args.task_id:
        raise LaneError("Task record does not match requested task ID.")
    _require_v4_record_for_lane(paths, record)
    allowed, resources = _record_scope(record)
    owner_id = _owner_id(paths, args.owner_id, persist=args.apply)
    suffix = uuid.uuid4().hex[:8]
    lane_id = f"lane-{_safe_id(args.task_id)}-{suffix}"
    claim_id = str(uuid.uuid4())
    branch = f"codex/task/{_safe_id(args.task_id)}-{suffix}"
    base_commit = rev_parse(paths, args.base)
    if args.worktree:
        worktree = Path(args.worktree).expanduser().resolve()
    else:
        worktree = (paths.root.parent / f"{paths.root.name}-worktrees" / lane_id).resolve()
    if paths.root == worktree or paths.root in worktree.parents:
        raise LaneError("Lane worktree must not be nested in the coordinator worktree.")
    payload = _lane_payload(
        task_id=args.task_id, lane_id=lane_id, claim_id=claim_id,
        owner_id=owner_id, branch=branch, base_ref=args.base, base_commit=base_commit,
        worktree=worktree, record=record_relative, allowed_paths=allowed,
        resources=resources, mode="local_worktree",
    )
    with _coordinator_lock(paths, "claim", apply=args.apply):
        _assert_claims_available(paths, args.task_id, resources, branch, worktree)
        if worktree.exists():
            raise LaneError(f"Requested worktree path already exists: {worktree}")
        if not args.apply:
            print(json.dumps({"apply": False, **payload}, ensure_ascii=False, sort_keys=True))
            return
        paths.ensure_runtime()
        journal_path = paths.shared_runtime / "audit" / f"claim-{claim_id}.json"
        atomic_write_json(journal_path, {**payload, "journal_state": "creating_worktree"})
        result = git(paths, "worktree", "add", "-b", branch, str(worktree), base_commit, check=False)
        if result.returncode != 0:
            raise LaneError((result.stderr or result.stdout).strip())
        lane_paths = WorkflowPaths.discover(worktree)
        try:
            _assign_record(lane_paths, record_relative, payload, mode="local_worktree")
            _write_runtime_claims(paths, payload, lane_paths)
            atomic_write_json(journal_path, {**payload, "journal_state": "active"})
        except Exception as exc:
            atomic_write_json(journal_path, {**payload, "journal_state": "recovery_required", "error": str(exc)})
            raise LaneError(
                f"Lane worktree was preserved after partial claim failure; recover {lane_id}: {exc}"
            ) from exc
    print(f"LANE_CLAIMED id={lane_id} worktree={worktree} branch={branch}")


def _dirty_digest(paths: WorkflowPaths) -> tuple[str, str]:
    result = subprocess.run(
        ["git", "-C", str(paths.root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True,
        check=True,
    ).stdout
    return hashlib.sha256(result).hexdigest(), result.decode("utf-8", errors="surrogateescape")


def adopt(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    if paths.layout.get("parallel", {}).get("mode") != "local_worktree":
        raise LaneError("local_worktree mode is not enabled in layout.json.")
    if (paths.lane_runtime / "lane.json").exists():
        raise LaneError("Current worktree already has a lane pointer.")
    branch = current_branch(paths)
    if not branch:
        raise LaneError("Adopt requires a named current branch.")
    digest, dirty = _dirty_digest(paths)
    if dirty and not args.adopt_existing_changes:
        raise LaneError(f"Adopt requires a clean worktree; dirty diff token is {digest}.")
    if dirty and args.confirm_diff_sha256 != digest:
        raise LaneError(f"Dirty adopt requires --confirm-diff-sha256 {digest}.")
    record_relative = args.record or _default_record(paths, args.task_id)
    _, record = load_record(paths, record_relative)
    _require_v4_record_for_lane(paths, record)
    allowed, resources = _record_scope(record)
    owner_id = _owner_id(paths, args.owner_id, persist=args.apply)
    suffix = uuid.uuid4().hex[:8]
    lane_id = f"lane-{_safe_id(args.task_id)}-{suffix}"
    payload = _lane_payload(
        task_id=args.task_id, lane_id=lane_id, claim_id=str(uuid.uuid4()), owner_id=owner_id,
        branch=branch, base_ref=str(record.get("lane", {}).get("base_ref") or "main"),
        base_commit=str(record.get("base_commit")), worktree=paths.root,
        record=record_relative, allowed_paths=allowed, resources=resources,
        mode="local_worktree",
    )
    with _coordinator_lock(paths, "adopt", apply=args.apply):
        _assert_claims_available(paths, args.task_id, resources, branch, paths.root)
        if not args.apply:
            print(json.dumps({"apply": False, "dirty_diff_sha256": digest, **payload}, ensure_ascii=False, sort_keys=True))
            return
        paths.ensure_runtime()
        _assign_record(paths, record_relative, payload, mode="local_worktree")
        _write_runtime_claims(paths, payload, paths)
    print(f"LANE_ADOPTED id={lane_id} branch={branch}")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _effective_status(payload: dict[str, Any]) -> str:
    expires = _parse_time(payload.get("expires_at"))
    if payload.get("state") == "stale" or expires is None or expires < datetime.now(timezone.utc):
        return "stale"
    worktree = Path(str(payload.get("worktree", "")))
    record_relative = payload.get("record")
    record = None
    record_unreadable = False
    if worktree.is_dir() and isinstance(record_relative, str):
        try:
            lane_paths = WorkflowPaths.discover(worktree)
            _, record = load_record(lane_paths, record_relative)
        except (
            OSError,
            json.JSONDecodeError,
            WorkflowDataError,
            WorkflowPathError,
            WorkflowJSONResourceError,
            LaneError,
            StateError,
        ):
            record_unreadable = True
    if record_unreadable:
        return "stale"
    if isinstance(record, dict):
        integration = record.get("integration") or {}
        verification = record.get("verification") or {}
        if integration.get("status") == "queued":
            return "queued"
        if integration.get("status") == "integrated":
            return "integrating"
        if record.get("status") == "completed" and verification.get("status") == "passed":
            return "verified"
        if record.get("phase") == "review":
            return "reviewing"
        if record.get("status") == "in_progress":
            return "active"
    return "claimed"


def _verified_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    verification = record.get("verification") or {}
    if verification.get("status") != "passed":
        raise LaneError("Queue requires verification.status=passed.")
    snapshot_id = verification.get("snapshot_id")
    delivery_commit = verification.get("delivery_commit")
    delivery_hash = verification.get("delivery_hash")
    changed_paths = verification.get("changed_paths")
    if not all(isinstance(value, str) and value for value in (snapshot_id, delivery_commit, delivery_hash)):
        raise LaneError("Verified task has an incomplete sealed snapshot.")
    if not isinstance(changed_paths, list) or not all(isinstance(path, str) and path for path in changed_paths):
        raise LaneError("Verified task has invalid changed paths.")
    try:
        normalized_paths = [normalize_repo_path(path) for path in changed_paths]
    except WorkflowPathError as exc:
        raise LaneError(f"Verified task has unsafe changed paths: {exc}") from exc
    if changed_paths != normalized_paths or changed_paths != sorted(set(changed_paths)):
        raise LaneError("Verified task changed paths must be sorted, unique, and canonical.")
    path_keys = [canonical_resource_key(f"path:{path}") for path in changed_paths]
    if len(path_keys) != len(set(path_keys)):
        raise LaneError("Verified task changed paths collide after cross-platform canonicalization.")
    return {
        "snapshot_id": snapshot_id,
        "delivery_commit": delivery_commit,
        "delivery_hash": delivery_hash,
        "changed_paths": changed_paths,
    }


def _queue_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = {
        key: payload.get(key)
        for key in ("snapshot_id", "delivery_commit", "delivery_hash", "changed_paths")
    }
    return _verified_snapshot({"verification": {"status": "passed", **snapshot}})


def _queued_lane_snapshot(paths: WorkflowPaths, queue_path: Path, queue: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    queue_id = queue.get("queue_id")
    lane_id = queue.get("lane_id")
    task_id = queue.get("task_id")
    claim_id = queue.get("claim_id")
    owner_generation = queue.get("owner_generation")
    if not all(isinstance(value, str) and value for value in (queue_id, lane_id, task_id, claim_id)):
        raise LaneError(f"Queue entry is missing an identity field: {queue_path}")
    try:
        uuid.UUID(queue_id)
    except ValueError as exc:
        raise LaneError(f"Queue entry has an invalid ID: {queue_path}") from exc
    if queue_path.stem != queue_id:
        raise LaneError(f"Queue entry filename does not match its ID: {queue_path}")
    if not is_json_integer(owner_generation, minimum=1):
        raise LaneError(f"Queue entry has an invalid owner generation: {queue_path}")
    if not isinstance(queue.get("queue_priority"), int) or isinstance(queue.get("queue_priority"), bool):
        raise LaneError(f"Queue entry has an invalid priority: {queue_path}")
    if not isinstance(queue.get("queued_at"), str) or _parse_time(queue.get("queued_at")) is None:
        raise LaneError(f"Queue entry has an invalid timestamp: {queue_path}")
    queued_snapshot = _queue_snapshot(queue)
    _, registry = _registry(paths, lane_id)
    expected_registry = {
        "lane_id": lane_id,
        "task_id": task_id,
        "claim_id": claim_id,
        "owner_generation": owner_generation,
    }
    if any(registry.get(field) != value for field, value in expected_registry.items()):
        raise LaneError(f"Queue entry does not match the current lane registry: {queue_path}")
    if _effective_status(registry) != "queued":
        raise LaneError(f"Queue entry does not belong to a live queued lane: {queue_path}")
    lane_paths = WorkflowPaths.discover(Path(str(registry.get("worktree", ""))))
    _, record = load_record(lane_paths, str(registry.get("record", "")))
    lane = record.get("lane") or {}
    integration = record.get("integration") or {}
    expected_record = {
        "task_id": task_id,
        "lane_id": lane_id,
        "claim_id": claim_id,
        "owner_generation": owner_generation,
        "queue_id": queue_id,
        "queue_priority": queue.get("queue_priority"),
        "queued_at": queue.get("queued_at"),
    }
    record_values = {
        "task_id": record.get("task_id"),
        "lane_id": lane.get("lane_id"),
        "claim_id": lane.get("claim_id"),
        "owner_generation": lane.get("owner_generation"),
        "queue_id": integration.get("queue_id"),
        "queue_priority": integration.get("queue_priority"),
        "queued_at": integration.get("queued_at"),
    }
    if integration.get("status") != "queued" or record_values != expected_record:
        raise LaneError(f"Queue entry does not match its tracked task record: {queue_path}")
    record_snapshot = _verified_snapshot(record)
    if record_snapshot != queued_snapshot:
        raise LaneError(f"Queue entry does not match its verified task snapshot: {queue_path}")
    if record.get("version") == 4:
        try:
            expected_dependencies = v4_dependency_snapshot(lane_paths, record)
        except WorkflowDataError as exc:
            raise LaneError(str(exc)) from exc
        if queue.get("dependency_snapshot", []) != expected_dependencies:
            raise LaneError(f"Queue entry dependency snapshot is stale: {queue_path}")
        _require_v4_integration_preflight(
            lane_paths, str(registry.get("record", ""))
        )
    return registry, queued_snapshot


def _assert_queue_paths_available(paths: WorkflowPaths, candidate: dict[str, Any]) -> None:
    conflicts: list[str] = []
    for queue_path in sorted((paths.shared_runtime / "queue").glob("*.json")):
        try:
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LaneError(f"Unreadable integration queue entry: {queue_path}: {exc}") from exc
        if not isinstance(queue, dict):
            raise LaneError(f"Invalid integration queue entry: {queue_path}")
        registry, queued_snapshot = _queued_lane_snapshot(paths, queue_path, queue)
        rendered_paths = sorted(
            {
                queued_path
                if candidate_path == queued_path
                else f"{queued_path} <-> {candidate_path}"
                for candidate_path in candidate["changed_paths"]
                for queued_path in queued_snapshot["changed_paths"]
                if _resource_conflict(f"path:{candidate_path}", f"path:{queued_path}")
            }
        )
        if rendered_paths:
            conflicts.append(
                f"lane {registry['lane_id']} task {registry['task_id']}: {', '.join(rendered_paths)}"
            )
    if conflicts:
        raise LaneError("Integration queue actual changed paths overlap: " + "; ".join(conflicts))


def _runtime_bucket(root: Path, relative: str) -> Path:
    return root.joinpath(*relative.split("/"))


def _worktree_roots(paths: WorkflowPaths) -> list[Path]:
    result = git(paths, "worktree", "list", "--porcelain")
    roots = [
        Path(line.removeprefix("worktree ")).resolve()
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    ]
    if not roots:
        raise LaneError("Git reported no worktrees to rebuild.")
    return roots


def _rebuild_lane(paths: WorkflowPaths) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    pointer_path = paths.lane_runtime / "lane.json"
    if not pointer_path.is_file():
        return None
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneError(f"Lane pointer is unreadable in {paths.root}: {exc}") from exc
    if not isinstance(pointer, dict):
        raise LaneError(f"Lane pointer root is invalid in {paths.root}.")
    for field in ("task_id", "lane_id", "claim_id", "owner_id", "branch", "record"):
        if not isinstance(pointer.get(field), str) or not pointer[field]:
            raise LaneError(f"Lane pointer {field} is invalid in {paths.root}.")
    if not isinstance(pointer.get("owner_generation"), int) or pointer["owner_generation"] < 1:
        raise LaneError(f"Lane pointer owner_generation is invalid in {paths.root}.")
    record_path, record = load_record(paths, pointer["record"])
    _require_v4_record_for_lane(paths, record)
    lane = record.get("lane") or {}
    if lane.get("mode") != "local_worktree":
        return None
    for field in ("task_id", "lane_id", "claim_id", "owner_generation", "branch"):
        expected = record.get("task_id") if field == "task_id" else lane.get(field)
        if pointer.get(field) != expected:
            raise LaneError(f"Lane pointer {field} does not match task record in {paths.root}.")
    if current_branch(paths) != lane.get("branch"):
        raise LaneError(f"Worktree branch does not match its task record in {paths.root}.")
    assignment = lane.get("assignment") or {}
    if assignment.get("assigned_owner_id") != pointer["owner_id"] or assignment.get("assignment_generation") != lane.get("owner_generation"):
        raise LaneError(f"Lane assignment does not match pointer ownership in {paths.root}.")
    allowed, resources = _record_scope(record)
    for field in ("base_ref", "base_commit"):
        if not isinstance(lane.get(field), str) or not lane[field]:
            raise LaneError(f"Task lane {field} is invalid in {paths.root}.")
    now = utc_now()
    payload = {
        "schema_version": 1,
        "task_id": record["task_id"],
        "lane_id": lane["lane_id"],
        "claim_id": lane["claim_id"],
        "owner_id": pointer["owner_id"],
        "owner_generation": lane["owner_generation"],
        "mode": "local_worktree",
        "branch": lane["branch"],
        "base_ref": lane["base_ref"],
        "base_commit": lane["base_commit"],
        "worktree": str(paths.root),
        "record": paths.relative(record_path),
        "allowed_paths": allowed,
        "resource_keys": resources,
        "created_at": now,
        "heartbeat_at": None,
        "expires_at": None,
        "state": "stale",
        "recovered_at": now,
    }
    integration = record.get("integration") or {}
    if integration.get("status") != "queued":
        return payload, None
    queue_id = integration.get("queue_id")
    queued_at = integration.get("queued_at")
    priority = integration.get("queue_priority")
    if not isinstance(queue_id, str) or not queue_id:
        raise LaneError(f"Queued lane has no queue ID in {paths.root}.")
    try:
        uuid.UUID(queue_id)
    except ValueError as exc:
        raise LaneError(f"Queued lane has an invalid queue ID in {paths.root}.") from exc
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise LaneError(f"Queued lane has an invalid priority in {paths.root}.")
    if not isinstance(queued_at, str) or _parse_time(queued_at) is None:
        raise LaneError(f"Queued lane has an invalid queued_at value in {paths.root}.")
    snapshot = _verified_snapshot(record)
    if record.get("version") == 4:
        try:
            snapshot["dependency_snapshot"] = v4_dependency_snapshot(paths, record)
        except WorkflowDataError as exc:
            raise LaneError(str(exc)) from exc
    return payload, {
        "queue_id": queue_id,
        "lane_id": payload["lane_id"],
        "task_id": payload["task_id"],
        "claim_id": payload["claim_id"],
        "owner_generation": payload["owner_generation"],
        "queue_priority": priority,
        "queued_at": queued_at,
        **snapshot,
    }


def _validate_rebuild(lanes: list[dict[str, Any]], queues: list[dict[str, Any]]) -> None:
    for field in ("task_id", "lane_id", "branch", "worktree", "record"):
        values = [str(item[field]) for item in lanes]
        if len(values) != len(set(values)):
            raise LaneError(f"Cannot rebuild duplicate lane {field} values.")
    for mapper, label in ((_safe_id, "task runtime filenames"), (_safe_id, "lane runtime filenames")):
        field = "task_id" if label.startswith("task") else "lane_id"
        values = [mapper(str(item[field])) for item in lanes]
        if len(values) != len(set(values)):
            raise LaneError(f"Cannot rebuild colliding {label}.")
    for index, lane in enumerate(lanes):
        for resource in lane["resource_keys"]:
            for other in lanes[:index]:
                for existing in other["resource_keys"]:
                    if _resource_conflict(resource, existing):
                        raise LaneError(
                            f"Cannot rebuild conflicting resource claims: {lane['lane_id']} and {other['lane_id']}."
                        )
    queue_ids = [item["queue_id"] for item in queues]
    if len(queue_ids) != len(set(queue_ids)):
        raise LaneError("Cannot rebuild duplicate queue IDs.")


def _write_rebuild_staging(staging: Path, lanes: list[dict[str, Any]], queues: list[dict[str, Any]]) -> None:
    for relative in REBUILD_BUCKETS:
        _runtime_bucket(staging, relative).mkdir(parents=True, exist_ok=True)
    for lane in lanes:
        atomic_write_json(_runtime_bucket(staging, "registry/lanes") / f"{_safe_id(lane['lane_id'])}.json", lane)
        atomic_write_json(_runtime_bucket(staging, "claims") / f"{_safe_id(lane['task_id'])}.json", lane)
        for resource in lane["resource_keys"]:
            resource_payload = dict(lane)
            resource_payload["resource_key"] = resource
            atomic_write_json(_runtime_bucket(staging, "resources") / f"{resource_key_digest(resource)}.json", resource_payload)
    for queue in queues:
        atomic_write_json(_runtime_bucket(staging, "queue") / f"{queue['queue_id']}.json", queue)


def _swap_rebuild_runtime(paths: WorkflowPaths, staging: Path, backup: Path) -> None:
    backup.mkdir(parents=True, exist_ok=False)
    journal_path = backup / "journal.json"
    atomic_write_json(journal_path, {"state": "prepared", "buckets": list(REBUILD_BUCKETS)})
    swaps: list[tuple[str, Path, Path, bool]] = []
    try:
        for relative in REBUILD_BUCKETS:
            target = _runtime_bucket(paths.shared_runtime, relative)
            staged = _runtime_bucket(staging, relative)
            archived = _runtime_bucket(backup / "runtime", relative)
            archived.parent.mkdir(parents=True, exist_ok=True)
            had_target = target.exists()
            if had_target:
                os.replace(target, archived)
            try:
                os.replace(staged, target)
            except OSError:
                if had_target and archived.exists():
                    os.replace(archived, target)
                raise
            swaps.append((relative, target, archived, had_target))
    except OSError as exc:
        failed_root = backup / "failed-rebuild"
        for relative, target, archived, had_target in reversed(swaps):
            if target.exists():
                failed_target = _runtime_bucket(failed_root, relative)
                failed_target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, failed_target)
            if had_target and archived.exists():
                os.replace(archived, target)
        atomic_write_json(journal_path, {"state": "rollback_attempted", "error": str(exc)})
        raise LaneError(f"Runtime rebuild swap failed: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    atomic_write_json(journal_path, {"state": "complete", "buckets": list(REBUILD_BUCKETS)})


def rebuild(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    if paths.layout.get("parallel", {}).get("mode") != "local_worktree":
        raise LaneError("rebuild requires local_worktree mode in layout.json.")
    with _coordinator_lock(paths, "rebuild", apply=args.apply):
        lanes: list[dict[str, Any]] = []
        queues: list[dict[str, Any]] = []
        for root in _worktree_roots(paths):
            if not root.is_dir():
                raise LaneError(f"Cannot rebuild missing worktree: {root}")
            lane_paths = WorkflowPaths.discover(root)
            recovered = _rebuild_lane(lane_paths)
            if recovered is None:
                continue
            lane, queue = recovered
            lanes.append(lane)
            if queue is not None:
                queues.append(queue)
        _validate_rebuild(lanes, queues)
        queues.sort(key=lambda item: (item["queue_priority"], item["queued_at"], item["queue_id"]))
        if not args.apply:
            print(
                json.dumps(
                    {"apply": False, "lanes": lanes, "queues": queues, "owners_marked_stale": True},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return
        paths.ensure_runtime()
        rebuild_id = f"rebuild-{uuid.uuid4().hex}"
        staging = paths.shared_runtime / f".{rebuild_id}-staging"
        backup = paths.shared_runtime / "backups" / rebuild_id
        _write_rebuild_staging(staging, lanes, queues)
        _swap_rebuild_runtime(paths, staging, backup)
    print(f"LANE_REGISTRY_REBUILT lanes={len(lanes)} queues={len(queues)} backup={backup}")


def list_lanes(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    rows = []
    registry = paths.shared_runtime / "registry" / "lanes"
    if not registry.is_dir():
        if args.as_json:
            print(json.dumps([], ensure_ascii=False))
            return
        print("No local lanes.")
        return
    for registry_path in sorted(registry.glob("*.json")):
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rows.append({"lane_id": registry_path.stem, "effective_status": "broken", "error": str(exc)})
            continue
        if not isinstance(payload, dict):
            rows.append({"lane_id": registry_path.stem, "effective_status": "broken"})
            continue
        payload = dict(payload)
        worktree = Path(str(payload.get("worktree", "")))
        record_relative = payload.get("record")
        if worktree.is_dir() and isinstance(record_relative, str) and record_relative:
            try:
                lane_paths = WorkflowPaths.discover(worktree)
                _, record = load_record(lane_paths, record_relative)
            except (
                OSError,
                WorkflowDataError,
                WorkflowPathError,
                WorkflowJSONResourceError,
            ):
                record = None
                lane_paths = None
            else:
                _require_v4_record_for_lane(lane_paths, record)
        payload["effective_status"] = _effective_status(payload)
        rows.append(payload)
    if args.as_json:
        print(json.dumps(rows, ensure_ascii=False, sort_keys=True))
        return
    if not rows:
        print("No local lanes.")
        return
    for row in rows:
        print(
            f"{row.get('lane_id')}  {row.get('effective_status')}  "
            f"{row.get('task_id')}  {row.get('branch')}  {row.get('worktree', '')}"
        )


def _registry(paths: WorkflowPaths, lane_id: str) -> tuple[Path, dict[str, Any]]:
    path = paths.shared_runtime / "registry" / "lanes" / f"{_safe_id(lane_id)}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneError(f"Unable to load lane registry {lane_id}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("lane_id") != lane_id:
        raise LaneError("Lane registry identity is invalid.")
    return path, payload


def heartbeat(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    paths.ensure_runtime()
    registry_path, payload = _registry(paths, args.lane)
    lane_paths, _record = _load_and_require_v4_lane_record(payload)
    if _effective_status(payload) == "stale":
        raise LaneError("Heartbeat refuses a stale lane; use recover --takeover after confirming ownership.")
    pointer_path = lane_paths.lane_runtime / "lane.json"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneError(f"Lane pointer is unreadable: {exc}") from exc
    for field in ("lane_id", "claim_id", "owner_id", "owner_generation"):
        if pointer.get(field) != payload.get(field):
            raise LaneError(f"Heartbeat token mismatch for {field}.")
    with AdvisoryLock(paths.shared_runtime / "locks" / f"lane-{_safe_id(args.lane)}.lock", timeout=2):
        _, current = _registry(paths, args.lane)
        if current.get("claim_id") != payload.get("claim_id") or current.get("owner_generation") != payload.get("owner_generation"):
            raise LaneError("Lane changed while heartbeat was being prepared.")
        current["heartbeat_at"] = utc_now()
        current["expires_at"] = _future(LEASE_SECONDS)
        atomic_write_json(registry_path, current)
        claim_path = paths.shared_runtime / "claims" / f"{_safe_id(current['task_id'])}.json"
        atomic_write_json(claim_path, current)
        atomic_write_json(paths.shared_runtime / "heartbeats" / f"{_safe_id(args.lane)}.json", current)
        for resource in current.get("resource_keys", []):
            resource_payload = dict(current)
            resource_payload["resource_key"] = resource
            atomic_write_json(paths.shared_runtime / "resources" / f"{resource_key_digest(resource)}.json", resource_payload)
    print(f"HEARTBEAT_OK lane={args.lane} expires={current['expires_at']}")


def expand_resources(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    registry_path, payload = _registry(paths, args.lane_id)
    additions = [canonical_resource_key(item) for item in args.add]
    new_resources = sorted(set(payload.get("resource_keys", [])) | set(additions))
    with _coordinator_lock(paths, "expand-resources", apply=args.apply):
        for _, existing in _existing_resource_claims(paths):
            if existing.get("lane_id") == args.lane_id:
                continue
            for requested in additions:
                if _resource_conflict(str(existing.get("resource_key")), requested):
                    raise LaneError(f"Resource conflict with lane {existing.get('lane_id')}: {requested}")
        lane_paths = WorkflowPaths.discover(Path(payload["worktree"]))
        _, current_record = load_record(lane_paths, payload["record"])
        _require_v4_record_for_lane(lane_paths, current_record)
        if not args.apply:
            print(json.dumps({"apply": False, "lane_id": args.lane_id, "resource_keys": new_resources}, ensure_ascii=False, sort_keys=True))
            return

        def mutation(record: dict[str, Any]) -> None:
            lane = record.get("lane") or {}
            if lane.get("claim_id") != payload.get("claim_id") or lane.get("owner_generation") != payload.get("owner_generation"):
                raise StateError("Task record lane token/generation mismatch.")
            lane["resource_keys"] = new_resources
            record["lane"] = lane
            # V4 contract_fingerprint covers scope.resource_keys; extra runtime
            # claims stay on lane.resource_keys only.
            if record.get("version") != 4:
                record["scope"]["resource_keys"] = new_resources

        mutate_record(
            lane_paths,
            payload["record"],
            args.expected_generation,
            True,
            mutation,
            allowed_versions=(3, 4),
        )
        payload["resource_keys"] = new_resources
        atomic_write_json(registry_path, payload)
        atomic_write_json(paths.shared_runtime / "claims" / f"{_safe_id(payload['task_id'])}.json", payload)
        for resource in new_resources:
            resource_payload = dict(payload)
            resource_payload["resource_key"] = resource
            atomic_write_json(paths.shared_runtime / "resources" / f"{resource_key_digest(resource)}.json", resource_payload)
    print(f"RESOURCES_EXPANDED lane={args.lane_id} count={len(new_resources)}")


def queue_lane(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    with _coordinator_lock(paths, "queue", apply=args.apply):
        registry_path, payload = _registry(paths, args.lane_id)
        lane_paths, queued_record = _load_and_require_v4_lane_record(payload)
        if _effective_status(payload) != "verified":
            raise LaneError("Only a verified lane can enter the integration queue.")
        if queued_record.get("version") == 4:
            _require_v4_integration_preflight(lane_paths, payload["record"])
        expected_generation = queued_record.get("generation")
        if not is_json_integer(expected_generation, minimum=0):
            raise LaneError("Queued task record generation is invalid.")
        lane = queued_record.get("lane") or {}
        if (
            queued_record.get("task_id") != payload.get("task_id")
            or lane.get("claim_id") != payload.get("claim_id")
            or lane.get("owner_generation") != payload.get("owner_generation")
        ):
            raise LaneError("Queued task record does not match its lane token or generation.")
        integration = queued_record.get("integration") or {}
        if integration.get("status") != "pending":
            raise LaneError("prepare-integration must run before queue.")
        if integration.get("mode") == "local_bootstrap":
            local_bootstrap_policy_gate(
                lane_paths.layout.get("integration_policy"),
                lane_paths.tracked("backlog").read_text(encoding="utf-8"),
                task_id=queued_record.get("task_id"),
            )
        snapshot = _verified_snapshot(queued_record)
        queue_id = str(uuid.uuid4())
        queue_payload = {
            "queue_id": queue_id,
            "lane_id": args.lane_id,
            "task_id": payload["task_id"],
            "claim_id": payload["claim_id"],
            "owner_generation": payload["owner_generation"],
            "queue_priority": args.priority,
            "queued_at": utc_now(),
            **snapshot,
        }
        if queued_record.get("version") == 4:
            queue_payload["dependency_snapshot"] = v4_dependency_snapshot(
                lane_paths, queued_record
            )
        _assert_queue_paths_available(paths, snapshot)
        if not args.apply:
            print(json.dumps({"apply": False, **queue_payload}, ensure_ascii=False, sort_keys=True))
            return

        def mutation(record: dict[str, Any]) -> None:
            if record.get("version") == 4:
                _require_v4_integration_preflight(lane_paths, payload["record"])
            current_lane = record.get("lane") or {}
            if (
                record.get("task_id") != payload.get("task_id")
                or current_lane.get("claim_id") != payload.get("claim_id")
                or current_lane.get("owner_generation") != payload.get("owner_generation")
            ):
                raise StateError("Task record lane token/generation changed during queue admission.")
            integration = record.get("integration") or {}
            if integration.get("status") != "pending":
                raise StateError("prepare-integration must run before queue.")
            if integration.get("mode") == "local_bootstrap":
                local_bootstrap_policy_gate(
                    lane_paths.layout.get("integration_policy"),
                    lane_paths.tracked("backlog").read_text(encoding="utf-8"),
                    task_id=record.get("task_id"),
                )
            if _verified_snapshot(record) != snapshot:
                raise StateError("Verified task snapshot changed during queue admission.")
            if record.get("version") == 4:
                current_dependency_snapshot = v4_dependency_snapshot(lane_paths, record)
                if current_dependency_snapshot != queue_payload.get("dependency_snapshot"):
                    raise StateError("V4 dependency snapshot changed during queue admission.")
            integration.update(
                {"status": "queued", "queue_id": queue_id, "queued_at": queue_payload["queued_at"], "queue_priority": args.priority}
            )
            record["integration"] = integration

        mutate_record(
            lane_paths,
            payload["record"],
            expected_generation,
            True,
            mutation,
            allowed_versions=(3, 4),
        )
        fault_injection("queue-after-record")
        payload["state"] = "queued"
        atomic_write_json(registry_path, payload)
        atomic_write_json(paths.shared_runtime / "queue" / f"{queue_id}.json", queue_payload)
    print(f"LANE_QUEUED lane={args.lane_id} queue={queue_id}")


def _queued_entry_for_refresh(
    paths: WorkflowPaths, payload: dict[str, Any], record: dict[str, Any]
) -> tuple[Path | None, dict[str, Any] | None]:
    integration = record.get("integration") or {}
    if integration.get("status") != "queued":
        if any(integration.get(field) is not None for field in ("queue_id", "queued_at", "queue_priority")):
            raise LaneError("Non-queued lane retains integration queue metadata.")
        return None, None
    queue_id = integration.get("queue_id")
    if not isinstance(queue_id, str):
        raise LaneError("Queued lane has no queue ID.")
    try:
        uuid.UUID(queue_id)
    except ValueError as exc:
        raise LaneError("Queued lane has an invalid queue ID.") from exc
    path = paths.shared_runtime / "queue" / f"{queue_id}.json"
    try:
        queue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneError(f"Queued lane has no readable queue entry: {exc}") from exc
    expected = {
        "queue_id": queue_id,
        "lane_id": payload.get("lane_id"),
        "task_id": payload.get("task_id"),
        "claim_id": payload.get("claim_id"),
        "owner_generation": payload.get("owner_generation"),
        "queue_priority": integration.get("queue_priority"),
        "queued_at": integration.get("queued_at"),
    }
    if not isinstance(queue, dict) or any(queue.get(field) != value for field, value in expected.items()):
        raise LaneError("Queued lane entry does not match its current claim token, generation, or priority.")
    if record.get("version") == 4:
        try:
            expected_dependencies = v4_dependency_snapshot(paths, record)
        except WorkflowDataError as exc:
            raise LaneError(str(exc)) from exc
        if queue.get("dependency_snapshot", []) != expected_dependencies:
            raise LaneError("Queued lane dependency snapshot is stale.")
    return path, queue


def _require_v4_refreshable(record: dict[str, Any]) -> None:
    if record.get("version") == 4:
        integration = record.get("integration")
        if (
            not isinstance(integration, dict)
            or integration.get("status") not in {"not_ready", "invalidated"}
        ):
            raise StateError(
                "V4 refresh-base cannot reopen pending or queued integration; "
                "use explicit abandon and rebuild the lane."
            )


def _reset_after_base_refresh(record: dict[str, Any], base_ref: str, base_commit: str) -> None:
    _require_v4_refreshable(record)
    lane = record.get("lane")
    if not isinstance(lane, dict):
        raise StateError("Task record lane is invalid.")
    acceptance = record.get("acceptance")
    if not isinstance(acceptance, list) or any(not isinstance(item, dict) for item in acceptance):
        raise StateError("Task record acceptance is invalid.")
    for item in acceptance:
        item["status"] = "pending"
        item["evidence"] = []
    dependency_snapshot = lane.get("dependency_snapshot")
    if not isinstance(dependency_snapshot, dict):
        dependency_snapshot = {"dependencies": []}
    dependency_snapshot["backlog_commit"] = base_commit
    if not isinstance(dependency_snapshot.get("dependencies"), list):
        raise StateError("Task record dependency snapshot is invalid.")
    lane["base_ref"] = base_ref
    lane["base_commit"] = base_commit
    lane["dependency_snapshot"] = dependency_snapshot
    record["lane"] = lane
    record["base_commit"] = base_commit
    if record.get("version") == 4:
        reset_v4_snapshot_evidence(
            record,
            allowed_integration_statuses=("not_ready", "invalidated"),
        )
        return
    record["status"] = "in_progress"
    record["phase"] = "developer"
    record["verification"] = {
        "status": "pending",
        "delivery_commit": None,
        "delivery_hash": None,
        "patch_hash": None,
        "snapshot_id": None,
        "changed_paths": [],
    }
    record["developer"] = {
        "evidence_contract_version": None,
        "agent_id": None,
        "snapshot_id": None,
        "scopes": [],
        "commands": [],
        "claims": [],
        "handoff": None,
    }
    record["review"] = {
        "evidence_contract_version": None,
        "agent_id": None,
        "snapshot_id": None,
        "status": "pending",
        "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
        "requirement_checklist": [],
        "accepted_findings": [],
        "claim_assessments": [],
        "summary": None,
    }
    record["human_approvals"] = []
    record["integration"] = {
        "status": "not_ready",
        "mode": None,
        "policy_id": None,
        "source_ref": None,
        "target_ref": None,
        "target_parent": None,
        "pr_head_commit": None,
        "result_commit": None,
        "merge_strategy": None,
        "queue_id": None,
        "queued_at": None,
        "queue_priority": None,
        "closeout_commit": None,
        "closeout_state_fingerprint": None,
        "pr_url": None,
        "ci_checks": [],
        "evidence": [],
    }
    record["process_retrospective"] = {
        "completed": False,
        "completed_by": None,
        "completed_at": None,
        "questions": {
            "repeated_problem_found": False,
            "guidance_gap_found": False,
            "deterministic_check_candidate_found": False,
        },
        "summary": None,
    }
    record["rule_proposals"] = []
    record["remaining_risks"] = []


def refresh_base(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    """Record a manually completed local-lane rebase and invalidate old evidence."""

    with _coordinator_lock(paths, "refresh-base", apply=args.apply):
        registry_path, payload = _registry(paths, args.lane_id)
        if payload.get("mode") != "local_worktree":
            raise LaneError("refresh-base only supports local_worktree lanes.")
        lane_paths, record = _load_and_require_v4_lane_record(payload)
        if _effective_status(payload) == "stale":
            raise LaneError("refresh-base refuses a stale lane; recover --takeover first.")
        if current_branch(lane_paths) != payload.get("branch"):
            raise LaneError("refresh-base must run against the lane's checked-out branch.")
        _, dirty = _dirty_digest(lane_paths)
        if dirty:
            raise LaneError("refresh-base requires a clean lane worktree after the manual rebase.")
        expected_generation = record.get("generation") if args.expected_generation is None else args.expected_generation
        if not is_json_integer(expected_generation, minimum=0):
            raise LaneError("Task record generation is invalid.")
        lane = record.get("lane") or {}
        for field in ("lane_id", "claim_id", "owner_generation", "branch", "base_commit"):
            if lane.get(field) != payload.get(field):
                raise LaneError(f"Task record lane.{field} does not match the runtime lane.")
        _require_v4_refreshable(record)
        if (record.get("integration") or {}).get("status") == "integrated":
            raise LaneError("refresh-base cannot rewrite an integrated task.")
        new_base = rev_parse(lane_paths, args.base)
        old_base = payload.get("base_commit")
        if not isinstance(old_base, str):
            raise LaneError("Runtime lane base commit is invalid.")
        if new_base == old_base:
            raise LaneError("refresh-base requires a newer base commit.")
        if not is_ancestor(lane_paths, old_base, new_base):
            raise LaneError("refresh-base refuses a base that does not advance the current lane base.")
        branch_tip = rev_parse(lane_paths, "HEAD")
        if not is_ancestor(lane_paths, new_base, branch_tip):
            raise LaneError("Manually rebase the lane onto --base before refresh-base.")
        merge_commits = git(lane_paths, "rev-list", "--merges", f"{new_base}..{branch_tip}").stdout.splitlines()
        if merge_commits:
            raise LaneError("refresh-base requires a rebase or cherry-pick result without merge commits.")
        integration = record.get("integration") or {}
        if integration.get("mode") == "local_bootstrap" and integration.get("status") in {"pending", "queued"}:
            target_ref = integration.get("target_ref")
            if not isinstance(target_ref, str) or rev_parse(lane_paths, target_ref) != new_base:
                raise LaneError("refresh-base must use the exact prepared local integration target.")
        queue_path, queue_payload = _queued_entry_for_refresh(paths, payload, record)
        if not args.apply:
            print(
                json.dumps(
                    {
                        "apply": False,
                        "lane_id": payload["lane_id"],
                        "old_base": old_base,
                        "new_base": new_base,
                        "invalidates": ["developer", "review", "verification", "approval", "integration", "queue"],
                        "would_remove_queue": str(queue_path) if queue_path else None,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return

        def mutation(current: dict[str, Any]) -> None:
            current_lane = current.get("lane") or {}
            if (
                current_lane.get("claim_id") != payload.get("claim_id")
                or current_lane.get("owner_generation") != payload.get("owner_generation")
                or current_lane.get("base_commit") != old_base
                or current.get("base_commit") != old_base
            ):
                raise StateError("Task record lane ownership changed during base refresh.")
            current_integration = current.get("integration") or {}
            for field in ("status", "queue_id", "queued_at", "queue_priority"):
                if current_integration.get(field) != integration.get(field):
                    raise StateError("Task integration changed during base refresh.")
            _reset_after_base_refresh(current, args.base, new_base)

        mutate_record(
            lane_paths,
            payload["record"],
            expected_generation,
            True,
            mutation,
            allowed_versions=(3, 4),
        )
        fault_injection("refresh-base-after-record")
        payload["base_ref"] = args.base
        payload["base_commit"] = new_base
        payload["state"] = "claimed"
        payload["heartbeat_at"] = utc_now()
        payload["expires_at"] = _future(LEASE_SECONDS)
        _write_runtime_claims(paths, payload, lane_paths)
        atomic_write_json(registry_path, payload)
        if queue_path is not None and queue_payload is not None:
            queue_path.unlink()
    print(f"LANE_BASE_REFRESHED lane={args.lane_id} base={new_base}")


def recover_lane(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    registry_path, payload = _registry(paths, args.lane_id)
    lane_paths, queued_record = _load_and_require_v4_lane_record(payload)
    if not args.takeover:
        print(json.dumps({"lane_id": args.lane_id, "effective_status": _effective_status(payload), "next": "use --takeover --apply only after stale ownership is confirmed"}, ensure_ascii=False, sort_keys=True))
        return
    expires = _parse_time(payload.get("expires_at"))
    if expires is not None and expires >= datetime.now(timezone.utc):
        raise LaneError("Takeover is refused while the current heartbeat lease is live.")
    previous_generation = payload.get("owner_generation")
    if not is_json_integer(previous_generation, minimum=1):
        raise LaneError("Lane owner generation is invalid.")
    new_owner = _owner_id(paths, args.owner_id, persist=args.apply)
    if not args.apply:
        print(json.dumps({"apply": False, "lane_id": args.lane_id, "owner_generation": previous_generation + 1, "owner_id": new_owner}, sort_keys=True))
        return
    with _coordinator_lock(paths, "recover-lane", apply=args.apply):
        _, current = _registry(paths, args.lane_id)
        if current.get("owner_generation") != previous_generation or current.get("claim_id") != payload.get("claim_id"):
            raise LaneError("Lane changed during takeover CAS.")
        current["owner_generation"] = previous_generation + 1
        current["owner_id"] = new_owner
        current["heartbeat_at"] = utc_now()
        current["expires_at"] = _future(LEASE_SECONDS)
        current["state"] = "claimed"
        lane_paths = WorkflowPaths.discover(Path(current["worktree"]))
        _, queued_record = load_record(lane_paths, current["record"])
        _require_v4_record_for_lane(lane_paths, queued_record)
        queued_integration = queued_record.get("integration") or {}
        queue_path: Path | None = None
        updated_queue: dict[str, Any] | None = None
        if queued_integration.get("status") == "queued":
            queue_id = queued_integration.get("queue_id")
            if not isinstance(queue_id, str) or not queue_id:
                raise LaneError("Queued task record has no queue ID.")
            queue_path = paths.shared_runtime / "queue" / f"{queue_id}.json"
            try:
                queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LaneError(f"Queued lane has no readable queue entry: {exc}") from exc
            expected_queue = {
                "queue_id": queue_id,
                "lane_id": current["lane_id"],
                "task_id": current["task_id"],
                "claim_id": current["claim_id"],
                "owner_generation": previous_generation,
            }
            if not isinstance(queue_payload, dict) or any(
                queue_payload.get(field) != expected
                for field, expected in expected_queue.items()
            ):
                raise LaneError("Queued lane entry does not match the stale claim token/generation.")
            updated_queue = dict(queue_payload)
            updated_queue["owner_generation"] = previous_generation + 1

        def mutation(record: dict[str, Any]) -> None:
            lane = record.get("lane") or {}
            if lane.get("claim_id") != current.get("claim_id") or lane.get("owner_generation") != previous_generation:
                raise StateError("Task record is not at the expected takeover generation.")
            integration = record.get("integration") or {}
            if queue_path is not None and integration.get("queue_id") != queued_integration.get("queue_id"):
                raise StateError("Task queue changed during stale takeover.")
            lane["owner_generation"] = previous_generation + 1
            lane["assignment"] = {
                "assigned_owner_id": new_owner,
                "assignment_generation": previous_generation + 1,
                "assigned_at": utc_now(),
                "assigned_by": "explicit-stale-takeover",
            }
            record["lane"] = lane

        mutate_record(
            lane_paths,
            current["record"],
            None,
            True,
            mutation,
            allowed_versions=(3, 4),
        )
        _write_runtime_claims(paths, current, lane_paths)
        atomic_write_json(registry_path, current)
        if queue_path is not None and updated_queue is not None:
            atomic_write_json(queue_path, updated_queue)
    print(f"LANE_TAKEN_OVER lane={args.lane_id} generation={current['owner_generation']}")


def release_lane(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    registry_path, payload = _registry(paths, args.lane_id)
    journal = paths.shared_runtime / "audit" / f"closeout-{payload.get('task_id')}.json"
    confirmed = False
    if journal.is_file():
        try:
            confirmed = json.loads(journal.read_text(encoding="utf-8")).get("confirmed") is True
        except (OSError, json.JSONDecodeError):
            confirmed = False
    worktree = Path(payload["worktree"])
    if worktree.is_dir() and not args.abandon:
        _load_and_require_v4_lane_record(payload)
    if not confirmed and not args.abandon:
        raise LaneError("Release requires confirmed closeout; use --abandon only for explicit non-integrated abandonment.")
    if worktree.is_dir():
        lane_paths = WorkflowPaths.discover(worktree)
        digest, dirty = _dirty_digest(lane_paths)
        if dirty and not args.abandon:
            raise LaneError(f"Release refuses a dirty lane worktree (diff token {digest}).")
    candidates = [
        registry_path,
        paths.shared_runtime / "claims" / f"{_safe_id(payload['task_id'])}.json",
    ]
    candidates.extend(
        paths.shared_runtime / "resources" / f"{resource_key_digest(key)}.json"
        for key in payload.get("resource_keys", [])
    )
    queue_candidates: list[Path] = []
    for queue_path in sorted((paths.shared_runtime / "queue").glob("*.json")):
        try:
            queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LaneError(f"Unreadable runtime queue entry: {queue_path}: {exc}") from exc
        if not isinstance(queue_payload, dict):
            raise LaneError(f"Invalid runtime queue entry: {queue_path}")
        identity = (queue_payload.get("lane_id"), queue_payload.get("task_id"), queue_payload.get("claim_id"))
        expected_identity = (payload.get("lane_id"), payload.get("task_id"), payload.get("claim_id"))
        if identity != expected_identity:
            continue
        if queue_payload.get("owner_generation") != payload.get("owner_generation"):
            raise LaneError(f"Queue generation mismatch prevents release: {queue_path}")
        queue_candidates.append(queue_path)
    candidates.extend(queue_candidates)
    if not args.apply:
        print(json.dumps({"apply": False, "lane_id": args.lane_id, "confirmed": confirmed, "would_remove_runtime": [str(item) for item in candidates], "worktree_preserved": str(worktree), "branch_preserved": payload.get("branch")}, ensure_ascii=False, sort_keys=True))
        return
    with _coordinator_lock(paths, "release-lane", apply=args.apply):
        for candidate in candidates:
            if not candidate.is_file():
                continue
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if data.get("claim_id") != payload.get("claim_id") or data.get("owner_generation") != payload.get("owner_generation"):
                raise LaneError(f"Token/generation mismatch prevents release: {candidate}")
        for candidate in candidates:
            if candidate.is_file():
                candidate.unlink()
        if worktree.is_dir():
            lane_paths = WorkflowPaths.discover(worktree)
            pointer = lane_paths.lane_runtime / "lane.json"
            if pointer.is_file():
                pointer.unlink()
    print(f"LANE_RELEASED lane={args.lane_id}; branch and worktree preserved")


def preassign(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    owner = str(uuid.UUID(args.owner_id))
    _, record = load_record(paths, args.record)
    _require_v4_record_for_lane(paths, record)
    task_id = str(record.get("task_id"))
    base_commit = rev_parse(paths, args.base)
    claim_id = str(uuid.uuid4())
    branch = f"codex/task/{_safe_id(task_id)}-{claim_id[:8]}"
    lane_id = f"lane-{_safe_id(task_id)}-{claim_id[:8]}"

    def mutation(item: dict[str, Any]) -> None:
        allowed, resources = _record_scope(item)
        item["lane"] = {
            "lane_id": lane_id, "mode": "remote_preassigned", "branch": branch,
            "base_ref": args.base, "base_commit": base_commit, "claim_id": claim_id,
            "owner_generation": 1,
            "assignment": {"assigned_owner_id": owner, "assignment_generation": 1, "assigned_at": utc_now(), "assigned_by": "coordinator-preassign"},
            "allowed_paths": allowed, "resource_keys": resources,
            "dependency_snapshot": {"backlog_commit": base_commit, "dependencies": []},
        }
        item["base_commit"] = base_commit
        item["status"] = "authorized"
        item["phase"] = "coordinator"

    mutate_record(
        paths,
        args.record,
        None,
        args.apply,
        mutation,
        allowed_versions=(3, 4),
    )
    print(f"REMOTE_PREASSIGNED branch={branch} owner_id={owner}; commit and push explicitly")


def resume_remote(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    record_path, record = load_record(paths, args.record)
    _require_v4_record_for_lane(paths, record)
    lane = record.get("lane") or {}
    mode = lane.get("mode")
    if mode not in {"remote_preassigned", "remote_claimed"}:
        raise LaneError("resume-remote requires a remote_preassigned or remote_claimed task record.")
    task_id = record.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise LaneError("Remote task record has no task ID.")
    for field in ("lane_id", "claim_id", "branch"):
        if not isinstance(lane.get(field), str) or not lane[field]:
            raise LaneError(f"Remote task record lane.{field} is invalid.")
    generation = lane.get("owner_generation")
    if not is_json_integer(generation, minimum=1):
        raise LaneError("Remote task record lane.owner_generation is invalid.")
    assignment = lane.get("assignment")
    if not isinstance(assignment, dict):
        raise LaneError("Remote task record lane.assignment is invalid.")
    owner_id = _owner_id(paths, args.owner_id, persist=args.apply)
    if assignment.get("assigned_owner_id") != owner_id:
        raise LaneError("Remote assignment belongs to a different owner ID.")
    if assignment.get("assignment_generation") != generation:
        raise LaneError("Remote assignment generation does not match the task lane.")
    branch = current_branch(paths)
    if branch != lane["branch"]:
        raise LaneError(
            f"Current branch {branch!r} does not match remote assignment branch {lane['branch']!r}."
        )

    if mode == "remote_claimed":
        remote, config = _remote_config(paths, args.remote)
        _require_remote_claimed_config(config)
        identity = _remote_claim_identity(record)
        claim_ref = _remote_claim_ref(identity["task_id"])
        _, claim = _read_remote_claim(paths, remote, claim_ref)
        _validate_remote_claim_binding(identity, claim)
        _require_live_remote_claim(claim, action="Remote claimed resume")
        task_oid = _remote_ref_oid(paths, remote, identity["task_ref"])
        if task_oid != rev_parse(paths, "HEAD"):
            raise LaneError("Current branch is not the exact remote task ref required for remote claimed resume.")

    pointer = _remote_lane_pointer(paths, record_path, record, owner_id)
    pointer_path = paths.lane_runtime / "lane.json"
    with AdvisoryLock(paths.lane_runtime / "resume-remote.lock", timeout=2):
        if pointer_path.exists():
            if not pointer_path.is_file():
                raise LaneError("Current worktree lane pointer path is not a file.")
            try:
                existing = json.loads(pointer_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LaneError(f"Current worktree lane pointer is unreadable: {exc}") from exc
            if existing != pointer:
                raise LaneError("Current worktree already belongs to a different lane pointer.")
            print(f"REMOTE_LANE_RESUMED lane={lane['lane_id']} branch={lane['branch']} existing=true")
            return
        if not args.apply:
            print(json.dumps({"apply": False, "pointer": pointer}, ensure_ascii=False, sort_keys=True))
            return
        atomic_write_json(pointer_path, pointer)
    print(f"REMOTE_LANE_RESUMED lane={lane['lane_id']} branch={lane['branch']}")


def _remote_lane_pointer(
    paths: WorkflowPaths, record_path: Path, record: dict[str, Any], owner_id: str
) -> dict[str, Any]:
    lane = record.get("lane") or {}
    task_id = record.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise LaneError("Remote task record has no task ID.")
    for field in ("lane_id", "claim_id", "branch"):
        if not isinstance(lane.get(field), str) or not lane[field]:
            raise LaneError(f"Remote task record lane.{field} is invalid.")
    generation = lane.get("owner_generation")
    if not is_json_integer(generation, minimum=1):
        raise LaneError("Remote task record lane.owner_generation is invalid.")
    return {
        "task_id": task_id,
        "lane_id": lane["lane_id"],
        "claim_id": lane["claim_id"],
        "owner_id": owner_id,
        "owner_generation": generation,
        "branch": lane["branch"],
        "record": paths.relative(record_path),
    }


def _install_remote_pointer(
    paths: WorkflowPaths, pointer: dict[str, Any], *, allow_generation_replacement: bool
) -> None:
    pointer_path = paths.lane_runtime / "lane.json"
    with AdvisoryLock(paths.lane_runtime / "resume-remote.lock", timeout=2):
        if pointer_path.exists():
            if not pointer_path.is_file():
                raise LaneError("Current worktree lane pointer path is not a file.")
            try:
                existing = json.loads(pointer_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LaneError(f"Current worktree lane pointer is unreadable: {exc}") from exc
            if existing == pointer:
                return
            immutable = ("task_id", "lane_id", "claim_id", "branch", "record")
            if (
                not allow_generation_replacement
                or any(existing.get(field) != pointer.get(field) for field in immutable)
                or not isinstance(existing.get("owner_generation"), int)
                or existing["owner_generation"] >= pointer["owner_generation"]
            ):
                raise LaneError("Current worktree already belongs to a different lane pointer.")
        atomic_write_json(pointer_path, pointer)


def _remove_remote_pointer_if_matches(paths: WorkflowPaths, pointer: dict[str, Any]) -> None:
    pointer_path = paths.lane_runtime / "lane.json"
    with AdvisoryLock(paths.lane_runtime / "resume-remote.lock", timeout=2):
        if not pointer_path.is_file():
            return
        try:
            existing = json.loads(pointer_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LaneError(f"Current worktree lane pointer is unreadable: {exc}") from exc
        if existing == pointer:
            pointer_path.unlink()


def _remote_config(paths: WorkflowPaths, supplied: str | None) -> tuple[str, dict[str, Any]]:
    config = paths.layout.get("remote") or {}
    remote = supplied or config.get("remote_name") or "origin"
    return str(remote), config


def _require_remote_claimed_config(config: dict[str, Any]) -> None:
    if config.get("mode") != "remote_claimed" or config.get("atomic_claims") is not True:
        raise LaneError("remote_claimed with atomic_claims=true is not enabled in layout.json.")


def _remote_claim_identity(record: dict[str, Any]) -> dict[str, Any]:
    lane = record.get("lane") or {}
    if lane.get("mode") != "remote_claimed":
        raise LaneError("Task record lane mode must be remote_claimed.")
    task_id = record.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise LaneError("Remote task record has no task ID.")
    claim_id = lane.get("claim_id")
    if not isinstance(claim_id, str) or not claim_id:
        raise LaneError("Remote task record lane.claim_id is invalid.")
    try:
        uuid.UUID(claim_id)
    except ValueError as exc:
        raise LaneError("Remote task record lane.claim_id must be a UUID.") from exc
    branch = lane.get("branch")
    if not isinstance(branch, str) or not branch.startswith("codex/task/"):
        raise LaneError("Remote task record lane.branch must use the codex/task namespace.")
    lane_id = lane.get("lane_id")
    if not isinstance(lane_id, str) or not lane_id:
        raise LaneError("Remote task record lane.lane_id is invalid.")
    generation = lane.get("owner_generation")
    if not is_json_integer(generation, minimum=1):
        raise LaneError("Remote task record lane.owner_generation is invalid.")
    assignment = lane.get("assignment")
    if not isinstance(assignment, dict):
        raise LaneError("Remote task record lane.assignment is invalid.")
    owner_id = assignment.get("assigned_owner_id")
    if not isinstance(owner_id, str) or not owner_id:
        raise LaneError("Remote task record assignment has no owner ID.")
    _remote_owner_uuid(owner_id, label="Remote task assignment owner ID")
    if assignment.get("assignment_generation") != generation:
        raise LaneError("Remote task assignment generation does not match the lane generation.")
    _, resource_keys = _record_scope(record)
    resource_refs = [
        f"refs/heads/codex/resources/{resource_key_digest(item)}"
        for item in resource_keys
    ]
    return {
        "task_id": task_id,
        "lane_id": lane_id,
        "claim_id": claim_id,
        "owner_generation": generation,
        "owner_id": owner_id,
        "branch": branch,
        "task_ref": f"refs/heads/{branch}",
        "resource_keys": resource_keys,
        "resource_refs": resource_refs,
    }


def _remote_claim_ref(task_id: str) -> str:
    return f"refs/heads/codex/claims/{_safe_id(task_id)}"


def _claim_payload(
    paths: WorkflowPaths,
    record: dict[str, Any],
    lease_revision: int,
    lease_seconds: int,
    *,
    transfer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(lease_revision, int) or isinstance(lease_revision, bool) or lease_revision < 1:
        raise LaneError("Remote claim lease revision must be a positive integer.")
    if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 1:
        raise LaneError("Remote claim lease duration must be a positive integer.")
    identity = _remote_claim_identity(record)
    payload = {
        "schema_version": 1,
        "task_id": identity["task_id"],
        "claim_id": identity["claim_id"],
        "owner_generation": identity["owner_generation"],
        "lease_revision": lease_revision,
        "owner_id": identity["owner_id"],
        "task_ref": identity["task_ref"],
        "resource_keys": identity["resource_keys"],
        "resource_refs": identity["resource_refs"],
        "heartbeat_at": utc_now(),
        "expires_at": _future(lease_seconds),
        "state": "active",
    }
    if transfer is not None:
        payload["transfer"] = transfer
    validate_workflow_schema(
        paths,
        "remote-claim-v1.schema.json",
        payload,
        label="Remote claim payload",
    )
    return payload


def _commit_tree(paths: WorkflowPaths, tree: str, message: str, parent: str | None = None) -> str:
    command = ["git", "-C", str(paths.root), "commit-tree", tree, "-m", message]
    if parent:
        command.extend(["-p", parent])
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "Codex Workflow")
    env.setdefault("GIT_AUTHOR_EMAIL", "workflow@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise LaneError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def _claim_commit(paths: WorkflowPaths, payload: dict[str, Any], parent: str | None = None) -> str:
    # Keep this writer boundary defensive even if a future caller bypasses
    # _claim_payload.
    validate_workflow_schema(
        paths,
        "remote-claim-v1.schema.json",
        payload,
        label="Remote claim payload",
    )
    blob = subprocess.run(
        ["git", "-C", str(paths.root), "hash-object", "-w", "--stdin"],
        input=(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        capture_output=True,
        check=True,
    ).stdout.decode().strip()
    tree_input = f"100644 blob {blob}\tclaim.json\n".encode("ascii")
    tree = subprocess.run(
        ["git", "-C", str(paths.root), "mktree"],
        input=tree_input,
        capture_output=True,
        check=True,
    ).stdout.decode("ascii").strip()
    return _commit_tree(
        paths,
        tree,
        f"workflow claim {payload['task_id']} lease {payload['lease_revision']}",
        parent,
    )


def _task_record_commit(
    paths: WorkflowPaths,
    parent: str,
    relative: str,
    record: dict[str, Any],
    *,
    message: str,
) -> str:
    validate_workflow_schema(
        paths,
        task_record_schema_name(record),
        record,
        label="Remote transfer task record",
    )
    entry = git(paths, "ls-tree", parent, "--", relative).stdout.strip()
    fields = entry.split(None, 2)
    if len(fields) != 3 or fields[1] != "blob" or not re.fullmatch(r"100[0-7]{3}", fields[0]):
        raise LaneError("Remote transfer requires the current task record to be a tracked regular file.")
    mode = fields[0]
    source = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    blob = subprocess.run(
        ["git", "-C", str(paths.root), "hash-object", "-w", "--stdin"],
        input=source,
        capture_output=True,
        check=True,
    ).stdout.decode("ascii").strip()
    with tempfile.TemporaryDirectory(prefix="codex-workflow-transfer-") as directory:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(Path(directory) / "index")

        def indexed_git(*arguments: str, input_data: bytes | None = None) -> str:
            result = subprocess.run(
                ["git", "-C", str(paths.root), *arguments],
                input=input_data,
                capture_output=True,
                env=env,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
                raise LaneError(detail)
            return result.stdout.decode("utf-8", errors="replace").strip()

        indexed_git("read-tree", parent)
        # Use raw bytes so Windows cannot translate the record terminator to
        # CRLF and accidentally create a second path ending in a hidden '\r'.
        indexed_git(
            "update-index",
            "--add",
            "--index-info",
            input_data=f"{mode} {blob}\t{relative}\n".encode("utf-8"),
        )
        tree = indexed_git("write-tree")
    return _commit_tree(paths, tree, message, parent)


def _remote_ref_oid(paths: WorkflowPaths, remote: str, ref: str) -> str | None:
    result = git(paths, "ls-remote", "--refs", remote, ref, check=False)
    if result.returncode != 0:
        raise LaneError((result.stderr or result.stdout).strip())
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) != 1:
        raise LaneError(f"Remote ref lookup was ambiguous: {ref}")
    return lines[0].split()[0]


def _read_remote_claim(paths: WorkflowPaths, remote: str, claim_ref: str) -> tuple[str, dict[str, Any]]:
    oid = _remote_ref_oid(paths, remote, claim_ref)
    if oid is None:
        raise LaneError(f"Remote claim does not exist: {claim_ref}")
    git(paths, "fetch", "--no-tags", remote, f"{claim_ref}:{claim_ref}")
    raw = git(paths, "show", f"{oid}:claim.json").stdout
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LaneError(f"Remote claim payload is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise LaneError("Remote claim payload must be an object.")
    validate_workflow_schema(
        paths,
        "remote-claim-v1.schema.json",
        payload,
        label="Remote claim payload",
    )
    return oid, payload


def _validate_remote_claim_binding(identity: dict[str, Any], payload: dict[str, Any]) -> None:
    for field in ("task_id", "claim_id", "owner_generation", "owner_id", "task_ref"):
        if payload.get(field) != identity[field]:
            raise LaneError(f"Remote claim ownership mismatch: {field}.")
    for field in ("resource_keys", "resource_refs"):
        if payload.get(field) != identity[field]:
            raise LaneError(f"Remote claim resource binding mismatch: {field}.")
    revision = payload.get("lease_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise LaneError("Remote claim lease revision is invalid.")
    if not isinstance(payload.get("state"), str):
        raise LaneError("Remote claim state is invalid.")


def _remote_claim_refs(payload: dict[str, Any], claim_ref: str) -> list[str]:
    resource_refs = payload.get("resource_refs")
    if not isinstance(resource_refs, list) or not all(isinstance(item, str) and item for item in resource_refs):
        raise LaneError("Remote claim resource refs are invalid.")
    refs = [claim_ref, *resource_refs]
    if len(refs) != len(set(refs)):
        raise LaneError("Remote claim refs are duplicated.")
    return refs


def _assert_remote_refs_at(
    paths: WorkflowPaths, remote: str, refs: list[str], expected_oid: str
) -> None:
    for ref in refs:
        if _remote_ref_oid(paths, remote, ref) != expected_oid:
            raise LaneError(f"Remote claim split-brain detected at {ref}; freeze this lane.")


def _remote_claim_expiry(payload: dict[str, Any]) -> datetime:
    expires = _parse_time(payload.get("expires_at"))
    if expires is None:
        raise LaneError("Remote claim expiry is invalid.")
    return expires


def _require_live_remote_claim(payload: dict[str, Any], *, action: str) -> None:
    if payload.get("state") != "active":
        raise LaneError(f"{action} requires an active remote claim.")
    if _remote_claim_expiry(payload) <= datetime.now(timezone.utc):
        raise LaneError(f"{action} refuses an expired remote lease; freeze this lane and use remote-takeover.")


def _require_expired_remote_claim(payload: dict[str, Any]) -> None:
    if payload.get("state") != "active":
        raise LaneError("Remote takeover requires an active claim whose lease has expired.")
    if _remote_claim_expiry(payload) > datetime.now(timezone.utc):
        raise LaneError("Remote takeover is refused while the current remote lease is live.")


def remote_claim(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    remote, config = _remote_config(paths, args.remote)
    _require_remote_claimed_config(config)
    record_path, record = load_record(paths, args.record)
    _require_v4_record_for_lane(paths, record)
    identity = _remote_claim_identity(record)
    owner_id = _owner_id(paths, identity["owner_id"], persist=args.apply)
    if owner_id != identity["owner_id"]:
        raise LaneError("Current runtime owner ID does not match the remote task assignment.")
    if current_branch(paths) != identity["branch"]:
        raise LaneError("Current HEAD must be the committed assigned task branch.")
    payload = _claim_payload(paths, record, 1, args.lease_seconds)
    claim_ref = _remote_claim_ref(identity["task_id"])
    refs = [payload["task_ref"], claim_ref, *payload["resource_refs"]]
    existing = [ref for ref in refs if _remote_ref_oid(paths, remote, ref)]
    if existing:
        raise LaneError("Remote task/claim/resource ref already exists: " + ", ".join(existing))
    if not args.apply:
        print(
            json.dumps(
                {
                    "apply": False,
                    "remote": remote,
                    "payload": payload,
                    "task_ref": payload["task_ref"],
                    "claim_ref": claim_ref,
                    "resource_refs": payload["resource_refs"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    commit = _claim_commit(paths, payload)
    refspecs = [
        f"{rev_parse(paths, 'HEAD')}:{payload['task_ref']}",
        f"{commit}:{claim_ref}",
        *[f"{commit}:{ref}" for ref in payload["resource_refs"]],
    ]
    result = git(paths, "push", "--atomic", remote, *refspecs, check=False)
    if result.returncode != 0:
        raise LaneError("Atomic remote claim failed with no valid lease: " + (result.stderr or result.stdout).strip())
    _install_remote_pointer(
        paths,
        _remote_lane_pointer(paths, record_path, record, owner_id),
        allow_generation_replacement=False,
    )
    print(f"REMOTE_CLAIMED task={record.get('task_id')} claim={payload['claim_id']}")


def remote_heartbeat(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    remote, config = _remote_config(paths, args.remote)
    _require_remote_claimed_config(config)
    _, record = load_record(paths, args.record)
    _require_v4_record_for_lane(paths, record)
    identity = _remote_claim_identity(record)
    owner_id = _owner_id(paths, identity["owner_id"], persist=args.apply)
    if owner_id != identity["owner_id"]:
        raise LaneError("Current runtime owner ID does not match the remote task assignment.")
    claim_ref = _remote_claim_ref(identity["task_id"])
    old_oid, old = _read_remote_claim(paths, remote, claim_ref)
    _validate_remote_claim_binding(identity, old)
    _require_live_remote_claim(old, action="Remote heartbeat")
    payload = _claim_payload(paths, record, old["lease_revision"] + 1, args.lease_seconds)
    refs = _remote_claim_refs(old, claim_ref)
    _assert_remote_refs_at(paths, remote, refs, old_oid)
    if not args.apply:
        print(
            json.dumps(
                {"apply": False, "old_oid": old_oid, "payload": payload},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    commit = _claim_commit(paths, payload, old_oid)
    leases = [f"--force-with-lease={ref}:{old_oid}" for ref in refs]
    refspecs = [f"{commit}:{ref}" for ref in refs]
    result = git(paths, "push", "--atomic", *leases, remote, *refspecs, check=False)
    if result.returncode != 0:
        raise LaneError("Remote heartbeat CAS failed; freeze this lane: " + (result.stderr or result.stdout).strip())
    print(f"REMOTE_HEARTBEAT_OK revision={payload['lease_revision']}")


def _transferred_record(
    record: dict[str, Any],
    *,
    next_owner_id: str,
    transfer_kind: str,
    approval: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    version = record.get("version")
    generation = record.get("generation")
    if version not in {3, 4} or not isinstance(generation, int) or isinstance(generation, bool):
        raise LaneError("Remote transfer requires a V3 or V4 task record with an integer generation.")
    if (record.get("integration") or {}).get("status") == "integrated":
        raise LaneError("Remote ownership transfer refuses an integrated task; use remote-release.")
    identity = _remote_claim_identity(record)
    updated = copy.deepcopy(record)
    lane = updated["lane"]
    transferred_at = utc_now()
    lane["owner_generation"] = identity["owner_generation"] + 1
    assignment = {
        "assigned_owner_id": next_owner_id,
        "assignment_generation": lane["owner_generation"],
        "assigned_at": transferred_at,
        "assigned_by": f"remote-{transfer_kind}",
    }
    if approval is not None:
        assignment["takeover_approval"] = approval
    lane["assignment"] = assignment
    updated["lane"] = lane
    updated["generation"] = record["generation"] + 1
    transfer = {
        "kind": transfer_kind,
        "from_owner_id": identity["owner_id"],
        "to_owner_id": next_owner_id,
        "transferred_at": transferred_at,
        "task_record_generation": updated["generation"],
    }
    if approval is not None:
        transfer["approval"] = approval
    return updated, transfer


def _remote_transfer(
    paths: WorkflowPaths, args: argparse.Namespace, *, transfer_kind: str
) -> None:
    remote, config = _remote_config(paths, args.remote)
    _require_remote_claimed_config(config)
    record_path, record = load_record(paths, args.record)
    _require_v4_record_for_lane(paths, record)
    identity = _remote_claim_identity(record)
    current_owner_id = _owner_id(paths, args.owner_id, persist=args.apply)
    if transfer_kind == "handoff":
        if current_owner_id != identity["owner_id"]:
            raise LaneError("Remote handoff owner does not match the current task assignment.")
        next_owner_id = _remote_owner_uuid(args.to_owner_id, label="Remote handoff target owner ID")
        if next_owner_id == current_owner_id:
            raise LaneError("Remote handoff target owner must differ from the current owner.")
        approval = None
    else:
        next_owner_id = current_owner_id
        if next_owner_id == identity["owner_id"]:
            raise LaneError("Remote takeover owner must differ from the expired owner.")
        approved_by = args.approved_by.strip()
        approval_ref = args.approval_ref.strip()
        if not approved_by or not approval_ref:
            raise LaneError("Remote takeover requires non-empty human approval evidence.")
        approval = {
            "approved_by": approved_by,
            "approval_ref": approval_ref,
            "approved_at": utc_now(),
        }
    if current_branch(paths) != identity["branch"]:
        raise LaneError("Remote transfer requires the assigned task branch to be checked out.")
    digest, dirty = _dirty_digest(paths)
    if dirty:
        raise LaneError(f"Remote transfer requires a clean task worktree; dirty diff token is {digest}.")
    local_head = rev_parse(paths, "HEAD")
    claim_ref = _remote_claim_ref(identity["task_id"])
    old_oid, old = _read_remote_claim(paths, remote, claim_ref)
    _validate_remote_claim_binding(identity, old)
    if transfer_kind == "handoff":
        _require_live_remote_claim(old, action="Remote handoff")
    else:
        _require_expired_remote_claim(old)
    remote_task_oid = _remote_ref_oid(paths, remote, identity["task_ref"])
    if remote_task_oid is None or remote_task_oid != local_head:
        raise LaneError("Remote transfer requires the current branch to equal the exact remote task ref.")
    refs = _remote_claim_refs(old, claim_ref)
    _assert_remote_refs_at(paths, remote, refs, old_oid)
    old_pointer = _remote_lane_pointer(paths, record_path, record, identity["owner_id"])
    updated, transfer = _transferred_record(
        record,
        next_owner_id=next_owner_id,
        transfer_kind=transfer_kind,
        approval=approval,
    )
    validate_workflow_schema(
        paths,
        task_record_schema_name(updated),
        updated,
        label="Remote transfer task record",
    )
    payload = _claim_payload(
        paths,
        updated,
        old["lease_revision"] + 1,
        args.lease_seconds,
        transfer=transfer,
    )
    if not args.apply:
        print(
            json.dumps(
                {
                    "apply": False,
                    "remote": remote,
                    "task_ref": identity["task_ref"],
                    "expected_task_oid": remote_task_oid,
                    "expected_claim_oid": old_oid,
                    "next_owner_id": next_owner_id,
                    "next_owner_generation": payload["owner_generation"],
                    "next_lease_revision": payload["lease_revision"],
                    "transfer": transfer,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    task_commit = _task_record_commit(
        paths,
        local_head,
        paths.relative(record_path),
        updated,
        message=f"workflow: {transfer_kind} {identity['task_id']}",
    )
    claim_commit = _claim_commit(paths, payload, old_oid)
    leases = [
        f"--force-with-lease={identity['task_ref']}:{remote_task_oid}",
        *[f"--force-with-lease={ref}:{old_oid}" for ref in refs],
    ]
    refspecs = [
        f"{task_commit}:{identity['task_ref']}",
        f"{claim_commit}:{claim_ref}",
        *[f"{claim_commit}:{ref}" for ref in payload["resource_refs"]],
    ]
    result = git(paths, "push", "--atomic", *leases, remote, *refspecs, check=False)
    if result.returncode != 0:
        raise LaneError("Remote transfer CAS failed; freeze this lane: " + (result.stderr or result.stdout).strip())
    advanced = git(paths, "merge", "--ff-only", task_commit, check=False)
    if advanced.returncode != 0:
        raise LaneError(
            "Remote transfer succeeded, but the local task branch did not fast-forward; fetch the task ref before continuing: "
            + (advanced.stderr or advanced.stdout).strip()
        )
    if transfer_kind == "handoff":
        _remove_remote_pointer_if_matches(paths, old_pointer)
        print(f"REMOTE_HANDED_OFF task={identity['task_id']} generation={payload['owner_generation']}")
    else:
        _install_remote_pointer(
            paths,
            _remote_lane_pointer(paths, record_path, updated, next_owner_id),
            allow_generation_replacement=True,
        )
        print(f"REMOTE_TAKEN_OVER task={identity['task_id']} generation={payload['owner_generation']}")


def remote_takeover(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    _remote_transfer(paths, args, transfer_kind="stale_takeover")


def remote_handoff(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    _remote_transfer(paths, args, transfer_kind="handoff")


def _remote_target_branch_ref(remote: str, target_ref: str) -> str:
    prefix = f"refs/remotes/{remote}/"
    if not target_ref.startswith(prefix) or target_ref == prefix:
        raise LaneError(
            f"Remote release requires integration.target_ref under {prefix}."
        )
    return "refs/heads/" + target_ref[len(prefix):]


def _remote_closeout_proof(
    paths: WorkflowPaths,
    remote: str,
    record_path: Path,
    record: dict[str, Any],
) -> dict[str, str]:
    integration = record.get("integration") or {}
    target_ref = integration.get("target_ref")
    if not isinstance(target_ref, str) or not target_ref:
        raise LaneError("Remote release requires a tracked integration target ref.")
    remote_target_ref = _remote_target_branch_ref(remote, target_ref)
    remote_target_oid = _remote_ref_oid(paths, remote, remote_target_ref)
    if remote_target_oid is None:
        raise LaneError(f"Remote target ref does not exist: {remote_target_ref}")
    try:
        local_target_oid = rev_parse(paths, target_ref)
    except WorkflowDataError as exc:
        raise LaneError(
            f"Remote target ref is not available locally; fetch {remote} before release."
        ) from exc
    if local_target_oid != remote_target_oid:
        raise LaneError(
            f"Remote target ref advanced to {remote_target_oid}; fetch {remote} before release."
        )

    record_relative = paths.relative(record_path)
    backlog_relative = paths.relative(paths.tracked("backlog"))
    try:
        target_record_raw = git(paths, "show", f"{target_ref}:{record_relative}").stdout
        target_backlog = git(paths, "show", f"{target_ref}:{backlog_relative}").stdout
        target_record = json.loads(target_record_raw)
    except (WorkflowDataError, json.JSONDecodeError) as exc:
        raise LaneError(
            "Remote target does not contain a readable integrated task record and Backlog closeout."
        ) from exc
    if not isinstance(target_record, dict):
        raise LaneError("Remote target task record root must be an object.")
    validate_workflow_schema(
        paths,
        task_record_schema_name(target_record),
        target_record,
        label="Remote target task record",
    )
    if (target_record.get("integration") or {}).get("status") != "integrated":
        raise LaneError("Remote target task record is not integrated.")

    current_identity = _remote_claim_identity(record)
    target_identity = _remote_claim_identity(target_record)
    for field in (
        "task_id",
        "lane_id",
        "claim_id",
        "owner_generation",
        "owner_id",
        "branch",
        "resource_keys",
        "resource_refs",
    ):
        if target_identity[field] != current_identity[field]:
            raise LaneError(f"Remote target task ownership differs from the release record: {field}.")

    target_integration = target_record["integration"]
    expected_fingerprint = target_integration.get("closeout_state_fingerprint")
    actual_fingerprint = closeout_state_fingerprint(target_record, target_backlog)
    if not isinstance(expected_fingerprint, str) or expected_fingerprint != actual_fingerprint:
        raise LaneError("Remote target closeout state fingerprint is missing or stale.")
    result_commit = target_integration.get("result_commit")
    if not isinstance(result_commit, str) or not is_ancestor(
        paths, result_commit, remote_target_oid
    ):
        raise LaneError("Remote target does not contain the exact integrated result commit.")
    return {
        "target_ref": target_ref,
        "remote_target_ref": remote_target_ref,
        "target_oid": remote_target_oid,
        "closeout_state_fingerprint": actual_fingerprint,
    }


def remote_release(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    remote, config = _remote_config(paths, args.remote)
    _require_remote_claimed_config(config)
    record_path, record = load_record(paths, args.record)
    _require_v4_record_for_lane(paths, record)
    if (record.get("integration") or {}).get("status") != "integrated":
        raise LaneError("Remote claim release requires integrated tracked state.")
    closeout = _remote_closeout_proof(paths, remote, record_path, record)
    identity = _remote_claim_identity(record)
    claim_ref = _remote_claim_ref(identity["task_id"])
    old_oid, payload = _read_remote_claim(paths, remote, claim_ref)
    expected_oid = args.expected_claim_oid
    if expected_oid is not None:
        if not re.fullmatch(r"[0-9a-f]{40,64}", expected_oid):
            raise LaneError("Remote release expected claim OID is invalid.")
        if expected_oid != old_oid:
            raise LaneError(
                f"Remote release expected claim OID {expected_oid}, but the current claim is {old_oid}; no refs were deleted."
            )
    elif args.apply:
        raise LaneError(
            "Remote release --apply requires --expected-claim-oid from a fresh dry-run."
        )
    _validate_remote_claim_binding(identity, payload)
    refs = _remote_claim_refs(payload, claim_ref)
    _assert_remote_refs_at(paths, remote, refs, old_oid)
    leases = [f"--force-with-lease={ref}:{old_oid}" for ref in refs]
    deletes = [f":{ref}" for ref in refs]
    if not args.apply:
        print(
            json.dumps(
                {
                    "apply": False,
                    "remote": remote,
                    "expected_oid": old_oid,
                    "delete_refs": refs,
                    **closeout,
                },
                sort_keys=True,
            )
        )
        return
    result = git(paths, "push", "--atomic", *leases, remote, *deletes, check=False)
    if result.returncode != 0:
        raise LaneError("Remote release CAS failed; claims were not safely released: " + (result.stderr or result.stdout).strip())
    remaining = [ref for ref in refs if _remote_ref_oid(paths, remote, ref) is not None]
    if remaining:
        raise LaneError(
            "Atomic remote release returned success but refs remain; freeze cleanup and inspect: "
            + ", ".join(remaining)
        )

    local_ref_removed = git(
        paths, "update-ref", "-d", claim_ref, old_oid, check=False
    ).returncode == 0
    _remove_remote_pointer_if_matches(
        paths,
        _remote_lane_pointer(paths, record_path, record, identity["owner_id"]),
    )
    audit = {
        "task_id": identity["task_id"],
        "claim_id": identity["claim_id"],
        "owner_generation": identity["owner_generation"],
        "expected_claim_oid": old_oid,
        "released_refs": refs,
        "remote": remote,
        **closeout,
        "released_at": utc_now(),
        "local_claim_ref_removed": local_ref_removed,
    }
    atomic_write_json(
        paths.shared_runtime
        / "audit"
        / f"remote-release-{_safe_id(identity['task_id'])}.json",
        audit,
    )
    print(
        f"REMOTE_RELEASED task={record.get('task_id')} expected_oid={old_oid} "
        f"target={closeout['target_oid']}"
    )


def main() -> None:
    args = parser().parse_args()
    try:
        paths = WorkflowPaths.discover(Path.cwd())
        if args.command == "claim":
            claim(paths, args)
        elif args.command == "adopt":
            adopt(paths, args)
        elif args.command == "list":
            list_lanes(paths, args)
        elif args.command == "heartbeat":
            heartbeat(paths, args)
        elif args.command == "lock-status":
            lock_status(paths, args)
        elif args.command == "lock-acquire":
            lock_acquire(paths, args)
        elif args.command == "lock-heartbeat":
            lock_heartbeat(paths, args)
        elif args.command == "lock-release":
            lock_release(paths, args)
        elif args.command == "lock-takeover":
            lock_takeover(paths, args)
        elif args.command == "expand-resources":
            expand_resources(paths, args)
        elif args.command == "queue":
            queue_lane(paths, args)
        elif args.command == "refresh-base":
            refresh_base(paths, args)
        elif args.command == "recover":
            recover_lane(paths, args)
        elif args.command == "rebuild":
            rebuild(paths, args)
        elif args.command == "release":
            release_lane(paths, args)
        elif args.command == "preassign":
            preassign(paths, args)
        elif args.command == "resume-remote":
            resume_remote(paths, args)
        elif args.command == "remote-claim":
            remote_claim(paths, args)
        elif args.command == "remote-heartbeat":
            remote_heartbeat(paths, args)
        elif args.command == "remote-takeover":
            remote_takeover(paths, args)
        elif args.command == "remote-handoff":
            remote_handoff(paths, args)
        elif args.command == "remote-release":
            remote_release(paths, args)
    except (
        LaneError, PersistentRoleLockError, WorkflowDataError, WorkflowJSONResourceError,
        WorkflowPathError, StateError,
        LockUnavailable, OSError, subprocess.CalledProcessError, ValueError,
    ) as exc:
        print(f"[workflow-lane] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
