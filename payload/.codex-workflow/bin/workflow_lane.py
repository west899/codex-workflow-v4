#!/usr/bin/env python3
"""Local worktree lanes and optional Git-ref remote claims for Workflow V3."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from workflow_common import (
    WorkflowDataError,
    current_branch,
    git,
    load_record,
    rev_parse,
    utc_now,
)
from workflow_lock import (
    AdvisoryLock,
    LockUnavailable,
    canonical_resource_key,
    resource_key_digest,
)
from workflow_paths import WorkflowPathError, WorkflowPaths, atomic_write_json
from workflow_state import StateError, mutate_record


LEASE_SECONDS = 900


class LaneError(ValueError):
    pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Manage Codex Workflow V3 lanes.")
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

    expand = sub.add_parser("expand-resources")
    expand.add_argument("lane_id")
    expand.add_argument("--add", action="append", required=True)
    expand.add_argument("--expected-generation", type=int)
    expand.add_argument("--apply", action="store_true")

    queue = sub.add_parser("queue")
    queue.add_argument("lane_id")
    queue.add_argument("--priority", type=int, default=100)
    queue.add_argument("--apply", action="store_true")

    recover = sub.add_parser("recover")
    recover.add_argument("lane_id")
    recover.add_argument("--takeover", action="store_true")
    recover.add_argument("--owner-id")
    recover.add_argument("--apply", action="store_true")

    release = sub.add_parser("release")
    release.add_argument("lane_id")
    release.add_argument("--abandon", action="store_true")
    release.add_argument("--apply", action="store_true")

    preassign = sub.add_parser("preassign")
    preassign.add_argument("record")
    preassign.add_argument("--owner-id", required=True)
    preassign.add_argument("--base", default="main")
    preassign.add_argument("--apply", action="store_true")

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

    remote_release = sub.add_parser("remote-release")
    remote_release.add_argument("record")
    remote_release.add_argument("--remote")
    remote_release.add_argument("--apply", action="store_true")
    return result


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    if not normalized:
        raise LaneError("Task/lane identifier has no safe Git component.")
    return normalized


def _owner_id(paths: WorkflowPaths, supplied: str | None) -> str:
    paths.ensure_runtime()
    identity_path = paths.shared_runtime / "owner-id"
    if supplied:
        try:
            owner = str(uuid.UUID(supplied))
        except ValueError as exc:
            raise LaneError("Owner ID must be a random UUID, not a username or hostname.") from exc
        if identity_path.is_file() and identity_path.read_text(encoding="utf-8").strip() != owner:
            raise LaneError("Supplied owner ID differs from this runtime's controlled owner ID.")
        if not identity_path.is_file():
            identity_path.write_text(owner + "\n", encoding="utf-8")
        return owner
    if identity_path.is_file():
        try:
            return str(uuid.UUID(identity_path.read_text(encoding="utf-8").strip()))
        except ValueError as exc:
            raise LaneError("Runtime owner-id is invalid; repair it explicitly.") from exc
    owner = str(uuid.uuid4())
    identity_path.write_text(owner + "\n", encoding="utf-8")
    return owner


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
    if not isinstance(maximum, int) or maximum < 1:
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

    mutate_record(paths, record_relative, None, True, mutation)


def claim(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    if paths.layout.get("parallel", {}).get("mode") != "local_worktree":
        raise LaneError("local_worktree mode is not enabled in layout.json.")
    record_relative = args.record or _default_record(paths, args.task_id)
    _, record = load_record(paths, record_relative)
    if record.get("task_id") != args.task_id:
        raise LaneError("Task record does not match requested task ID.")
    allowed, resources = _record_scope(record)
    owner_id = _owner_id(paths, args.owner_id)
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
    paths.ensure_runtime()
    with AdvisoryLock(paths.shared_runtime / "locks" / "claims-global.lock", timeout=2):
        _assert_claims_available(paths, args.task_id, resources, branch, worktree)
        if worktree.exists():
            raise LaneError(f"Requested worktree path already exists: {worktree}")
        if not args.apply:
            print(json.dumps({"apply": False, **payload}, ensure_ascii=False, sort_keys=True))
            return
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
    allowed, resources = _record_scope(record)
    owner_id = _owner_id(paths, args.owner_id)
    suffix = uuid.uuid4().hex[:8]
    lane_id = f"lane-{_safe_id(args.task_id)}-{suffix}"
    payload = _lane_payload(
        task_id=args.task_id, lane_id=lane_id, claim_id=str(uuid.uuid4()), owner_id=owner_id,
        branch=branch, base_ref=str(record.get("lane", {}).get("base_ref") or "main"),
        base_commit=str(record.get("base_commit")), worktree=paths.root,
        record=record_relative, allowed_paths=allowed, resources=resources,
        mode="local_worktree",
    )
    paths.ensure_runtime()
    with AdvisoryLock(paths.shared_runtime / "locks" / "claims-global.lock", timeout=2):
        _assert_claims_available(paths, args.task_id, resources, branch, paths.root)
        if not args.apply:
            print(json.dumps({"apply": False, "dirty_diff_sha256": digest, **payload}, ensure_ascii=False, sort_keys=True))
            return
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
    worktree = Path(str(payload.get("worktree", "")))
    record_relative = payload.get("record")
    record = None
    if worktree.is_dir() and isinstance(record_relative, str):
        try:
            lane_paths = WorkflowPaths.discover(worktree)
            _, record = load_record(lane_paths, record_relative)
        except Exception:
            record = None
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
    expires = _parse_time(payload.get("expires_at"))
    if expires is None or expires < datetime.now(timezone.utc):
        return "stale"
    return "claimed"


def list_lanes(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    paths.ensure_runtime()
    rows = []
    for registry_path in sorted((paths.shared_runtime / "registry" / "lanes").glob("*.json")):
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rows.append({"lane_id": registry_path.stem, "effective_status": "broken", "error": str(exc)})
            continue
        if not isinstance(payload, dict):
            rows.append({"lane_id": registry_path.stem, "effective_status": "broken"})
            continue
        payload = dict(payload)
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
    worktree = Path(payload["worktree"])
    lane_paths = WorkflowPaths.discover(worktree)
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
    with AdvisoryLock(paths.shared_runtime / "locks" / "claims-global.lock", timeout=2):
        for _, existing in _existing_resource_claims(paths):
            if existing.get("lane_id") == args.lane_id:
                continue
            for requested in additions:
                if _resource_conflict(str(existing.get("resource_key")), requested):
                    raise LaneError(f"Resource conflict with lane {existing.get('lane_id')}: {requested}")
        if not args.apply:
            print(json.dumps({"apply": False, "lane_id": args.lane_id, "resource_keys": new_resources}, ensure_ascii=False, sort_keys=True))
            return
        lane_paths = WorkflowPaths.discover(Path(payload["worktree"]))

        def mutation(record: dict[str, Any]) -> None:
            lane = record.get("lane") or {}
            if lane.get("claim_id") != payload.get("claim_id") or lane.get("owner_generation") != payload.get("owner_generation"):
                raise StateError("Task record lane token/generation mismatch.")
            lane["resource_keys"] = new_resources
            record["lane"] = lane
            record["scope"]["resource_keys"] = new_resources

        mutate_record(lane_paths, payload["record"], args.expected_generation, True, mutation)
        payload["resource_keys"] = new_resources
        atomic_write_json(registry_path, payload)
        atomic_write_json(paths.shared_runtime / "claims" / f"{_safe_id(payload['task_id'])}.json", payload)
        for resource in new_resources:
            resource_payload = dict(payload)
            resource_payload["resource_key"] = resource
            atomic_write_json(paths.shared_runtime / "resources" / f"{resource_key_digest(resource)}.json", resource_payload)
    print(f"RESOURCES_EXPANDED lane={args.lane_id} count={len(new_resources)}")


def queue_lane(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    registry_path, payload = _registry(paths, args.lane_id)
    if _effective_status(payload) != "verified":
        raise LaneError("Only a verified lane can enter the integration queue.")
    queue_id = str(uuid.uuid4())
    queue_payload = {
        "queue_id": queue_id,
        "lane_id": args.lane_id,
        "task_id": payload["task_id"],
        "claim_id": payload["claim_id"],
        "owner_generation": payload["owner_generation"],
        "queue_priority": args.priority,
        "queued_at": utc_now(),
    }
    if not args.apply:
        print(json.dumps({"apply": False, **queue_payload}, ensure_ascii=False, sort_keys=True))
        return
    lane_paths = WorkflowPaths.discover(Path(payload["worktree"]))

    def mutation(record: dict[str, Any]) -> None:
        integration = record.get("integration") or {}
        if integration.get("status") != "pending":
            raise StateError("prepare-integration must run before queue.")
        integration.update(
            {"status": "queued", "queue_id": queue_id, "queued_at": queue_payload["queued_at"], "queue_priority": args.priority}
        )
        record["integration"] = integration

    mutate_record(lane_paths, payload["record"], None, True, mutation)
    payload["state"] = "queued"
    atomic_write_json(registry_path, payload)
    atomic_write_json(paths.shared_runtime / "queue" / f"{queue_id}.json", queue_payload)
    print(f"LANE_QUEUED lane={args.lane_id} queue={queue_id}")


def recover_lane(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    registry_path, payload = _registry(paths, args.lane_id)
    if not args.takeover:
        print(json.dumps({"lane_id": args.lane_id, "effective_status": _effective_status(payload), "next": "use --takeover --apply only after stale ownership is confirmed"}, ensure_ascii=False, sort_keys=True))
        return
    expires = _parse_time(payload.get("expires_at"))
    if expires is not None and expires >= datetime.now(timezone.utc):
        raise LaneError("Takeover is refused while the current heartbeat lease is live.")
    new_owner = _owner_id(paths, args.owner_id)
    previous_generation = payload.get("owner_generation")
    if not isinstance(previous_generation, int):
        raise LaneError("Lane owner generation is invalid.")
    if not args.apply:
        print(json.dumps({"apply": False, "lane_id": args.lane_id, "owner_generation": previous_generation + 1, "owner_id": new_owner}, sort_keys=True))
        return
    with AdvisoryLock(paths.shared_runtime / "locks" / "claims-global.lock", timeout=2):
        _, current = _registry(paths, args.lane_id)
        if current.get("owner_generation") != previous_generation or current.get("claim_id") != payload.get("claim_id"):
            raise LaneError("Lane changed during takeover CAS.")
        current["owner_generation"] = previous_generation + 1
        current["owner_id"] = new_owner
        current["heartbeat_at"] = utc_now()
        current["expires_at"] = _future(LEASE_SECONDS)
        current["state"] = "claimed"
        lane_paths = WorkflowPaths.discover(Path(current["worktree"]))

        def mutation(record: dict[str, Any]) -> None:
            lane = record.get("lane") or {}
            if lane.get("claim_id") != current.get("claim_id") or lane.get("owner_generation") != previous_generation:
                raise StateError("Task record is not at the expected takeover generation.")
            lane["owner_generation"] = previous_generation + 1
            lane["assignment"] = {
                "assigned_owner_id": new_owner,
                "assignment_generation": previous_generation + 1,
                "assigned_at": utc_now(),
                "assigned_by": "explicit-stale-takeover",
            }
            record["lane"] = lane

        mutate_record(lane_paths, current["record"], None, True, mutation)
        _write_runtime_claims(paths, current, lane_paths)
        atomic_write_json(registry_path, current)
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
    if not confirmed and not args.abandon:
        raise LaneError("Release requires confirmed closeout; use --abandon only for explicit non-integrated abandonment.")
    worktree = Path(payload["worktree"])
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
    if not args.apply:
        print(json.dumps({"apply": False, "lane_id": args.lane_id, "confirmed": confirmed, "would_remove_runtime": [str(item) for item in candidates], "worktree_preserved": str(worktree), "branch_preserved": payload.get("branch")}, ensure_ascii=False, sort_keys=True))
        return
    with AdvisoryLock(paths.shared_runtime / "locks" / "claims-global.lock", timeout=2):
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
        item["status"] = "authorized"
        item["phase"] = "coordinator"

    mutate_record(paths, args.record, None, args.apply, mutation)
    print(f"REMOTE_PREASSIGNED branch={branch} owner_id={owner}; commit and push explicitly")


def _remote_config(paths: WorkflowPaths, supplied: str | None) -> tuple[str, dict[str, Any]]:
    config = paths.layout.get("remote") or {}
    remote = supplied or config.get("remote_name") or "origin"
    return str(remote), config


def _claim_payload(record: dict[str, Any], lease_revision: int, lease_seconds: int) -> dict[str, Any]:
    lane = record.get("lane") or {}
    assignment = lane.get("assignment") or {}
    resource_keys = [canonical_resource_key(item) for item in lane.get("resource_keys", [])]
    task_ref = f"refs/heads/{lane.get('branch')}"
    return {
        "schema_version": 1,
        "task_id": record.get("task_id"),
        "claim_id": lane.get("claim_id"),
        "owner_generation": lane.get("owner_generation"),
        "lease_revision": lease_revision,
        "owner_id": assignment.get("assigned_owner_id"),
        "task_ref": task_ref,
        "resource_keys": resource_keys,
        "resource_refs": [f"refs/heads/codex/resources/{resource_key_digest(item)}" for item in resource_keys],
        "heartbeat_at": utc_now(),
        "expires_at": _future(lease_seconds),
        "state": "active",
    }


def _claim_commit(paths: WorkflowPaths, payload: dict[str, Any], parent: str | None = None) -> str:
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
    command = ["git", "-C", str(paths.root), "commit-tree", tree, "-m", f"workflow claim {payload['task_id']} lease {payload['lease_revision']}"]
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


def remote_claim(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    remote, config = _remote_config(paths, args.remote)
    if config.get("mode") != "remote_claimed" or config.get("atomic_claims") is not True:
        raise LaneError("remote_claimed with atomic_claims=true is not enabled in layout.json.")
    _, record = load_record(paths, args.record)
    lane = record.get("lane") or {}
    if lane.get("mode") != "remote_claimed":
        raise LaneError("Task record lane mode must be remote_claimed.")
    if rev_parse(paths, "HEAD") != rev_parse(paths, f"refs/heads/{lane.get('branch')}"):
        raise LaneError("Current HEAD must be the committed assigned task branch.")
    payload = _claim_payload(record, 1, args.lease_seconds)
    claim_ref = f"refs/heads/codex/claims/{_safe_id(str(record.get('task_id')))}"
    refs = [claim_ref, *payload["resource_refs"]]
    existing = [ref for ref in refs if _remote_ref_oid(paths, remote, ref)]
    if existing:
        raise LaneError("Remote claim/resource ref already exists: " + ", ".join(existing))
    commit = _claim_commit(paths, payload)
    refspecs = [f"{rev_parse(paths, 'HEAD')}:{payload['task_ref']}", f"{commit}:{claim_ref}"] + [f"{commit}:{ref}" for ref in payload["resource_refs"]]
    if not args.apply:
        print(json.dumps({"apply": False, "remote": remote, "claim_commit": commit, "payload": payload, "refspecs": refspecs}, ensure_ascii=False, sort_keys=True))
        return
    result = git(paths, "push", "--atomic", remote, *refspecs, check=False)
    if result.returncode != 0:
        raise LaneError("Atomic remote claim failed with no valid lease: " + (result.stderr or result.stdout).strip())
    print(f"REMOTE_CLAIMED task={record.get('task_id')} claim={payload['claim_id']}")


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
    return oid, payload


def remote_heartbeat(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    remote, config = _remote_config(paths, args.remote)
    if config.get("mode") != "remote_claimed" or config.get("atomic_claims") is not True:
        raise LaneError("remote_claimed atomic mode is not enabled.")
    _, record = load_record(paths, args.record)
    claim_ref = f"refs/heads/codex/claims/{_safe_id(str(record.get('task_id')))}"
    old_oid, old = _read_remote_claim(paths, remote, claim_ref)
    lane = record.get("lane") or {}
    assignment = lane.get("assignment") or {}
    for field, expected in (
        ("claim_id", lane.get("claim_id")),
        ("owner_generation", lane.get("owner_generation")),
        ("owner_id", assignment.get("assigned_owner_id")),
    ):
        if old.get(field) != expected:
            raise LaneError(f"Remote heartbeat ownership mismatch: {field}.")
    payload = _claim_payload(record, int(old.get("lease_revision", 0)) + 1, args.lease_seconds)
    commit = _claim_commit(paths, payload, old_oid)
    refs = [claim_ref, *payload["resource_refs"]]
    for ref in refs:
        if _remote_ref_oid(paths, remote, ref) != old_oid:
            raise LaneError(f"Remote claim split-brain detected at {ref}; freeze this lane.")
    leases = [f"--force-with-lease={ref}:{old_oid}" for ref in refs]
    refspecs = [f"{commit}:{ref}" for ref in refs]
    if not args.apply:
        print(json.dumps({"apply": False, "old_oid": old_oid, "new_oid": commit, "payload": payload}, ensure_ascii=False, sort_keys=True))
        return
    result = git(paths, "push", "--atomic", *leases, remote, *refspecs, check=False)
    if result.returncode != 0:
        raise LaneError("Remote heartbeat CAS failed; freeze this lane: " + (result.stderr or result.stdout).strip())
    print(f"REMOTE_HEARTBEAT_OK revision={payload['lease_revision']}")


def remote_release(paths: WorkflowPaths, args: argparse.Namespace) -> None:
    remote, config = _remote_config(paths, args.remote)
    if config.get("mode") != "remote_claimed" or config.get("atomic_claims") is not True:
        raise LaneError("remote_claimed atomic mode is not enabled.")
    _, record = load_record(paths, args.record)
    if (record.get("integration") or {}).get("status") != "integrated":
        raise LaneError("Remote claim release requires integrated tracked state.")
    claim_ref = f"refs/heads/codex/claims/{_safe_id(str(record.get('task_id')))}"
    old_oid, payload = _read_remote_claim(paths, remote, claim_ref)
    lane = record.get("lane") or {}
    if payload.get("claim_id") != lane.get("claim_id") or payload.get("owner_generation") != lane.get("owner_generation"):
        raise LaneError("Remote release ownership mismatch.")
    refs = [claim_ref, *payload.get("resource_refs", [])]
    for ref in refs:
        if _remote_ref_oid(paths, remote, ref) != old_oid:
            raise LaneError(f"Remote claim split-brain detected at {ref}.")
    leases = [f"--force-with-lease={ref}:{old_oid}" for ref in refs]
    deletes = [f":{ref}" for ref in refs]
    if not args.apply:
        print(json.dumps({"apply": False, "remote": remote, "expected_oid": old_oid, "delete_refs": refs}, sort_keys=True))
        return
    result = git(paths, "push", "--atomic", *leases, remote, *deletes, check=False)
    if result.returncode != 0:
        raise LaneError("Remote release CAS failed; claims were not safely released: " + (result.stderr or result.stdout).strip())
    print(f"REMOTE_RELEASED task={record.get('task_id')}")


def main() -> None:
    args = parser().parse_args()
    try:
        paths = WorkflowPaths.discover(Path.cwd())
        paths.ensure_runtime()
        if args.command == "claim":
            claim(paths, args)
        elif args.command == "adopt":
            adopt(paths, args)
        elif args.command == "list":
            list_lanes(paths, args)
        elif args.command == "heartbeat":
            heartbeat(paths, args)
        elif args.command == "expand-resources":
            expand_resources(paths, args)
        elif args.command == "queue":
            queue_lane(paths, args)
        elif args.command == "recover":
            recover_lane(paths, args)
        elif args.command == "release":
            release_lane(paths, args)
        elif args.command == "preassign":
            preassign(paths, args)
        elif args.command == "remote-claim":
            remote_claim(paths, args)
        elif args.command == "remote-heartbeat":
            remote_heartbeat(paths, args)
        elif args.command == "remote-release":
            remote_release(paths, args)
    except (
        LaneError, WorkflowDataError, WorkflowPathError, StateError,
        LockUnavailable, OSError, subprocess.CalledProcessError, ValueError,
    ) as exc:
        print(f"[workflow-lane] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
