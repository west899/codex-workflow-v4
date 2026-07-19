#!/usr/bin/env python3
"""Resolve Codex Workflow V3 paths without assuming ``.git`` is a directory.

The resolver deliberately separates three containment domains:

* tracked files live below the current Git worktree root;
* shared runtime lives below ``git rev-parse --git-common-dir``;
* lane-private runtime lives below ``git rev-parse --git-dir``.

Every other V3 command imports this module instead of rebuilding paths itself.
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


LAYOUT_RELATIVE = PurePosixPath(".codex-workflow/layout.json")
RUNTIME_NAME = "codex-workflow-v3"
MAX_JSON_INTEGER_DIGITS = 640
MAX_JSON_NESTING = 256


class JSONResourceLimitError(ValueError):
    """A JSON value exceeded an explicit workflow resource boundary."""


class WorkflowPathError(ValueError):
    """A configured or discovered workflow path is unsafe or invalid."""


class WorkflowPathResourceError(WorkflowPathError):
    """A configured JSON path resource failed inside its parser."""


class WorkflowPathOSError(WorkflowPathError):
    """The operating system prevented a filesystem path from resolving."""


class WorkflowPathRuntimeError(WorkflowPathError):
    """The filesystem resolver rejected a path at runtime."""


class WorkflowPathValueError(WorkflowPathError):
    """A path value was invalid before the filesystem could resolve it."""


def bounded_json_integer(value: str) -> int:
    """Reject JSON integers whose decimal representation is a resource hazard."""

    digits = value.lstrip("-")
    if len(digits) > MAX_JSON_INTEGER_DIGITS:
        raise JSONResourceLimitError(
            f"JSON integer exceeds the {MAX_JSON_INTEGER_DIGITS}-digit resource limit"
        )
    return int(value)


def parse_bounded_json(text: str) -> Any:
    """Parse JSON with deterministic integer and nesting resource limits."""

    payload = json.loads(text, parse_int=bounded_json_integer)
    pending: list[tuple[Any, int]] = [(payload, 0)]
    while pending:
        value, depth = pending.pop()
        if not isinstance(value, (dict, list)):
            continue
        next_depth = depth + 1
        if next_depth > MAX_JSON_NESTING:
            raise JSONResourceLimitError(
                f"JSON nesting exceeds the {MAX_JSON_NESTING}-level resource limit"
            )
        children = value.values() if isinstance(value, dict) else value
        pending.extend((child, next_depth) for child in children)
    return payload


def resolve_path(path: Path, *, label: str, expand_user: bool = False) -> Path:
    """Resolve a path with version-independent symlink-loop classification."""

    if expand_user:
        try:
            path = path.expanduser()
        except RecursionError:
            raise
        except RuntimeError as exc:
            raise WorkflowPathRuntimeError(f"Unable to expand {label}: {exc}") from exc
        except OSError as exc:
            raise WorkflowPathOSError(f"Unable to expand {label}: {exc}") from exc
        except ValueError as exc:
            raise WorkflowPathValueError(f"Unable to expand {label}: {exc}") from exc

    try:
        return path.resolve(strict=True)
    except RecursionError:
        raise
    except RuntimeError as exc:
        # Python <= 3.12 reports a symlink loop as RuntimeError.
        raise WorkflowPathRuntimeError(f"Unable to resolve {label}: {exc}") from exc
    except OSError as exc:
        # Python >= 3.13 reports the same loop as OSError(ELOOP).
        if exc.errno == errno.ELOOP:
            raise WorkflowPathRuntimeError(f"Unable to resolve {label}: {exc}") from exc
        if not isinstance(exc, FileNotFoundError):
            raise WorkflowPathOSError(f"Unable to resolve {label}: {exc}") from exc
    except ValueError as exc:
        raise WorkflowPathValueError(f"Unable to resolve {label}: {exc}") from exc

    # Workflow paths may be planned before their final component exists. The
    # strict probe above detects loops consistently; this fallback preserves
    # the package's existing support for missing leaf paths.
    try:
        return path.resolve(strict=False)
    except RecursionError:
        raise
    except RuntimeError as exc:
        raise WorkflowPathRuntimeError(f"Unable to resolve {label}: {exc}") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise WorkflowPathRuntimeError(f"Unable to resolve {label}: {exc}") from exc
        raise WorkflowPathOSError(f"Unable to resolve {label}: {exc}") from exc
    except ValueError as exc:
        raise WorkflowPathValueError(f"Unable to resolve {label}: {exc}") from exc


def _run_git(start: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(start), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise WorkflowPathError(detail or f"Git command failed: {' '.join(arguments)}")
    return result.stdout.strip()


def _git_absolute_dir(start: Path, flag: str, root: Path | None = None) -> Path:
    """Read a Git directory, using an explicit fallback for older Git versions."""

    result = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--path-format=absolute", flag],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode == 0:
        return resolve_path(Path(result.stdout.strip()), label=f"Git {flag} path")

    raw = _run_git(start, "rev-parse", flag)
    candidate = Path(raw)
    if not candidate.is_absolute():
        # rev-parse directory output is relative to the command's -C directory.
        candidate = start / candidate
    resolved = resolve_path(candidate, label=f"Git {flag} path")
    if flag == "--show-toplevel" and root is not None and resolved != root:
        raise WorkflowPathError("Git root changed while resolving workflow paths.")
    return resolved


def _is_descendant(path: Path, parent: Path, *, allow_equal: bool = False) -> bool:
    path = resolve_path(path, label="containment candidate")
    parent = resolve_path(parent, label="containment parent")
    return path == parent if allow_equal and path == parent else parent in path.parents


def normalize_repo_path(value: str | PurePosixPath) -> str:
    """Return a safe repository-relative POSIX path."""

    if not isinstance(value, (str, PurePosixPath)):
        raise WorkflowPathError("Workflow path must be text.")
    raw = str(value).replace("\\", "/").strip()
    if not raw:
        raise WorkflowPathError("Workflow path must not be empty.")
    if raw.startswith("/") or raw.startswith("//"):
        raise WorkflowPathError(f"Absolute workflow path is not allowed: {raw}")
    if len(raw) >= 2 and raw[1] == ":":
        raise WorkflowPathError(f"Drive-qualified workflow path is not allowed: {raw}")
    parts = PurePosixPath(raw).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise WorkflowPathError(f"Unsafe workflow path: {raw}")
    return PurePosixPath(*parts).as_posix()


def _load_layout(root: Path) -> dict[str, Any]:
    layout_path = root / Path(*LAYOUT_RELATIVE.parts)
    try:
        raw = layout_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkflowPathError(f"Missing workflow layout: {layout_path}") from exc
    try:
        layout = parse_bounded_json(raw)
    except json.JSONDecodeError as exc:
        raise WorkflowPathError(f"Invalid workflow layout JSON: {exc}") from exc
    except (JSONResourceLimitError, RecursionError) as exc:
        raise WorkflowPathResourceError(f"Invalid workflow layout JSON: {exc}") from exc
    if not isinstance(layout, dict):
        raise WorkflowPathError("Workflow layout root must be an object.")
    for key in ("layout_version", "protocol_version", "workflow_schema_version"):
        if layout.get(key) != 3:
            raise WorkflowPathError(f"Workflow layout requires {key}=3.")
    paths = layout.get("paths")
    if not isinstance(paths, dict):
        raise WorkflowPathError("Workflow layout paths must be an object.")
    for key, value in paths.items():
        try:
            paths[key] = normalize_repo_path(value)
        except WorkflowPathError as exc:
            raise WorkflowPathError(f"Invalid layout path {key!r}: {exc}") from exc
    return layout


@dataclass(frozen=True)
class WorkflowPaths:
    root: Path
    common_dir: Path
    git_dir: Path
    layout: dict[str, Any]

    @classmethod
    def discover(cls, start: Path | str | None = None) -> "WorkflowPaths":
        origin = resolve_path(
            Path(start or Path.cwd()),
            label="workflow origin",
            expand_user=True,
        )
        if origin.is_file():
            origin = origin.parent
        root = _git_absolute_dir(origin, "--show-toplevel")
        common_dir = _git_absolute_dir(origin, "--git-common-dir", root)
        git_dir = _git_absolute_dir(origin, "--git-dir", root)
        layout = _load_layout(root)
        return cls(root=root, common_dir=common_dir, git_dir=git_dir, layout=layout)

    def tracked(self, key_or_relative: str, *, key: bool = True) -> Path:
        paths = self.layout["paths"]
        if key:
            if key_or_relative not in paths:
                raise WorkflowPathError(f"Unknown workflow layout path: {key_or_relative}")
            relative = paths[key_or_relative]
        else:
            relative = normalize_repo_path(key_or_relative)
        candidate = resolve_path(
            self.root / Path(*PurePosixPath(relative).parts),
            label=f"tracked path {relative}",
        )
        if not _is_descendant(candidate, self.root):
            raise WorkflowPathError(f"Tracked path escapes worktree root: {relative}")
        return candidate

    @property
    def shared_runtime(self) -> Path:
        candidate = resolve_path(self.common_dir / RUNTIME_NAME, label="shared runtime")
        if not _is_descendant(candidate, self.common_dir):
            raise WorkflowPathError("Shared runtime escapes git common directory.")
        return candidate

    @property
    def lane_runtime(self) -> Path:
        candidate = resolve_path(self.git_dir / RUNTIME_NAME, label="lane runtime")
        if not _is_descendant(candidate, self.git_dir):
            raise WorkflowPathError("Lane runtime escapes worktree Git directory.")
        return candidate

    def ensure_runtime(self) -> None:
        for relative in (
            "registry/lanes",
            "claims",
            "resources",
            "queue",
            "heartbeats",
            "locks",
            "backups",
            "audit",
        ):
            (self.shared_runtime / relative).mkdir(parents=True, exist_ok=True)
        self.lane_runtime.mkdir(parents=True, exist_ok=True)

    def relative(self, path: Path) -> str:
        resolved = resolve_path(path, label="worktree-relative path")
        if not _is_descendant(resolved, self.root):
            raise WorkflowPathError(f"Path is outside current worktree: {path}")
        return resolved.relative_to(self.root).as_posix()


def atomic_write_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Durably replace a file after writing and rereading a sibling temp file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != data:
            raise OSError(f"Temporary file verification failed for {path}")
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode=mode)


def atomic_write_json(path: Path, payload: Any, *, mode: int | None = None) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        mode=mode,
    )


def safe_join(parent: Path, components: Iterable[str]) -> Path:
    """Join untrusted single components while preserving parent containment."""

    candidate = parent
    for component in components:
        if not component or component in {".", ".."} or any(
            marker in component for marker in ("/", "\\", ":")
        ):
            raise WorkflowPathError(f"Unsafe path component: {component!r}")
        candidate /= component
    candidate = resolve_path(candidate, label="runtime path")
    if not _is_descendant(candidate, parent):
        raise WorkflowPathError("Joined path escapes its runtime domain.")
    return candidate
