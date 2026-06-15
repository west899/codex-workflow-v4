#!/usr/bin/env python3
"""Deterministic checks for the repository's AI workflow control plane."""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path


VALID_MODES = {"start", "manual", "stop", "preflight", "snapshot", "gate"}
MODES_REQUIRING_ARGUMENT = {"preflight", "snapshot", "gate"}


def parse_cli() -> tuple[str, str | None]:
    if len(sys.argv) == 1:
        return "manual", None
    if sys.argv[1] in {"-h", "--help"}:
        print(
            "usage: workflow_check.py "
            "[start|manual|stop|preflight RECORD|snapshot RECORD|gate RECORD]"
        )
        raise SystemExit(0)

    mode = sys.argv[1]
    if mode not in VALID_MODES:
        print(f"[workflow-check] ERROR: unknown mode: {mode}", file=sys.stderr)
        raise SystemExit(2)

    argument = sys.argv[2] if len(sys.argv) > 2 else None
    if mode in MODES_REQUIRING_ARGUMENT and not argument:
        print(
            f"[workflow-check] ERROR: {mode} mode requires a task-record path.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if mode not in MODES_REQUIRING_ARGUMENT and argument:
        print(
            f"[workflow-check] ERROR: {mode} mode does not accept an argument.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if len(sys.argv) > 3:
        print("[workflow-check] ERROR: too many arguments.", file=sys.stderr)
        raise SystemExit(2)
    return mode, argument


MODE, MODE_ARGUMENT = parse_cli()
ERRORS: list[str] = []
WARNINGS: list[str] = []


def git_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
        return Path(result.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        print("[workflow-check] ERROR: run this command inside a Git repository.")
        raise SystemExit(1)


ROOT = git_root()
HASH_EXCLUDED_PATHS = {
    "PLAN.md",
    "docs/MVP_BACKLOG.md",
}

DOCUMENTS = {
    "PROJECT.md": {
        "max_lines": 80,
        "sections": ["## 一眼看懂", "## 已确认事实", "## 待确认"],
    },
    "PLAN.md": {
        "max_lines": 80,
        "sections": ["## 当前结果", "## 当前任务", "## 阶段门", "## 验证证据"],
    },
    "AGENTS.md": {
        "max_lines": 100,
        "sections": [
            "## 开始协议",
            "## 事实协议",
            "## 执行协议",
            "## 流程改进闭环",
        ],
    },
    "DECISIONS.md": {
        "max_lines": 80,
        "sections": ["## 当前有效决定", "## 纠错记录"],
    },
    "docs/WORKFLOW_V2.md": {
        "max_lines": 320,
        "sections": [
            "## 一眼看懂",
            "## 共同入口",
            "## 计划层级",
            "## 逐步流程",
            "### 第 1 步：目标与问题发现",
            "### 第 2 步：目标版本与范围",
            "### 第 3 步：方案、Backlog 与工程基线",
            "### 第 4 步：建立并授权单个任务",
            "### 第 5 步：Developer 探索与执行计划",
            "### 第 6 步：Developer 实现与自验证",
            "### 第 7 步：Reviewer 独立审查",
            "### 第 8 步：修复、复审与人工验收",
            "### 第 9 步：流程复盘与 Rule Proposal",
            "### 第 10 步：机械门禁与任务关闭",
            "### 第 11 步：合并、循环、上线与观察",
            "## 文件汇总",
        ],
    },
}


def read_project_file(relative_path: str) -> str | None:
    target = ROOT / relative_path
    if not target.is_file():
        ERRORS.append(f"Missing required file: {relative_path}")
        return None
    content = target.read_text(encoding="utf-8")
    if not content.strip():
        ERRORS.append(f"Required file is empty: {relative_path}")
    return content


def check_governance_documents() -> None:
    for file_name, rules in DOCUMENTS.items():
        content = read_project_file(file_name)
        if content is None:
            continue
        line_count = len(content.splitlines())
        if line_count > rules["max_lines"]:
            WARNINGS.append(
                f"{file_name} has {line_count} lines; "
                f"review for duplication (soft limit {rules['max_lines']})."
            )
        for section in rules["sections"]:
            if section not in content:
                ERRORS.append(f"{file_name} is missing required section: {section}")

    plan = read_project_file("PLAN.md")
    if plan:
        evidence_match = re.search(
            r"## 验证证据\s*(.*?)(?=^## |\Z)", plan, re.MULTILINE | re.DOTALL
        )
        evidence = evidence_match.group(1) if evidence_match else ""
        completed_tasks = re.findall(
            r"^- \[x\]\s*(?:\[(E-\d+)\])?", plan, re.MULTILINE | re.IGNORECASE
        )
        for evidence_id in completed_tasks:
            if not evidence_id:
                ERRORS.append("Every completed PLAN.md task must include an [E-NNN] ID.")
            elif evidence_id not in evidence:
                ERRORS.append(
                    f"PLAN.md completed task references {evidence_id}, "
                    "but the evidence section does not define it."
                )


def check_skills() -> None:
    skills_root = ROOT / ".agents" / "skills"
    if not skills_root.is_dir():
        ERRORS.append("Missing repository skills directory: .agents/skills")
        return

    required_skills = {
        "orchestrate-project-task",
        "implement-project-task",
        "review-project-change",
    }
    present_skills = {item.parent.name for item in skills_root.glob("*/SKILL.md")}
    for missing_skill in sorted(required_skills - present_skills):
        ERRORS.append(f"Missing required skill: .agents/skills/{missing_skill}")

    required_references = {
        ".agents/skills/orchestrate-project-task/references/task-record-template.json",
        ".agents/skills/orchestrate-project-task/references/rule-proposal-template.json",
        ".agents/skills/orchestrate-project-task/references/mvp-backlog-template.md",
        ".agents/skills/implement-project-task/references/exec-plan-template.md",
        ".agents/skills/review-project-change/references/review-checklist.md",
    }
    for relative_path in sorted(required_references):
        if not (ROOT / relative_path).is_file():
            ERRORS.append(f"Missing required workflow reference: {relative_path}")

    for skill_file in skills_root.glob("*/SKILL.md"):
        content = skill_file.read_text(encoding="utf-8")
        folder_name = skill_file.parent.name
        is_required = folder_name in required_skills
        if "[TODO:" in content:
            message = f"{skill_file.relative_to(ROOT)} contains a template TODO."
            (ERRORS if is_required else WARNINGS).append(message)
        if not content.startswith("---\n"):
            message = f"{skill_file.relative_to(ROOT)} lacks YAML frontmatter."
            (ERRORS if is_required else WARNINGS).append(message)
            continue

        frontmatter = content.split("---", 2)[1]
        name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
        description_match = re.search(
            r"^description:\s*(.+)$", frontmatter, re.MULTILINE
        )
        if not name_match or name_match.group(1).strip() != folder_name:
            message = (
                f"{skill_file.relative_to(ROOT)} name should match folder {folder_name}."
            )
            (ERRORS if is_required else WARNINGS).append(message)
        if not description_match or len(description_match.group(1).strip()) < 30:
            message = f"{skill_file.relative_to(ROOT)} needs a specific description."
            (ERRORS if is_required else WARNINGS).append(message)
        if is_required and "Rule Proposal" not in content:
            ERRORS.append(
                f"{skill_file.relative_to(ROOT)} must define Rule Proposal handling."
            )

        metadata_file = skill_file.parent / "agents" / "openai.yaml"
        if not metadata_file.is_file():
            if is_required:
                ERRORS.append(
                    f"Missing skill metadata: {metadata_file.relative_to(ROOT)}"
                )
            continue
        metadata = metadata_file.read_text(encoding="utf-8")
        if is_required and f"${folder_name}" not in metadata:
            ERRORS.append(
                f"{metadata_file.relative_to(ROOT)} default prompt must mention "
                f"${folder_name}."
            )


def check_codex_configuration() -> None:
    hooks_file = ROOT / ".codex" / "hooks.json"
    if not hooks_file.is_file():
        ERRORS.append("Missing Codex hooks configuration: .codex/hooks.json")
    else:
        try:
            hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
            configured_events = hooks.get("hooks", {})
            if not isinstance(configured_events, dict):
                ERRORS.append(".codex/hooks.json hooks must be an object.")
                configured_events = {}
            expected_commands = {
                "SessionStart": ("workflow_check.py", "start"),
                "Stop": ("codex_stop_hook.py", None),
            }
            for event, (script_name, mode_name) in expected_commands.items():
                groups = configured_events.get(event)
                if not isinstance(groups, list) or not groups:
                    ERRORS.append(f".codex/hooks.json is missing {event} hook.")
                    continue
                handlers = []
                for group in groups:
                    if not isinstance(group, dict):
                        ERRORS.append(
                            f".codex/hooks.json {event} groups must be objects."
                        )
                        continue
                    group_handlers = group.get("hooks", [])
                    if not isinstance(group_handlers, list):
                        ERRORS.append(
                            f".codex/hooks.json {event} hooks must be a list."
                        )
                        continue
                    handlers.extend(
                        hook
                        for hook in group_handlers
                        if isinstance(hook, dict) and hook.get("type") == "command"
                    )
                commands = [
                    handler.get(key, "")
                    for handler in handlers
                    for key in ("command", "commandWindows")
                ]
                if not any(
                    script_name in command
                    and (
                        mode_name is None
                        or re.search(rf"\b{re.escape(mode_name)}\b", command)
                    )
                    for command in commands
                ):
                    ERRORS.append(
                        f".codex/hooks.json {event} hook must run {script_name}"
                        + (f" {mode_name}." if mode_name else ".")
                    )
                if not any(handler.get("commandWindows") for handler in handlers):
                    WARNINGS.append(
                        f".codex/hooks.json {event} hook has no commandWindows override."
                    )
        except json.JSONDecodeError as exc:
            ERRORS.append(f".codex/hooks.json is invalid JSON: {exc}")

    required_agents = {
        "developer": False,
        "reviewer": True,
    }
    for agent_name, must_be_read_only in required_agents.items():
        agent_file = ROOT / ".codex" / "agents" / f"{agent_name}.toml"
        if not agent_file.is_file():
            ERRORS.append(f"Missing custom agent: {agent_file.relative_to(ROOT)}")
            continue
        content = agent_file.read_text(encoding="utf-8")
        for key in ("name", "description", "developer_instructions"):
            if not re.search(rf"^{key}\s*=", content, re.MULTILINE):
                ERRORS.append(
                    f"{agent_file.relative_to(ROOT)} is missing required key {key}."
                )
        if not re.search(
            rf'^name\s*=\s*"{re.escape(agent_name)}"', content, re.MULTILINE
        ):
            ERRORS.append(
                f"{agent_file.relative_to(ROOT)} name must be {agent_name}."
            )
        expected_skill = (
            "$implement-project-task"
            if agent_name == "developer"
            else "$review-project-change"
        )
        if expected_skill not in content:
            ERRORS.append(
                f"{agent_file.relative_to(ROOT)} must reference {expected_skill}."
            )
        if "Rule Proposal" not in content:
            ERRORS.append(
                f"{agent_file.relative_to(ROOT)} must report Rule Proposal candidates."
            )
        if must_be_read_only and not re.search(
            r'^sandbox_mode\s*=\s*"read-only"', content, re.MULTILINE
        ):
            ERRORS.append(
                f"{agent_file.relative_to(ROOT)} must set sandbox_mode to read-only."
            )
        if not must_be_read_only and re.search(
            r'^sandbox_mode\s*=\s*"read-only"', content, re.MULTILINE
        ):
            ERRORS.append(
                f"{agent_file.relative_to(ROOT)} must remain write-capable."
            )


def has_head() -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=ROOT,
        capture_output=True,
    )
    return result.returncode == 0


def current_head() -> str | None:
    if not has_head():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def excluded_from_change_hash(relative_path: str) -> bool:
    return relative_path in HASH_EXCLUDED_PATHS or relative_path.startswith(".agent/")


def worktree_hash(base_commit: str) -> str:
    if not has_head():
        raise ValueError("Repository has no baseline commit (HEAD).")

    digest = hashlib.sha256()
    diff_result = subprocess.run(
        [
            "git",
            "diff",
            "--binary",
            base_commit,
            "--",
            ".",
            ":(exclude).agent/**",
            ":(exclude)PLAN.md",
            ":(exclude)docs/MVP_BACKLOG.md",
        ],
        cwd=ROOT,
        capture_output=True,
    )
    if diff_result.returncode != 0:
        raise ValueError("Unable to diff the worktree from task-record base_commit.")
    diff = diff_result.stdout
    digest.update(b"base-commit\0")
    digest.update(base_commit.encode("ascii"))
    digest.update(b"\0tracked-diff\0")
    digest.update(diff)

    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    for raw_path in sorted(path for path in untracked if path):
        relative_path = raw_path.decode("utf-8", errors="surrogateescape")
        if excluded_from_change_hash(relative_path):
            continue
        target = ROOT / relative_path
        digest.update(b"\0untracked\0")
        digest.update(raw_path)
        if target.is_symlink():
            digest.update(b"\0symlink\0")
            digest.update(os.readlink(target).encode("utf-8", errors="surrogateescape"))
        elif target.is_file():
            digest.update(b"\0mode\0")
            digest.update(str(target.stat().st_mode & 0o777).encode("ascii"))
            digest.update(b"\0content\0")
            digest.update(target.read_bytes())

    return digest.hexdigest()


def task_record_base_commit(record_path: str | None) -> str:
    if not record_path:
        raise ValueError("Snapshot mode requires a task-record path.")

    target = (ROOT / record_path).resolve()
    runs_root = (ROOT / ".agent" / "runs").resolve()
    if runs_root not in target.parents or not target.is_file():
        raise ValueError("Snapshot task record must exist under .agent/runs/.")

    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Task record is invalid JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise ValueError("Task record root must be a JSON object.")

    base_commit = record.get("base_commit")
    if not isinstance(base_commit, str) or not base_commit.strip():
        raise ValueError("Task record requires non-empty base_commit.")
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{base_commit}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError("Task record base_commit is not a valid Git commit.")
    return base_commit


def require_text(value, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        ERRORS.append(f"Task record requires non-empty {field}.")


def require_object(value, field: str) -> dict:
    if not isinstance(value, dict):
        ERRORS.append(f"Task record {field} must be an object.")
        return {}
    return value


def require_list(value, field: str) -> list:
    if not isinstance(value, list):
        ERRORS.append(f"Task record {field} must be a list.")
        return []
    return value


def require_string_list(value, field: str, *, non_empty: bool = False) -> list[str]:
    items = require_list(value, field)
    if non_empty and not items:
        ERRORS.append(f"Task record {field} must not be empty.")
    if any(not isinstance(item, str) or not item.strip() for item in items):
        ERRORS.append(f"Task record {field} must contain only non-empty strings.")
        return []
    return items


def check_acceptance_criteria(acceptance, final: bool) -> None:
    if not isinstance(acceptance, list) or not acceptance:
        ERRORS.append("Task record requires at least one acceptance criterion.")
        return

    acceptance_ids: set[str] = set()
    for index, item in enumerate(acceptance):
        if not isinstance(item, dict):
            ERRORS.append(f"Task record acceptance[{index}] must be an object.")
            continue
        criterion_id = item.get("id")
        require_text(criterion_id, "acceptance[].id")
        require_text(item.get("criterion"), "acceptance[].criterion")
        if isinstance(criterion_id, str):
            if criterion_id in acceptance_ids:
                ERRORS.append(f"Duplicate acceptance criterion ID: {criterion_id}.")
            acceptance_ids.add(criterion_id)

        status = item.get("status")
        evidence = item.get("evidence")
        valid_evidence = (
            isinstance(evidence, list)
            and bool(evidence)
            and all(isinstance(entry, str) and entry.strip() for entry in evidence)
        )
        if final and (status != "pass" or not valid_evidence):
            ERRORS.append(
                f"Acceptance {criterion_id or '<unknown>'} must pass "
                "with non-empty text evidence."
            )
        if not final and status not in {"pending", "pass"}:
            ERRORS.append(
                f"Acceptance {criterion_id or '<unknown>'} must be pending or pass "
                "during preflight."
            )
        if not final and status == "pass" and not valid_evidence:
            ERRORS.append(
                f"Acceptance {criterion_id or '<unknown>'} marked pass "
                "requires non-empty text evidence."
            )


def check_task_source(record: dict, final: bool) -> None:
    if record.get("version") != 2:
        ERRORS.append("Task record version must be 2.")

    source_value = record.get("source")
    source = require_object(source_value, "source")
    if not isinstance(source_value, dict):
        return

    source_type = source.get("type")
    allowed_types = {"mvp_backlog", "incident", "maintenance", "user_directive"}
    if source_type not in allowed_types:
        ERRORS.append(
            "Task source.type must be mvp_backlog, incident, maintenance, "
            "or user_directive."
        )
    require_text(source.get("reference"), "source.reference")
    require_text(source.get("priority_reason"), "source.priority_reason")

    if source_type != "mvp_backlog":
        return

    reference = source.get("reference")
    if not isinstance(reference, str) or not re.fullmatch(
        r"(?:MVP|OPS)-\d{3,}", reference
    ):
        ERRORS.append("MVP Backlog source.reference must match MVP-NNN or OPS-NNN.")
        return

    backlog_path = ROOT / "docs" / "MVP_BACKLOG.md"
    if not backlog_path.is_file():
        ERRORS.append("MVP Backlog task requires docs/MVP_BACKLOG.md.")
        return

    backlog = backlog_path.read_text(encoding="utf-8")
    if not re.search(
        r"^>\s*状态：\s*(?:approved|in-progress|release-ready)\s*$",
        backlog,
        re.MULTILINE,
    ):
        ERRORS.append(
            "docs/MVP_BACKLOG.md must have approved, in-progress, "
            "or release-ready status."
        )

    matched_statuses: list[str] = []
    active_count = 0
    status_index = None
    for line in backlog.splitlines():
        if not line.lstrip().startswith("|"):
            status_index = None
            continue
        cells = split_markdown_row(line)
        if not cells:
            continue
        if cells[0] == "ID" and "状态" in cells:
            status_index = cells.index("状态")
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if status_index is None or status_index >= len(cells):
            continue
        if not re.fullmatch(r"(?:MVP|OPS)-\d{3,}", cells[0]):
            continue
        row_status = cells[status_index]
        if row_status == "active":
            active_count += 1
        if cells[0] == reference:
            matched_statuses.append(row_status)

    if not matched_statuses:
        ERRORS.append(f"Backlog source {reference} does not exist.")
        return
    if len(matched_statuses) > 1:
        ERRORS.append(f"Backlog source {reference} is duplicated.")
    selected_status = matched_statuses[-1]
    allowed_statuses = {"active", "verified"} if final else {"active"}
    if selected_status not in allowed_statuses:
        ERRORS.append(
            f"Backlog source {reference} must be "
            f"{'active or verified' if final else 'active'}."
        )
    expected_active_count = 1 if selected_status == "active" else 0
    if active_count != expected_active_count:
        ERRORS.append(
            "docs/MVP_BACKLOG.md contains an unexpected number of active items "
            "for the selected task state."
        )


def check_process_improvement(record: dict) -> None:
    retrospective = record.get("process_retrospective")
    if not isinstance(retrospective, dict):
        ERRORS.append("Task record requires a process_retrospective object.")
        return

    if retrospective.get("completed") is not True:
        ERRORS.append("Process retrospective must be completed before final gate.")
    for field in ("completed_by", "completed_at", "summary"):
        require_text(
            retrospective.get(field),
            f"process_retrospective.{field}",
        )

    question_fields = {
        "repeated_problem_found",
        "guidance_gap_found",
        "deterministic_check_candidate_found",
    }
    questions = retrospective.get("questions")
    if not isinstance(questions, dict) or set(questions) != question_fields:
        ERRORS.append(
            "Process retrospective questions are incomplete or contain extra fields."
        )
        positive_answer = False
    else:
        non_boolean = [
            field for field in question_fields if not isinstance(questions.get(field), bool)
        ]
        if non_boolean:
            ERRORS.append("Every process retrospective answer must be boolean.")
        positive_answer = any(questions.get(field) is True for field in question_fields)

    proposals = require_list(record.get("rule_proposals"), "rule_proposals")
    if positive_answer and not proposals:
        ERRORS.append(
            "A positive process retrospective answer requires a Rule Proposal."
        )
    if proposals and not positive_answer:
        ERRORS.append(
            "Rule Proposals require at least one positive retrospective answer."
        )

    proposal_ids: set[str] = set()
    permanent_targets = {"AGENTS.md", "CI", "DECISIONS.md"}
    final_statuses = {"recorded", "rejected", "deferred", "implemented"}

    for index, proposal in enumerate(proposals):
        prefix = f"rule_proposals[{index}]"
        if not isinstance(proposal, dict):
            ERRORS.append(f"{prefix} must be an object.")
            continue

        proposal_id = proposal.get("id")
        if not isinstance(proposal_id, str) or not re.fullmatch(
            r"RP-\d{3,}", proposal_id
        ):
            ERRORS.append(f"{prefix}.id must match RP-NNN.")
        elif proposal_id in proposal_ids:
            ERRORS.append(f"Duplicate Rule Proposal ID: {proposal_id}.")
        else:
            proposal_ids.add(proposal_id)

        if proposal.get("reported_by") not in {
            "developer",
            "reviewer",
            "coordinator",
        }:
            ERRORS.append(
                f"{prefix}.reported_by must be developer, reviewer, or coordinator."
            )
        for field in ("problem", "proposed_change"):
            require_text(proposal.get(field), f"{prefix}.{field}")

        evidence = proposal.get("evidence")
        if (
            not isinstance(evidence, list)
            or not evidence
            or not all(isinstance(item, str) and item.strip() for item in evidence)
        ):
            ERRORS.append(f"{prefix}.evidence must contain concrete text evidence.")

        recurrence = proposal.get("recurrence")
        if recurrence not in {"single", "repeated"}:
            ERRORS.append(f"{prefix}.recurrence must be single or repeated.")

        target_name = proposal.get("target")
        valid_target = (
            target_name == "task-only"
            or target_name in permanent_targets
            or (
                isinstance(target_name, str)
                and (
                    target_name.startswith("skill:")
                    or target_name.startswith("script:")
                )
                and bool(target_name.split(":", 1)[1].strip())
            )
        )
        if not valid_target:
            ERRORS.append(f"{prefix}.target is not a supported governance target.")

        status = proposal.get("status")
        if status not in final_statuses:
            ERRORS.append(
                f"{prefix}.status must be recorded, rejected, deferred, or implemented."
            )
        for field in ("decision_by", "decision_at", "decision_source"):
            require_text(proposal.get(field), f"{prefix}.{field}")

        decision_role = proposal.get("decision_role")
        if target_name == "task-only":
            if status != "recorded":
                ERRORS.append(f"{prefix} task-only proposals must be recorded.")
            if decision_role != "coordinator":
                ERRORS.append(
                    f"{prefix} task-only proposals must be triaged by the coordinator."
                )
            if recurrence == "repeated":
                ERRORS.append(
                    f"{prefix} repeated problems cannot be closed as task-only."
                )
        elif valid_target:
            if status == "recorded":
                ERRORS.append(
                    f"{prefix} permanent-target proposals cannot use recorded status."
                )
            if decision_role != "user":
                ERRORS.append(
                    f"{prefix} permanent-target proposals require a user decision."
                )

        implemented_in = proposal.get("implemented_in")
        if status == "implemented":
            if (
                not isinstance(implemented_in, list)
                or not implemented_in
                or not all(
                    isinstance(item, str) and item.strip() for item in implemented_in
                )
            ):
                ERRORS.append(
                    f"{prefix}.implemented_in must list the changed governance files."
                )
            else:
                for implemented_path in implemented_in:
                    if not (ROOT / implemented_path).exists():
                        ERRORS.append(
                            f"{prefix}.implemented_in path does not exist: "
                            f"{implemented_path}."
                        )
        elif implemented_in not in (None, []):
            ERRORS.append(
                f"{prefix}.implemented_in must be empty unless status is implemented."
            )


def check_task_record(record_path: str | None, final: bool) -> None:
    if not record_path:
        ERRORS.append("Gate mode requires a task-record path.")
        return

    target = (ROOT / record_path).resolve()
    runs_root = (ROOT / ".agent" / "runs").resolve()
    if runs_root not in target.parents:
        ERRORS.append("Task record must live under .agent/runs/.")
        return
    if not target.is_file():
        ERRORS.append(f"Task record does not exist: {target.relative_to(ROOT)}")
        return

    active_pointer = ROOT / ".agent" / "active-task"
    if not active_pointer.is_file():
        ERRORS.append("Missing .agent/active-task pointer.")
    else:
        active_value = active_pointer.read_text(encoding="utf-8").strip()
        if active_value != str(target.relative_to(ROOT)):
            ERRORS.append(".agent/active-task does not point to this task record.")

    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        ERRORS.append(f"Task record is invalid JSON: {exc}")
        return
    if not isinstance(record, dict):
        ERRORS.append("Task record root must be a JSON object.")
        return

    require_text(record.get("task_id"), "task_id")
    require_text(record.get("request"), "request")
    check_task_source(record, final)
    task_id = record.get("task_id")
    if isinstance(task_id, str) and target.stem != task_id:
        ERRORS.append("Task-record filename must match task_id.")
    allowed_statuses = (
        {"completed"} if final else {"planned", "authorized", "in_progress"}
    )
    if record.get("status") not in allowed_statuses:
        expected = ", ".join(sorted(allowed_statuses))
        ERRORS.append(f"Task record status must be one of: {expected}.")
    if final and record.get("status") != "completed":
        ERRORS.append("Task record status must be completed before final gate.")

    scope = require_object(record.get("scope"), "scope")
    require_string_list(scope.get("in"), "scope.in", non_empty=True)
    require_string_list(scope.get("out"), "scope.out", non_empty=True)

    authorization = require_object(
        record.get("implementation_authorization"),
        "implementation_authorization",
    )
    if authorization.get("authorized") is not True:
        ERRORS.append("Implementation authorization is missing.")
    for field in ("authorized_by", "authorized_at", "source"):
        require_text(authorization.get(field), f"implementation_authorization.{field}")

    planning = require_object(record.get("planning"), "planning")
    if planning.get("level") not in {"small", "medium", "large"}:
        ERRORS.append("Task record planning.level must be small, medium, or large.")
    require_string_list(planning.get("steps"), "planning.steps", non_empty=True)
    if planning.get("level") == "large":
        exec_plan = planning.get("exec_plan")
        require_text(exec_plan, "planning.exec_plan")
        if isinstance(exec_plan, str) and exec_plan.strip():
            plan_path = (ROOT / exec_plan).resolve()
            plans_root = (ROOT / ".agent" / "plans").resolve()
            if plans_root not in plan_path.parents or not plan_path.is_file():
                ERRORS.append(
                    "Large task planning.exec_plan must reference an existing file "
                    "under .agent/plans/."
                )

    risk = require_object(record.get("risk"), "risk")
    risk_fields = {
        "product_scope",
        "sensitive_data",
        "destructive_change",
        "irreversible_architecture",
        "production_release",
    }
    if set(risk) != risk_fields or not all(
        isinstance(risk.get(field), bool) for field in risk_fields
    ):
        ERRORS.append("Task record risk object is incomplete or contains non-booleans.")
    risk_requires_approval = any(risk.get(field) is True for field in risk_fields)

    base_commit = record.get("base_commit")
    require_text(base_commit, "base_commit")
    if isinstance(base_commit, str) and base_commit:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{base_commit}^{{commit}}"],
            cwd=ROOT,
            capture_output=True,
        )
        if result.returncode != 0:
            ERRORS.append("Task record base_commit is not a valid Git commit.")
        else:
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", base_commit, "HEAD"],
                cwd=ROOT,
                capture_output=True,
            )
            if result.returncode != 0:
                ERRORS.append(
                    "Task-record base_commit must remain an ancestor of current HEAD."
                )

    check_acceptance_criteria(record.get("acceptance"), final)

    if not final:
        if risk_requires_approval:
            approvals = require_list(record.get("human_approvals"), "human_approvals")
            if not approvals:
                ERRORS.append("Risk flags require human approval before implementation.")
            for index, approval in enumerate(approvals):
                if not isinstance(approval, dict):
                    ERRORS.append(
                        f"Task record human_approvals[{index}] must be an object."
                    )
                    continue
                for field in ("scope", "approved_by", "approved_at", "source"):
                    require_text(approval.get(field), f"human_approvals[].{field}")
        return

    developer = require_object(record.get("developer"), "developer")
    require_text(developer.get("agent_id"), "developer.agent_id")
    require_text(developer.get("worktree_hash"), "developer.worktree_hash")
    require_text(developer.get("handoff"), "developer.handoff")
    commands = require_list(developer.get("commands"), "developer.commands")
    if not commands:
        ERRORS.append("Task record requires Developer verification commands.")
    else:
        successful_commands = 0
        for index, command in enumerate(commands):
            if not isinstance(command, dict):
                ERRORS.append(
                    f"Task record developer.commands[{index}] must be an object."
                )
                continue
            require_text(command.get("command"), "developer.commands[].command")
            exit_code = command.get("exit_code")
            if not isinstance(exit_code, int):
                ERRORS.append("Developer command exit_code must be an integer.")
            elif exit_code == 0:
                successful_commands += 1
            elif command.get("expected_failure") is not True:
                ERRORS.append(
                    f"Developer command failed without expected_failure: "
                    f"{command.get('command', '<unknown>')}"
                )
            require_text(command.get("result"), "developer.commands[].result")
        if successful_commands == 0:
            ERRORS.append(
                "Task record requires at least one successful Developer "
                "verification command."
            )

    if isinstance(base_commit, str) and base_commit:
        try:
            current_hash = worktree_hash(base_commit)
        except ValueError as exc:
            ERRORS.append(str(exc))
            current_hash = None
    else:
        current_hash = None
    if current_hash and developer.get("worktree_hash") != current_hash:
        ERRORS.append("Developer worktree hash does not match the current worktree.")

    review = require_object(record.get("review"), "review")
    require_text(review.get("agent_id"), "review.agent_id")
    if review.get("agent_id") == developer.get("agent_id"):
        ERRORS.append("Developer and Reviewer agent IDs must be different.")
    if review.get("status") != "pass":
        ERRORS.append("Independent review status must be pass.")
    require_text(review.get("summary"), "review.summary")
    if current_hash and review.get("reviewed_worktree_hash") != current_hash:
        ERRORS.append("Reviewer did not review the current worktree hash.")
    findings = require_object(review.get("findings"), "review.findings")
    if not review.get("requirement_checklist"):
        ERRORS.append("Review must include an independently derived requirement checklist.")
    else:
        require_string_list(
            review.get("requirement_checklist"),
            "review.requirement_checklist",
            non_empty=True,
        )
    expected_priorities = {"p0", "p1", "p2", "p3"}
    if set(findings) != expected_priorities or any(
        not isinstance(findings.get(priority), int)
        or isinstance(findings.get(priority), bool)
        or findings.get(priority, -1) < 0
        for priority in expected_priorities
    ):
        ERRORS.append(
            "Review findings must contain non-negative integer p0, p1, p2, and p3."
        )
    for priority in ("p0", "p1"):
        if findings.get(priority, 0) != 0:
            ERRORS.append(f"Unresolved {priority.upper()} findings block completion.")
    p2_count = findings.get("p2", 0)
    accepted_findings = require_list(
        review.get("accepted_findings"),
        "review.accepted_findings",
    )
    if isinstance(p2_count, int) and p2_count and len(accepted_findings) < p2_count:
        ERRORS.append("Every unresolved P2 finding requires explicit acceptance.")
    accepted_ids: set[str] = set()
    for index, accepted in enumerate(accepted_findings):
        if not isinstance(accepted, dict):
            ERRORS.append(
                f"Task record review.accepted_findings[{index}] must be an object."
            )
            continue
        for field in ("finding_id", "accepted_by", "accepted_at", "source"):
            require_text(accepted.get(field), f"review.accepted_findings[].{field}")
        finding_id = accepted.get("finding_id")
        if isinstance(finding_id, str):
            if finding_id in accepted_ids:
                ERRORS.append(f"Duplicate accepted finding ID: {finding_id}.")
            accepted_ids.add(finding_id)

    approvals = require_list(record.get("human_approvals"), "human_approvals")
    if risk_requires_approval and not approvals:
        ERRORS.append("Risk flags require human approval.")
    for index, approval in enumerate(approvals):
        if not isinstance(approval, dict):
            ERRORS.append(f"Task record human_approvals[{index}] must be an object.")
            continue
        for field in ("scope", "approved_by", "approved_at", "source"):
            require_text(approval.get(field), f"human_approvals[].{field}")

    require_string_list(record.get("remaining_risks"), "remaining_risks")
    check_process_improvement(record)


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


def git_scannable_paths() -> list[Path]:
    try:
        output = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        ERRORS.append("Unable to enumerate Git-tracked and unignored files.")
        return []

    paths: list[Path] = []
    for raw_path in sorted(set(item for item in output.split(b"\0") if item)):
        relative_path = raw_path.decode("utf-8", errors="surrogateescape")
        target = ROOT / relative_path
        try:
            if target.is_symlink():
                continue
            if not target.is_file() or target.stat().st_size > 1_000_000:
                continue
        except OSError:
            continue
        paths.append(target)
    return paths


def iter_text_files():
    for target in git_scannable_paths():
        try:
            data = target.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        try:
            yield target, data.decode("utf-8")
        except UnicodeDecodeError:
            continue


def scan_for_secrets() -> None:
    patterns = [
        ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
        ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
        ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
        (
            "assigned credential",
            re.compile(
                r"""\b(?:api[_-]?key|access[_-]?token|secret|password)\b
                    \s*[:=]\s*["'`](?!example|dummy|test|changeme|<)
                    [^"'`\s]{12,}""",
                re.IGNORECASE | re.VERBOSE,
            ),
        ),
    ]

    for file_path, content in iter_text_files():
        for label, pattern in patterns:
            if pattern.search(content):
                ERRORS.append(
                    f"Possible {label} in {file_path.relative_to(ROOT)}."
                )


def check_session_heartbeat() -> None:
    heartbeat = ROOT / ".codex-log" / "last-session-check.json"
    if not heartbeat.is_file():
        WARNINGS.append(
            "SessionStart heartbeat is missing; review and trust project hooks, "
            "then start a new Codex session."
        )
        return
    try:
        payload = json.loads(heartbeat.read_text(encoding="utf-8"))
        checked_at = payload.get("checked_at_unix")
    except (OSError, json.JSONDecodeError):
        WARNINGS.append("SessionStart heartbeat is unreadable or invalid.")
        return
    if not isinstance(checked_at, int):
        WARNINGS.append("SessionStart heartbeat has no valid timestamp.")
        return
    if time.time() - checked_at > 86_400:
        WARNINGS.append(
            "SessionStart heartbeat is older than 24 hours; start a new Codex "
            "session to confirm the Hook still runs."
        )


def main() -> None:
    check_governance_documents()
    check_skills()
    check_codex_configuration()
    if not has_head():
        WARNINGS.append(
            "Repository has no baseline commit; code implementation and snapshot/gate "
            "modes are unavailable until a human-approved initial commit exists."
        )
    if MODE in {"stop", "manual", "gate", "preflight"}:
        scan_for_secrets()
    if MODE == "manual":
        check_session_heartbeat()
    if MODE == "preflight":
        check_task_record(MODE_ARGUMENT, final=False)
    if MODE == "gate":
        check_task_record(MODE_ARGUMENT, final=True)

    if MODE == "start" and not ERRORS:
        heartbeat = ROOT / ".codex-log" / "last-session-check.json"
        try:
            heartbeat.parent.mkdir(parents=True, exist_ok=True)
            heartbeat.write_text(
                json.dumps(
                    {
                        "checked_at_unix": int(time.time()),
                        "repository": str(ROOT),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            WARNINGS.append(f"Unable to write SessionStart heartbeat: {exc}")

    for warning in WARNINGS:
        print(f"[workflow-check] WARN: {warning}", file=sys.stderr)
    for error in ERRORS:
        print(f"[workflow-check] ERROR: {error}", file=sys.stderr)

    if ERRORS:
        suffix = "" if len(ERRORS) == 1 else "s"
        print(
            f"[workflow-check] FAIL ({len(ERRORS)} error{suffix})", file=sys.stderr
        )
        raise SystemExit(1)

    if MODE == "snapshot":
        try:
            base_commit = task_record_base_commit(MODE_ARGUMENT)
            print(f"WORKTREE_SHA256={worktree_hash(base_commit)}")
        except ValueError as exc:
            print(f"[workflow-check] ERROR: {exc}", file=sys.stderr)
            raise SystemExit(1)

    suffix = "" if len(WARNINGS) == 1 else "s"
    print(f"[workflow-check] PASS ({MODE}; {len(WARNINGS)} warning{suffix})")


if __name__ == "__main__":
    main()
