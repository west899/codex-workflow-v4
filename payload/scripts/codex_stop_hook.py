#!/usr/bin/env python3
"""Codex Stop hook: block failed gates and distinguish verified from integrated."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def continue_with_warning(message: str) -> None:
    emit({"continue": True, "systemMessage": message})


def block_once(event: dict, reason: str) -> None:
    if event.get("stop_hook_active"):
        continue_with_warning(
            "The active task is not verified after one Stop-hook continuation. "
            "Do not claim completion.\n" + reason
        )
        return
    emit(
        {
            "decision": "block",
            "reason": reason,
        }
    )


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        event = {}

    cwd = Path(event.get("cwd") or Path.cwd())
    try:
        root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except OSError:
        emit({"continue": True})
        return
    if root_result.returncode != 0:
        emit({"continue": True})
        return
    root = Path(root_result.stdout.strip())
    active_pointer = root / ".agent" / "active-task"
    if not active_pointer.is_file():
        emit({"continue": True})
        return

    task_record = active_pointer.read_text(encoding="utf-8").strip()
    if not task_record:
        block_once(
            event,
            ".agent/active-task is empty; repair or remove it explicitly.",
        )
        return

    record_path = (root / task_record).resolve()
    runs_root = (root / ".agent" / "runs").resolve()
    if runs_root not in record_path.parents or not record_path.is_file():
        block_once(
            event,
            "The active task pointer must reference an existing record under "
            ".agent/runs/.",
        )
        return

    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        block_once(event, f"Unable to read the active task record: {exc}")
        return
    if not isinstance(record, dict):
        block_once(event, "The active task record root must be a JSON object.")
        return

    status = record.get("status")
    if status in {"planned", "authorized", "in_progress"}:
        continue_with_warning(
            f"Active task {record.get('task_id', task_record)} remains {status}. "
            "Pausing is allowed, but do not report it as verified, done, or released."
        )
        return
    if status != "completed":
        block_once(
            event,
            f"Active task has unsupported status {status!r}; repair the task record.",
        )
        return

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "workflow_check.py"),
                "gate",
                task_record,
            ],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        block_once(event, f"Unable to run the active task gate: {exc}")
        return
    if result.returncode == 0:
        continue_with_warning(
            "Active task gate passed. Until PR/CI/human merge is recorded, "
            "report the Backlog item as verified, not done or released."
        )
        return

    reason = (result.stderr or result.stdout).strip()
    block_once(
        event,
        "Active task gate failed. Resolve these checks before claiming completion:\n"
        + reason,
    )


if __name__ == "__main__":
    main()
