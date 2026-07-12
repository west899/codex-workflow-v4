#!/usr/bin/env python3
"""Short-lived cross-platform advisory locks for Codex Workflow V3."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, IO

from workflow_paths import atomic_write_json


class LockUnavailable(RuntimeError):
    """The advisory lock could not be acquired before its deadline."""


class PersistentRoleLockError(RuntimeError):
    """A durable Coordinator or Integrator lease is invalid or unavailable."""


ROLE_LOCKS = frozenset({"coordinator", "integrator"})
DEFAULT_ROLE_LEASE_SECONDS = 900
_SESSION_ID = str(uuid.uuid4())


class AdvisoryLock:
    """Exclusive OS advisory lock held by an open file handle.

    The OS releases the lock if the process exits, including an abrupt crash.  The
    file itself is persistent and is not treated as proof of lock ownership.
    """

    def __init__(self, path: Path, *, timeout: float = 0.0, poll: float = 0.05):
        self.path = path
        self.timeout = max(0.0, timeout)
        self.poll = max(0.01, poll)
        self._stream: IO[bytes] | None = None

    def acquire(self) -> "AdvisoryLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._stream = stream
                return self
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    stream.close()
                    raise LockUnavailable(f"Workflow lock is already held: {self.path}")
                time.sleep(self.poll)

    def release(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            self._stream = None

    def __enter__(self) -> "AdvisoryLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


def canonical_resource_key(value: str) -> str:
    """Normalize a cooperative resource key identically across platforms."""

    if not isinstance(value, str):
        raise ValueError("Resource key must be text.")
    normalized = unicodedata.normalize("NFC", value.strip().replace("\\", "/"))
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    normalized = normalized.rstrip("/").casefold()
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        raise ValueError(f"Unsafe resource key: {value!r}")
    return normalized


def resource_key_digest(value: str) -> str:
    return hashlib.sha256(canonical_resource_key(value).encode("utf-8")).hexdigest()


def lock_probe(path: Path) -> None:
    """Use a second process to prove mutual exclusion and crash-safe release."""

    helper = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from pathlib import Path; "
        "from workflow_lock import AdvisoryLock, LockUnavailable; "
        "lock=AdvisoryLock(Path(sys.argv[2]), timeout=0); "
        "\ntry:\n lock.acquire()\nexcept LockUnavailable:\n raise SystemExit(3)\n"
        "else:\n lock.release()\n raise SystemExit(0)\n"
    )
    module_root = str(Path(__file__).resolve().parent)
    with AdvisoryLock(path, timeout=0):
        blocked = subprocess.run(
            [sys.executable, "-B", "-c", helper, module_root, str(path)],
            capture_output=True,
            text=True,
        )
        if blocked.returncode != 3:
            raise LockUnavailable(
                "Filesystem advisory-lock probe failed; a second process acquired the held lock."
            )
    released = subprocess.run(
        [sys.executable, "-B", "-c", helper, module_root, str(path)],
        capture_output=True,
        text=True,
    )
    if released.returncode != 0:
        raise LockUnavailable(
            "Filesystem advisory-lock probe failed; the OS did not release the lock after handle close."
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _future(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")


def _require_role(role: str) -> str:
    if role not in ROLE_LOCKS:
        raise PersistentRoleLockError(
            f"Persistent role lock must be one of: {', '.join(sorted(ROLE_LOCKS))}."
        )
    return role


def _require_uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise PersistentRoleLockError(f"{label} must be a UUID.")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise PersistentRoleLockError(f"{label} must be a UUID.") from exc


def role_lock_path(runtime: Path, role: str) -> Path:
    role = _require_role(role)
    return runtime / "locks" / f"{role}.lock.json"


def role_lock_guard_path(runtime: Path, role: str) -> Path:
    role = _require_role(role)
    return runtime / "locks" / f"{role}.guard"


def _validate_role_lock(payload: Any, role: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PersistentRoleLockError(f"Persistent {role} lock must be a JSON object.")
    if payload.get("schema_version") != 1 or payload.get("role") != role:
        raise PersistentRoleLockError(f"Persistent {role} lock schema or role is invalid.")
    if payload.get("state") not in {"active", "released"}:
        raise PersistentRoleLockError(f"Persistent {role} lock state is invalid.")
    generation = payload.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise PersistentRoleLockError(f"Persistent {role} lock generation is invalid.")
    for field in ("token", "owner_id", "session_id"):
        _require_uuid(payload.get(field), label=f"Persistent {role} lock {field}")
    pid = payload.get("owner_pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
        raise PersistentRoleLockError(f"Persistent {role} lock owner_pid is invalid.")
    for field in ("acquired_at", "heartbeat_at"):
        if _parse_utc(payload.get(field)) is None:
            raise PersistentRoleLockError(f"Persistent {role} lock {field} is invalid.")
    expires = payload.get("expires_at")
    if payload["state"] == "active" and _parse_utc(expires) is None:
        raise PersistentRoleLockError(f"Persistent {role} lock expires_at is invalid.")
    if payload["state"] == "released" and expires is not None:
        raise PersistentRoleLockError(f"Released {role} lock must have expires_at=null.")
    return dict(payload)


def read_role_lock(runtime: Path, role: str) -> dict[str, Any] | None:
    role = _require_role(role)
    path = role_lock_path(runtime, role)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PersistentRoleLockError(f"Persistent {role} lock is unreadable: {exc}") from exc
    return _validate_role_lock(payload, role)


def role_lock_status(runtime: Path, role: str) -> dict[str, Any]:
    role = _require_role(role)
    payload = read_role_lock(runtime, role)
    if payload is None:
        return {"role": role, "state": "unlocked", "effective_status": "unlocked", "generation": 0}
    status = payload["state"]
    if status == "active" and _parse_utc(payload["expires_at"]) < datetime.now(timezone.utc):
        status = "stale"
    return {**payload, "effective_status": status}


def validate_role_lock_access(
    runtime: Path,
    role: str,
    *,
    token: str | None = None,
    generation: int | None = None,
) -> dict[str, Any]:
    """Read-only admission check for automatic or token-attached role access."""

    if (token is None) != (generation is None):
        raise PersistentRoleLockError("Persistent role lock token and generation must be supplied together.")
    current = role_lock_status(runtime, role)
    status = current["effective_status"]
    if token is not None:
        token = _require_uuid(token, label="Persistent role lock token")
        if current.get("state") != "active":
            raise PersistentRoleLockError(f"Persistent {role} lock is not active for attachment.")
        if current.get("token") != token or current.get("generation") != generation:
            raise PersistentRoleLockError(
                f"Persistent {role} lock token/generation does not match the supplied lease."
            )
        if status == "stale":
            raise PersistentRoleLockError(
                f"Persistent {role} lock is stale; explicit takeover is required."
            )
        return current
    if status == "active":
        raise PersistentRoleLockError(
            f"Persistent {role} lock is held by token {current['token']} generation {current['generation']}."
        )
    if status == "stale":
        raise PersistentRoleLockError(
            f"Persistent {role} lock is stale; explicit takeover is required with "
            f"token {current['token']} generation {current['generation']}."
        )
    return current


@dataclass(frozen=True)
class RoleLockLease:
    role: str
    token: str
    generation: int
    owner_id: str
    owner_pid: int
    session_id: str
    expires_at: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RoleLockLease":
        return cls(
            role=str(payload["role"]),
            token=str(payload["token"]),
            generation=int(payload["generation"]),
            owner_id=str(payload["owner_id"]),
            owner_pid=int(payload["owner_pid"]),
            session_id=str(payload["session_id"]),
            expires_at=str(payload["expires_at"]),
        )

    def public(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "token": self.token,
            "generation": self.generation,
            "owner_id": self.owner_id,
            "owner_pid": self.owner_pid,
            "session_id": self.session_id,
            "expires_at": self.expires_at,
        }


class PersistentRoleLock:
    """Durable role lease plus an OS guard held for one critical command.

    The JSON lease remains after a crash, while the OS guard is released by the
    operating system. A later command must explicitly take over a stale lease;
    normal commands never delete or replace it implicitly.
    """

    def __init__(
        self,
        runtime: Path,
        role: str,
        *,
        action: str,
        lease_seconds: int = DEFAULT_ROLE_LEASE_SECONDS,
        token: str | None = None,
        generation: int | None = None,
        owner_id: str | None = None,
        release_on_exit: bool = True,
    ) -> None:
        self.runtime = runtime
        self.role = _require_role(role)
        if not isinstance(action, str) or not action.strip():
            raise PersistentRoleLockError("Persistent role lock action must be non-empty text.")
        self.action = action.strip()
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 1:
            raise PersistentRoleLockError("Persistent role lock lease_seconds must be a positive integer.")
        if (token is None) != (generation is None):
            raise PersistentRoleLockError("Persistent role lock token and generation must be supplied together.")
        self.lease_seconds = lease_seconds
        self.expected_token = _require_uuid(token, label="Persistent role lock token") if token else None
        if generation is not None and (
            not isinstance(generation, int) or isinstance(generation, bool) or generation < 1
        ):
            raise PersistentRoleLockError("Persistent role lock generation must be a positive integer.")
        self.expected_generation = generation
        self.owner_id = _require_uuid(owner_id, label="Persistent role lock owner_id") if owner_id else str(uuid.uuid4())
        self.release_on_exit = release_on_exit
        self.lease: RoleLockLease | None = None
        self._guard: AdvisoryLock | None = None

    def _acquire_guard(self) -> None:
        self.runtime.joinpath("locks").mkdir(parents=True, exist_ok=True)
        self._guard = AdvisoryLock(role_lock_guard_path(self.runtime, self.role), timeout=2)
        self._guard.acquire()

    def _new_payload(self, generation: int) -> dict[str, Any]:
        now = _utc_now()
        return {
            "schema_version": 1,
            "role": self.role,
            "state": "active",
            "token": str(uuid.uuid4()),
            "generation": generation,
            "owner_id": self.owner_id,
            "owner_pid": os.getpid(),
            "session_id": _SESSION_ID,
            "action": self.action,
            "acquired_at": now,
            "heartbeat_at": now,
            "expires_at": _future(self.lease_seconds),
        }

    def _write(self, payload: dict[str, Any]) -> None:
        atomic_write_json(role_lock_path(self.runtime, self.role), payload)

    def _require_current(self, *, allow_expired: bool) -> dict[str, Any]:
        if self.lease is None:
            raise PersistentRoleLockError("Persistent role lock was not acquired.")
        current = read_role_lock(self.runtime, self.role)
        if current is None or current.get("state") != "active":
            raise PersistentRoleLockError(f"Persistent {self.role} lock is no longer active.")
        if (
            current.get("token") != self.lease.token
            or current.get("generation") != self.lease.generation
        ):
            raise PersistentRoleLockError(
                f"Persistent {self.role} lock token/generation changed during the operation."
            )
        if not allow_expired and _parse_utc(current.get("expires_at")) < datetime.now(timezone.utc):
            raise PersistentRoleLockError(
                f"Persistent {self.role} lock is stale; heartbeat cannot revive an expired detached lease."
            )
        return current

    def acquire(self) -> "PersistentRoleLock":
        self._acquire_guard()
        try:
            current = read_role_lock(self.runtime, self.role)
            if self.expected_token is not None:
                if current is None or current.get("state") != "active":
                    raise PersistentRoleLockError(f"Persistent {self.role} lock is not active for attachment.")
                if (
                    current.get("token") != self.expected_token
                    or current.get("generation") != self.expected_generation
                ):
                    raise PersistentRoleLockError(
                        f"Persistent {self.role} lock token/generation does not match the supplied lease."
                    )
                if _parse_utc(current.get("expires_at")) < datetime.now(timezone.utc):
                    raise PersistentRoleLockError(
                        f"Persistent {self.role} lock is stale; explicit takeover is required."
                    )
                self.lease = RoleLockLease.from_payload(current)
                self.heartbeat()
                return self

            if current is not None and current.get("state") == "active":
                expires = _parse_utc(current.get("expires_at"))
                if expires < datetime.now(timezone.utc):
                    raise PersistentRoleLockError(
                        f"Persistent {self.role} lock is stale; explicit takeover is required with "
                        f"token {current['token']} generation {current['generation']}."
                    )
                raise PersistentRoleLockError(
                    f"Persistent {self.role} lock is held by token {current['token']} generation {current['generation']}."
                )
            generation = 1 if current is None else int(current["generation"]) + 1
            payload = self._new_payload(generation)
            self._write(payload)
            self.lease = RoleLockLease.from_payload(payload)
            return self
        except Exception:
            self._release_guard()
            raise

    def heartbeat(self) -> RoleLockLease:
        current = self._require_current(allow_expired=self.expected_token is None)
        current["heartbeat_at"] = _utc_now()
        current["expires_at"] = _future(self.lease_seconds)
        current["action"] = self.action
        self._write(current)
        self.lease = RoleLockLease.from_payload(current)
        return self.lease

    def release(self) -> dict[str, Any]:
        current = self._require_current(allow_expired=True)
        current["state"] = "released"
        current["heartbeat_at"] = _utc_now()
        current["expires_at"] = None
        current["released_at"] = _utc_now()
        current["released_by_pid"] = os.getpid()
        self._write(current)
        return current

    def detach(self) -> RoleLockLease:
        if self.lease is None:
            raise PersistentRoleLockError("Persistent role lock was not acquired.")
        self._release_guard()
        return self.lease

    def _release_guard(self) -> None:
        if self._guard is not None:
            self._guard.release()
            self._guard = None

    def __enter__(self) -> "PersistentRoleLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if self.release_on_exit and self.lease is not None:
                self.release()
        finally:
            self._release_guard()


def acquire_role_lock(
    runtime: Path,
    role: str,
    *,
    action: str,
    lease_seconds: int = DEFAULT_ROLE_LEASE_SECONDS,
    owner_id: str | None = None,
) -> RoleLockLease:
    lock = PersistentRoleLock(
        runtime,
        role,
        action=action,
        lease_seconds=lease_seconds,
        owner_id=owner_id,
        release_on_exit=False,
    )
    lock.acquire()
    return lock.detach()


def heartbeat_role_lock(
    runtime: Path,
    role: str,
    *,
    token: str,
    generation: int,
    lease_seconds: int = DEFAULT_ROLE_LEASE_SECONDS,
) -> RoleLockLease:
    with PersistentRoleLock(
        runtime,
        role,
        action="manual-heartbeat",
        lease_seconds=lease_seconds,
        token=token,
        generation=generation,
        release_on_exit=False,
    ) as lock:
        return lock.heartbeat()


def release_role_lock(runtime: Path, role: str, *, token: str, generation: int) -> dict[str, Any]:
    lock = PersistentRoleLock(
        runtime,
        role,
        action="manual-release",
        token=token,
        generation=generation,
        release_on_exit=False,
    )
    lock._acquire_guard()
    try:
        current = lock._require_current(allow_expired=True) if lock.lease else None
        if current is None:
            current = read_role_lock(runtime, role)
            if current is None or current.get("state") != "active":
                raise PersistentRoleLockError(f"Persistent {role} lock is not active.")
            if current.get("token") != lock.expected_token or current.get("generation") != generation:
                raise PersistentRoleLockError(
                    f"Persistent {role} lock token/generation does not match the supplied lease."
                )
            lock.lease = RoleLockLease.from_payload(current)
        return lock.release()
    finally:
        lock._release_guard()


def takeover_role_lock(
    runtime: Path,
    role: str,
    *,
    expected_token: str,
    expected_generation: int,
    approved_by: str,
    approval_ref: str,
    lease_seconds: int = DEFAULT_ROLE_LEASE_SECONDS,
    owner_id: str | None = None,
) -> RoleLockLease:
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise PersistentRoleLockError("Persistent role lock takeover requires approved_by.")
    if not isinstance(approval_ref, str) or not approval_ref.strip():
        raise PersistentRoleLockError("Persistent role lock takeover requires approval_ref.")
    lock = PersistentRoleLock(
        runtime,
        role,
        action="explicit-takeover",
        lease_seconds=lease_seconds,
        owner_id=owner_id,
        release_on_exit=False,
    )
    lock._acquire_guard()
    try:
        current = read_role_lock(runtime, role)
        expected_token = _require_uuid(expected_token, label="Persistent role lock expected_token")
        if current is None or current.get("state") != "active":
            raise PersistentRoleLockError(f"Persistent {role} lock is not active for takeover.")
        if (
            current.get("token") != expected_token
            or current.get("generation") != expected_generation
        ):
            raise PersistentRoleLockError(
                f"Persistent {role} lock changed before takeover; token/generation CAS failed."
            )
        if _parse_utc(current.get("expires_at")) >= datetime.now(timezone.utc):
            raise PersistentRoleLockError(
                f"Persistent {role} lock takeover is refused while the lease is live."
            )
        replacement = lock._new_payload(int(current["generation"]) + 1)
        lock._write(replacement)
        audit = {
            "kind": "persistent-role-lock-takeover",
            "role": role,
            "taken_over_at": _utc_now(),
            "approved_by": approved_by.strip(),
            "approval_ref": approval_ref.strip(),
            "previous": current,
            "replacement": replacement,
        }
        audit_path = runtime / "audit" / (
            f"lock-takeover-{role}-{replacement['generation']}-{replacement['token']}.json"
        )
        atomic_write_json(audit_path, audit)
        lock.lease = RoleLockLease.from_payload(replacement)
        return lock.detach()
    except Exception:
        lock._release_guard()
        raise
