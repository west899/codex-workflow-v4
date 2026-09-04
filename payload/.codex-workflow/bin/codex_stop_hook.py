#!/usr/bin/env python3
"""Lane-local Codex Stop hook for Codex Workflow V4."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from workflow_common import WorkflowDataError, load_record, v4_stop_hook_next_action
from workflow_paths import WorkflowPathError, WorkflowPaths


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def warn(message: str) -> None:
    emit({"continue": True, "systemMessage": message})


def block_once(event: dict, reason: str) -> None:
    if event.get("stop_hook_active"):
        warn("Workflow state remains inconsistent after one Stop continuation. Do not claim completion.\n" + reason)
    else:
        emit({"decision": "block", "reason": reason})


def active_lane_count(paths: WorkflowPaths) -> int:
    registry = paths.shared_runtime / "registry" / "lanes"
    return len(list(registry.glob("*.json"))) if registry.is_dir() else 0


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, AttributeError):
        event = {}
    cwd = Path(event.get("cwd") or Path.cwd()).expanduser()
    try:
        paths = WorkflowPaths.discover(cwd)
    except (WorkflowPathError, OSError):
        emit({"continue": True})
        return

    pointer = paths.lane_runtime / "lane.json"
    if not pointer.is_file():
        count = active_lane_count(paths)
        if count:
            warn(
                f"Coordinator worktree has no active lane. {count} lane(s) remain active; "
                "inspect them with workflow_lane.py list --all."
            )
        else:
            emit({"continue": True})
        return
    try:
        identity = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        block_once(event, f"Lane pointer is unreadable: {exc}")
        return
    if not isinstance(identity, dict):
        block_once(event, "Lane pointer root must be a JSON object.")
        return
    try:
        _, record = load_record(paths, str(identity.get("record", "")))
    except WorkflowDataError as exc:
        block_once(event, str(exc))
        return
    lane = record.get("lane") or {}
    for field in ("task_id", "lane_id", "claim_id", "owner_generation", "branch"):
        expected = record.get("task_id") if field == "task_id" else lane.get(field)
        if identity.get(field) != expected:
            block_once(event, f"Lane pointer {field} does not match the tracked task record.")
            return

    product_action = v4_stop_hook_next_action(record)
    if product_action is not None:
        warn(product_action["text"])
        return

    status = record.get("status")
    verification = record.get("verification") or {}
    integration = record.get("integration") or {}
    task_id = record.get("task_id")
    if status in {"planned", "authorized", "in_progress"}:
        warn(
            f"Lane {lane.get('lane_id')} task {task_id} remains {status}. Pausing is allowed, "
            "but do not report it as verified, done, synced, or released."
        )
        return
    if status != "completed":
        block_once(event, f"Task {task_id} has unsupported status {status!r}.")
        return
    if verification.get("status") == "pending":
        result = subprocess.run(
            [sys.executable, str(paths.tracked("bin") / "workflow_check.py"), "gate", str(identity["record"])],
            cwd=paths.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            block_once(
                event,
                "The lane gate failed; resolve it before claiming verified:\n"
                + (result.stderr or result.stdout).strip(),
            )
            return
        warn(
            f"Task {task_id} gate passes for this lane. Run workflow_state.py mark-verified "
            "before reporting it as verified."
        )
        return
    if verification.get("status") == "invalidated" or integration.get("status") == "invalidated":
        warn(f"Task {task_id} evidence was invalidated. Return this lane to Developer and re-verify.")
        return
    integration_status = integration.get("status")
    if verification.get("status") == "passed" and integration_status == "not_ready":
        warn(
            f"Task {task_id} is verified, not done. Record any required local-bootstrap approval, "
            "then run prepare-integration."
        )
    elif integration_status == "pending":
        warn(
            f"Task {task_id} is verified and awaiting {integration.get('mode')} integration. "
            "The Stop hook will not merge, push, or close it automatically."
        )
    elif integration_status == "queued":
        warn(f"Task {task_id} is queued. Only the Integrator may advance the target branch.")
    elif integration_status == "merged_pending_closeout":
        warn(
            f"Task {task_id} product delivery is merged but closeout is not confirmed. "
            "Prepare/fetch the closeout-only commit and run confirm-closeout or reconcile."
        )
    elif integration_status == "integrated":
        warn(
            f"Task {task_id} has integrated tracked state, but this lane pointer still exists. "
            "Confirm the exact closeout commit, then release runtime claims; done is not released."
        )
    else:
        block_once(event, f"Task {task_id} has unsupported integration status {integration_status!r}.")


if __name__ == "__main__":
    main()

