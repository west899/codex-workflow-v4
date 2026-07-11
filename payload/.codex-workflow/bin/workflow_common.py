#!/usr/bin/env python3
"""Shared deterministic data and Git helpers for Codex Workflow V3."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from workflow_paths import WorkflowPathError, WorkflowPaths, normalize_repo_path


REQUIREMENTS_START = "<!-- CODEX_REQUIREMENTS_JSON_START -->"
REQUIREMENTS_END = "<!-- CODEX_REQUIREMENTS_JSON_END -->"
HEX_OID = re.compile(r"^[0-9a-f]{40,64}$")


class WorkflowDataError(ValueError):
    """Tracked workflow data is missing, inconsistent, or unsafe."""


def fault_injection(name: str) -> None:
    """Abort a workflow write boundary when an isolated regression test requests it."""

    if os.environ.get("CODEX_WORKFLOW_TEST_FAIL_AT") == name:
        raise OSError(f"Injected workflow failure at {name}.")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def normalize_markdown(value: str) -> str:
    value = value.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", value).rstrip("\n") + "\n"


@dataclass(frozen=True)
class RequirementsBrief:
    path: Path
    metadata: dict[str, Any]
    markdown: str
    fingerprint: str


def read_requirements_brief(path: Path) -> RequirementsBrief:
    source = path.read_text(encoding="utf-8-sig")
    if source.count(REQUIREMENTS_START) != 1 or source.count(REQUIREMENTS_END) != 1:
        raise WorkflowDataError("Requirements brief must contain one JSON marker pair.")
    before, rest = source.split(REQUIREMENTS_START, 1)
    raw_json, markdown = rest.split(REQUIREMENTS_END, 1)
    if before.strip():
        raise WorkflowDataError("Requirements JSON marker must be the first brief content.")
    try:
        metadata = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise WorkflowDataError(f"Requirements metadata is invalid JSON: {exc}") from exc
    if not isinstance(metadata, dict):
        raise WorkflowDataError("Requirements metadata root must be an object.")
    fingerprint_payload = {
        key: metadata.get(key)
        for key in ("schema_version", "brief_id", "revision", "target_release", "requirements")
    }
    digest = hashlib.sha256(
        canonical_json_bytes(fingerprint_payload)
        + b"\0"
        + normalize_markdown(markdown).encode("utf-8")
    ).hexdigest()
    return RequirementsBrief(path, metadata, normalize_markdown(markdown), digest)


def git(paths: WorkflowPaths, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(paths.root), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise WorkflowDataError(detail or f"Git failed: {' '.join(arguments)}")
    return result


def rev_parse(paths: WorkflowPaths, reference: str) -> str:
    value = git(paths, "rev-parse", "--verify", reference).stdout.strip()
    if not HEX_OID.fullmatch(value):
        raise WorkflowDataError(f"Git reference did not resolve to an object ID: {reference}")
    return value


def is_ancestor(paths: WorkflowPaths, ancestor: str, descendant: str) -> bool:
    result = git(paths, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    if result.returncode not in {0, 1}:
        raise WorkflowDataError((result.stderr or result.stdout).strip())
    return result.returncode == 0


def current_branch(paths: WorkflowPaths) -> str | None:
    result = git(paths, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def status_paths(paths: WorkflowPaths) -> list[str]:
    raw = subprocess.run(
        ["git", "-C", str(paths.root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True,
        check=True,
    ).stdout
    entries = [item for item in raw.split(b"\0") if item]
    changed: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index].decode("utf-8", errors="surrogateescape")
        path = entry[3:]
        if entry[:2] in {"R ", "C ", " R", " C"} and index + 1 < len(entries):
            changed.append(entries[index + 1].decode("utf-8", errors="surrogateescape").replace("\\", "/"))
            index += 1
        changed.append(path.replace("\\", "/"))
        index += 1
    return sorted(set(changed))


def allowed_path(path: str, patterns: Iterable[str]) -> bool:
    normalized = normalize_repo_path(path)
    for raw in patterns:
        pattern = normalize_repo_path(raw)
        if fnmatch.fnmatchcase(normalized, pattern):
            return True
        prefix = pattern.removesuffix("/**").rstrip("/")
        if pattern.endswith("/**") and (normalized == prefix or normalized.startswith(prefix + "/")):
            return True
    return False


def _raw_delta(paths: WorkflowPaths, base: str, target: str) -> list[dict[str, str]]:
    output = subprocess.run(
        [
            "git", "-C", str(paths.root), "diff-tree", "-r", "--raw", "-z",
            "--no-renames", "--full-index", "--no-commit-id", base, target,
        ],
        capture_output=True,
        check=True,
    ).stdout
    items = output.split(b"\0")
    records: list[dict[str, str]] = []
    index = 0
    while index < len(items):
        header = items[index]
        index += 1
        if not header:
            continue
        if index >= len(items):
            raise WorkflowDataError("Malformed raw Git delta.")
        path = items[index].decode("utf-8", errors="surrogateescape").replace("\\", "/")
        index += 1
        fields = header.decode("ascii").split()
        if len(fields) != 5 or not fields[0].startswith(":"):
            raise WorkflowDataError("Malformed raw Git delta header.")
        records.append(
            {
                "status": fields[4][0],
                "old_path": path,
                "new_path": path,
                "old_mode": fields[0][1:],
                "new_mode": fields[1],
                "old_oid": fields[2],
                "new_oid": fields[3],
            }
        )
    return sorted(records, key=lambda row: (row["new_path"], row["old_path"], row["status"]))


def canonical_delivery(paths: WorkflowPaths, base: str, target: str) -> dict[str, Any]:
    base_oid = rev_parse(paths, base)
    target_oid = rev_parse(paths, target)
    entries = [
        entry
        for entry in _raw_delta(paths, base_oid, target_oid)
        if not is_mutable_control_path(entry["new_path"])
        and not is_mutable_control_path(entry["old_path"])
    ]
    patch_hash = sha256_json({"algorithm": "codex-delta-v1", "entries": entries})
    delivery_hash = sha256_json(
        {"algorithm": "codex-delta-v1", "base_commit": base_oid, "entries": entries}
    )
    return {
        "base_commit": base_oid,
        "target_commit": target_oid,
        "entries": entries,
        "changed_paths": sorted({row["new_path"] for row in entries}),
        "patch_hash": patch_hash,
        "delivery_hash": delivery_hash,
    }


def is_mutable_control_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized == ".codex-workflow/governance/PLAN.md" or normalized == ".codex-workflow/state/MVP_BACKLOG.md" or normalized.startswith(
        ".codex-workflow/state/runs/"
    ) or normalized.startswith(".codex-workflow/state/plans/")


def read_embedded_json(text: str, marker_name: str) -> dict[str, Any]:
    start = f"<!-- {marker_name}_START -->"
    end = f"<!-- {marker_name}_END -->"
    if text.count(start) != 1 or text.count(end) != 1:
        raise WorkflowDataError(f"Expected one {marker_name} marker pair.")
    raw = text.split(start, 1)[1].split(end, 1)[0]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkflowDataError(f"Invalid {marker_name} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowDataError(f"{marker_name} JSON must be an object.")
    return payload


def snapshot_id(record: dict[str, Any], delivery_hash: str) -> str:
    lane = record.get("lane") or {}
    return sha256_json(
        {
            "task_id": record.get("task_id"),
            "lane_id": lane.get("lane_id"),
            "base_commit": record.get("base_commit") or lane.get("base_commit"),
            "branch": lane.get("branch"),
            "delivery_hash": delivery_hash,
        }
    )


def load_record(paths: WorkflowPaths, relative: str | Path) -> tuple[Path, dict[str, Any]]:
    raw = str(relative).replace("\\", "/")
    normalized = normalize_repo_path(raw)
    candidate = (paths.root / Path(*PurePosixPath(normalized).parts)).resolve()
    runs = paths.tracked("runs").resolve()
    if runs not in candidate.parents or not candidate.is_file():
        raise WorkflowDataError("Task record must be an existing file under the configured runs directory.")
    try:
        record = json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowDataError(f"Task record is invalid JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise WorkflowDataError("Task record root must be an object.")
    return candidate, record


def split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in stripped[1:]:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if current:
        cells.append("".join(current).strip())
    return cells


def backlog_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        cells = split_markdown_row(line)
        if len(cells) < 9 or not re.fullmatch(r"(?:MVP|OPS)-[A-Za-z0-9._-]+", cells[0]):
            continue
        rows.append(
            {
                "line": index,
                "id": cells[0],
                "dependencies": [] if cells[3] in {"", "无", "-"} else [item.strip() for item in re.split(r"[,，]", cells[3]) if item.strip()],
                "status": cells[6],
                "blocking_type": cells[7],
                "cells": cells,
            }
        )
    return rows


def update_backlog_status(text: str, task_id: str, status: str, *, integration: str | None = None) -> str:
    lines = text.splitlines()
    matches = [row for row in backlog_rows(text) if row["id"] == task_id]
    if len(matches) != 1:
        raise WorkflowDataError(f"Backlog must contain exactly one row for {task_id}.")
    row = matches[0]
    cells = row["cells"]
    cells[6] = status
    if integration is not None and len(cells) >= 11:
        cells[10] = integration
    lines[row["line"]] = "| " + " | ".join(cells) + " |"
    return "\n".join(lines).rstrip("\n") + "\n"


def unlock_ready_dependencies(text: str) -> tuple[str, list[str]]:
    rows = backlog_rows(text)
    by_id = {row["id"]: row for row in rows}
    unlocked: list[str] = []
    for row in rows:
        if row["status"] != "blocked" or row["blocking_type"] != "dependencies":
            continue
        dependencies = row["dependencies"]
        if dependencies and all(
            dependency in by_id and by_id[dependency]["status"] == "done"
            for dependency in dependencies
        ):
            cells = row["cells"]
            cells[6] = "ready"
            cells[7] = "none"
            text_lines = text.splitlines()
            text_lines[row["line"]] = "| " + " | ".join(cells) + " |"
            text = "\n".join(text_lines).rstrip("\n") + "\n"
            unlocked.append(row["id"])
            rows = backlog_rows(text)
            by_id = {item["id"]: item for item in rows}
    return text, unlocked


def closeout_state_fingerprint(record: dict[str, Any], backlog_text: str) -> str:
    integration = record.get("integration") or {}
    state = {
        "task_id": record.get("task_id"),
        "integration": {
            key: integration.get(key)
            for key in (
                "status", "mode", "policy_id", "source_ref", "target_ref",
                "target_parent", "pr_head_commit", "result_commit", "merge_strategy",
                "queue_id", "closeout_commit", "pr_url", "ci_checks", "evidence",
            )
        },
        "backlog": normalize_markdown(backlog_text),
    }
    return sha256_json(state)
