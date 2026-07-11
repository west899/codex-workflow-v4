#!/usr/bin/env python3
"""Short-lived cross-platform advisory locks for Codex Workflow V3."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import IO


class LockUnavailable(RuntimeError):
    """The advisory lock could not be acquired before its deadline."""


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
