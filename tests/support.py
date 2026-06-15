from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def run(
    command: list[str],
    *,
    cwd: Path,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        command,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        env=env,
    )


def install_project(target: Path) -> subprocess.CompletedProcess[str]:
    return run(
        [
            sys.executable,
            "-B",
            str(PACKAGE_ROOT / "install.py"),
            str(target),
            "--project-name",
            "workflow-test",
        ],
        cwd=PACKAGE_ROOT,
    )


def create_baseline(target: Path) -> str:
    commands = [
        ["git", "config", "user.email", "workflow-test@example.invalid"],
        ["git", "config", "user.name", "Workflow Test"],
        ["git", "add", "."],
        ["git", "commit", "-m", "baseline"],
    ]
    for command in commands:
        result = run(command, cwd=target)
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
    result = run(["git", "rev-parse", "HEAD"], cwd=target)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def write_task(target: Path, record: dict) -> Path:
    runs = target / ".agent" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{record['task_id']}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    pointer = target / ".agent" / "active-task"
    pointer.write_text(f".agent/runs/{path.name}\n")
    return path


def basic_record(base_commit: str, *, status: str = "planned") -> dict:
    return {
        "version": 2,
        "task_id": "test-task",
        "status": status,
        "source": {
            "type": "user_directive",
            "reference": "test-request",
            "priority_reason": "Regression test",
        },
        "request": "Verify workflow behavior",
        "scope": {"in": ["Workflow behavior"], "out": ["Unrelated behavior"]},
        "planning": {
            "level": "small",
            "exec_plan": None,
            "steps": ["Run the focused regression test"],
        },
        "implementation_authorization": {
            "authorized": True,
            "authorized_by": "test-user",
            "authorized_at": "2026-06-15T00:00:00Z",
            "source": "test",
        },
        "risk": {
            "product_scope": False,
            "sensitive_data": False,
            "destructive_change": False,
            "irreversible_architecture": False,
            "production_release": False,
        },
        "base_commit": base_commit,
        "acceptance": [
            {
                "id": "AC-001",
                "criterion": "The workflow behavior is observable",
                "status": "pending",
                "evidence": [],
            }
        ],
        "developer": {
            "agent_id": None,
            "worktree_hash": None,
            "commands": [],
            "handoff": None,
        },
        "review": {
            "agent_id": None,
            "reviewed_worktree_hash": None,
            "status": "pending",
            "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
            "requirement_checklist": [],
            "accepted_findings": [],
            "summary": None,
        },
        "human_approvals": [],
        "process_retrospective": {
            "completed": False,
            "completed_by": None,
            "completed_at": None,
            "questions": {
                "repeated_problem_found": False,
                "guidance_gap_found": False,
                "deterministic_check_candidate_found": False,
            },
            "summary": None,
        },
        "rule_proposals": [],
        "remaining_risks": [],
    }
