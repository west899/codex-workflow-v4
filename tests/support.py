from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def run(
    command: list[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        command,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def install_project(target: Path, *, parallel_mode: str = "single", extra: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    return run(
        [
            sys.executable,
            "-B",
            str(PACKAGE_ROOT / "install.py"),
            str(target),
            "--project-name",
            "workflow-test",
            "--parallel-mode",
            parallel_mode,
            *(extra or []),
        ],
        cwd=PACKAGE_ROOT,
    )


def git(target: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = run(["git", *arguments], cwd=target)
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result


def configure_git(target: Path) -> None:
    git(target, "config", "user.email", "workflow-test@example.invalid")
    git(target, "config", "user.name", "Workflow Test")


def commit_all(target: Path, message: str) -> str:
    git(target, "add", ".")
    git(target, "commit", "-m", message)
    return git(target, "rev-parse", "HEAD").stdout.strip()


def create_baseline(target: Path) -> str:
    configure_git(target)
    return commit_all(target, "baseline")


def workflow_command(target: Path, script: str, *arguments: str) -> list[str]:
    return [
        sys.executable,
        "-B",
        str(target / ".codex-workflow" / "bin" / script),
        *arguments,
    ]


def canonical_json(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def normalized_markdown(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).rstrip("\n") + "\n"


def requirements_fingerprint(metadata: dict, markdown: str) -> str:
    payload = {
        key: metadata[key]
        for key in ("schema_version", "brief_id", "revision", "target_release", "requirements")
    }
    return hashlib.sha256(canonical_json(payload) + b"\0" + normalized_markdown(markdown).encode("utf-8")).hexdigest()


def approved_requirements(target: Path, *, brief_id: str = "REQ-001") -> tuple[Path, str]:
    metadata = {
        "schema_version": 1,
        "brief_id": brief_id,
        "revision": 1,
        "target_release": "MVP-1",
        "status": "approved",
        "requirements": {
            "scope": {"in": ["Deliver the observable workflow"], "out": ["Production release"]},
            "users": [{"id": "REQ-U-001", "text": "Project maintainer", "status": "confirmed", "source": "user:test"}],
            "outcomes": [{"id": "REQ-O-001", "text": "A verified result", "status": "confirmed", "source": "user:test"}],
            "flows": [{"id": "REQ-FLOW-001", "steps": ["claim", "implement", "verify"], "status": "confirmed", "source": "user:test"}],
            "capabilities": [{"id": "REQ-F-001", "priority": "must", "preconditions": [], "inputs": [], "action": "Run the workflow", "observable_result": "The gate passes", "boundaries": ["No release"], "status": "confirmed", "source": "user:test"}],
            "scenarios": [
                {"id": "REQ-S-001", "type": "happy", "applicability": "required", "na_reason": None, "na_source": None, "expected": "The task completes", "status": "confirmed", "source": "user:test"},
                {"id": "REQ-S-002", "type": "failure", "applicability": "required", "na_reason": None, "na_source": None, "expected": "Invalid state is rejected", "status": "confirmed", "source": "user:test"},
                {"id": "REQ-S-003", "type": "non_goal", "applicability": "required", "na_reason": None, "na_source": None, "expected": "No automatic deployment", "status": "confirmed", "source": "user:test"},
            ],
            "non_goals": [{"id": "REQ-NG-001", "text": "Production release", "status": "confirmed", "source": "user:test"}],
            "constraints": [],
            "open_questions": [{"id": "REQ-Q-001", "blocking": False, "status": "open", "owner": "maintainer", "resolution_target": "before release", "source": "user:test", "resolution": None, "resolved_by": None, "resolved_source": None}],
            "calibration_rounds": [{"id": "CAL-001", "result": "confirmed", "changed_requirement_ids": [], "source": "user:test"}],
        },
        "approval": {"approved_by": "test-user", "approved_at": "2026-07-11T00:00:00Z", "source": "user:test", "approved_fingerprint": None},
    }
    markdown = "\n# Approved Requirements\n\nREQ-F-001 and REQ-S-001 are confirmed by the test user.\n"
    fingerprint = requirements_fingerprint(metadata, markdown)
    metadata["approval"]["approved_fingerprint"] = fingerprint
    brief = target / ".codex-workflow" / "governance" / "requirements" / f"{brief_id}.md"
    brief.write_text(
        "<!-- CODEX_REQUIREMENTS_JSON_START -->\n"
        + json.dumps(metadata, ensure_ascii=False, indent=2)
        + "\n<!-- CODEX_REQUIREMENTS_JSON_END -->"
        + markdown,
        encoding="utf-8",
    )
    baseline = {"brief_id": brief_id, "revision": 1, "approval_fingerprint": fingerprint}
    for relative in (
        Path(".codex-workflow/governance/PROJECT.md"),
        Path(".codex-workflow/state/MVP_BACKLOG.md"),
    ):
        path = target / relative
        text = path.read_text(encoding="utf-8")
        start = "<!-- CODEX_REQUIREMENTS_BASELINE_START -->"
        end = "<!-- CODEX_REQUIREMENTS_BASELINE_END -->"
        payload = dict(baseline)
        if relative.name == "MVP_BACKLOG.md":
            payload = {
                "workflow_schema_version": 3,
                "requirements_gate_mode": "required",
                **baseline,
                "target_release": "MVP-1",
                "status": "approved",
            }
        text = text.split(start, 1)[0] + start + "\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n" + end + text.split(end, 1)[1]
        path.write_text(text, encoding="utf-8")
    return brief, fingerprint


def basic_v3_record(
    base_commit: str,
    *,
    task_id: str = "MVP-001",
    source_type: str = "user_directive",
    requirements_baseline: dict | None = None,
    allowed_paths: list[str] | None = None,
    resources: list[str] | None = None,
) -> dict:
    allowed_paths = allowed_paths or ["src/**", "tests/**"]
    resources = resources or ["path:src", "path:tests"]
    source = {
        "type": source_type,
        "reference": task_id if source_type == "mvp_backlog" else "test-directive",
        "priority_reason": "Regression test",
    }
    if requirements_baseline:
        source["requirements_baseline"] = requirements_baseline
    return {
        "version": 3,
        "generation": 0,
        "phase": "coordinator",
        "task_id": task_id,
        "status": "authorized",
        "source": source,
        "request": "Verify the V3 workflow behavior",
        "scope": {"in": ["V3 behavior"], "out": ["Production release"], "allowed_paths": allowed_paths, "resource_keys": resources},
        "planning": {"level": "small", "exec_plan": None, "steps": ["Run the focused regression test"]},
        "implementation_authorization": {"authorized": True, "authorized_by": "test-user", "authorized_at": "2026-07-11T00:00:00Z", "source": "user:test"},
        "risk": {"product_scope": False, "sensitive_data": False, "destructive_change": False, "irreversible_architecture": False, "production_release": False},
        "base_commit": base_commit,
        "acceptance": [{"id": "AC-001", "criterion": "The workflow behavior is observable", "status": "pending", "evidence": []}],
        "lane": {"lane_id": None, "mode": "single", "branch": None, "base_ref": None, "base_commit": None, "claim_id": None, "owner_generation": 0, "assignment": None, "allowed_paths": allowed_paths, "resource_keys": resources, "dependency_snapshot": {"backlog_commit": base_commit, "dependencies": []}},
        "verification": {"status": "pending", "delivery_commit": None, "delivery_hash": None, "patch_hash": None, "snapshot_id": None, "changed_paths": []},
        "developer": {"agent_id": None, "snapshot_id": None, "commands": [], "handoff": None},
        "review": {"agent_id": None, "snapshot_id": None, "status": "pending", "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0}, "requirement_checklist": [], "accepted_findings": [], "summary": None},
        "human_approvals": [],
        "integration": {"status": "not_ready", "mode": None, "policy_id": None, "source_ref": None, "target_ref": None, "target_parent": None, "pr_head_commit": None, "result_commit": None, "merge_strategy": None, "queue_id": None, "queued_at": None, "queue_priority": None, "closeout_commit": None, "closeout_state_fingerprint": None, "pr_url": None, "ci_checks": [], "evidence": []},
        "process_retrospective": {"completed": False, "completed_by": None, "completed_at": None, "questions": {"repeated_problem_found": False, "guidance_gap_found": False, "deterministic_check_candidate_found": False}, "summary": None},
        "rule_proposals": [],
        "remaining_risks": [],
    }


def write_record(target: Path, record: dict) -> Path:
    path = target / ".codex-workflow" / "state" / "runs" / f"{record['task_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def record_relative(record_path: Path, target: Path) -> str:
    return record_path.relative_to(target).as_posix()

