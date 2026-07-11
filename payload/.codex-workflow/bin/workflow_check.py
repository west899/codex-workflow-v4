#!/usr/bin/env python3
"""Deterministic read-side gates for Codex Workflow V3."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from workflow_common import (
    WorkflowDataError,
    allowed_path,
    backlog_rows,
    canonical_delivery,
    closeout_state_fingerprint,
    current_branch,
    git,
    is_ancestor,
    is_mutable_control_path,
    load_record,
    read_embedded_json,
    read_requirements_brief,
    rev_parse,
    snapshot_id,
    status_paths,
    utc_now,
)
from workflow_lock import lock_probe
from workflow_paths import WorkflowPathError, WorkflowPaths, atomic_write_json


PLACEHOLDER = re.compile(r"(?:<[^>]+>|\b(?:TBD|TODO|placeholder)\b|\.\.\.)", re.IGNORECASE)
BASELINE_MARKER = "CODEX_REQUIREMENTS_BASELINE"


class Checks:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def require_text(self, value: Any, name: str) -> str | None:
        if not isinstance(value, str) or not value.strip():
            self.error(f"{name} must be non-empty text.")
            return None
        if PLACEHOLDER.search(value):
            self.error(f"{name} contains a placeholder.")
        return value

    def finish(self, mode: str) -> None:
        for warning in self.warnings:
            print(f"[workflow-check] WARN: {warning}", file=sys.stderr)
        for error in self.errors:
            print(f"[workflow-check] ERROR: {error}", file=sys.stderr)
        if self.errors:
            print(f"[workflow-check] FAIL ({len(self.errors)} errors)", file=sys.stderr)
            raise SystemExit(1)
        suffix = "" if len(self.warnings) == 1 else "s"
        print(f"[workflow-check] PASS ({mode}; {len(self.warnings)} warning{suffix})")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Check Codex Workflow V3 state.")
    result.add_argument(
        "mode",
        choices=(
            "start", "manual", "stop", "preflight", "snapshot", "gate",
            "requirements-snapshot", "requirements-gate", "integration-preflight",
            "closeout-gate",
        ),
    )
    result.add_argument("target", nargs="?")
    result.add_argument("--json", action="store_true", dest="as_json")
    return result


def _objects(value: Any, checks: Checks, name: str, *, non_empty: bool = True) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (non_empty and not value):
        checks.error(f"{name} must be a non-empty array." if non_empty else f"{name} must be an array.")
        return []
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            checks.error(f"{name}[{index}] must be an object.")
        else:
            result.append(item)
    return result


def _strings(value: Any, checks: Checks, name: str, *, non_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (non_empty and not value):
        checks.error(f"{name} must be a non-empty array." if non_empty else f"{name} must be an array.")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        text = checks.require_text(item, f"{name}[{index}]")
        if text:
            result.append(text)
    return result


def requirements_gate(path: Path, checks: Checks) -> Any:
    try:
        brief = read_requirements_brief(path)
    except (OSError, WorkflowDataError) as exc:
        checks.error(str(exc))
        return None
    data = brief.metadata
    if data.get("schema_version") != 1:
        checks.error("Requirements schema_version must be 1.")
    checks.require_text(data.get("brief_id"), "requirements.brief_id")
    if not isinstance(data.get("revision"), int) or data.get("revision", 0) < 1:
        checks.error("requirements.revision must be an integer >= 1.")
    checks.require_text(data.get("target_release"), "requirements.target_release")
    if data.get("status") != "approved":
        checks.error("Requirements brief status must be approved.")

    requirements = data.get("requirements")
    if not isinstance(requirements, dict):
        checks.error("requirements.requirements must be an object.")
        requirements = {}
    scope = requirements.get("scope")
    if not isinstance(scope, dict):
        checks.error("requirements.scope must be an object.")
        scope = {}
    _strings(scope.get("in"), checks, "requirements.scope.in")
    _strings(scope.get("out"), checks, "requirements.scope.out")

    users = _objects(requirements.get("users"), checks, "requirements.users")
    outcomes = _objects(requirements.get("outcomes"), checks, "requirements.outcomes")
    flows = _objects(requirements.get("flows"), checks, "requirements.flows")
    capabilities = _objects(requirements.get("capabilities"), checks, "requirements.capabilities")
    scenarios = _objects(requirements.get("scenarios"), checks, "requirements.scenarios")
    non_goals = _objects(requirements.get("non_goals"), checks, "requirements.non_goals")
    questions = _objects(requirements.get("open_questions", []), checks, "requirements.open_questions", non_empty=False)
    rounds = _objects(requirements.get("calibration_rounds"), checks, "requirements.calibration_rounds")
    if not isinstance(requirements.get("constraints", []), list):
        checks.error("requirements.constraints must be an array.")

    identifiers: set[str] = set()
    for group_name, group in (
        ("users", users), ("outcomes", outcomes), ("flows", flows),
        ("capabilities", capabilities), ("scenarios", scenarios),
        ("non_goals", non_goals), ("open_questions", questions),
        ("calibration_rounds", rounds),
    ):
        for index, item in enumerate(group):
            item_id = checks.require_text(item.get("id"), f"requirements.{group_name}[{index}].id")
            if item_id in identifiers:
                checks.error(f"Duplicate requirements ID: {item_id}.")
            if item_id:
                identifiers.add(item_id)

    for name, group in (("users", users), ("outcomes", outcomes), ("non_goals", non_goals)):
        for index, item in enumerate(group):
            checks.require_text(item.get("text"), f"requirements.{name}[{index}].text")
            if item.get("status") != "confirmed":
                checks.error(f"requirements.{name}[{index}] must be confirmed.")
            checks.require_text(item.get("source"), f"requirements.{name}[{index}].source")

    for index, flow in enumerate(flows):
        _strings(flow.get("steps"), checks, f"requirements.flows[{index}].steps")
        if flow.get("status") != "confirmed":
            checks.error(f"requirements.flows[{index}] must be confirmed.")
        checks.require_text(flow.get("source"), f"requirements.flows[{index}].source")

    must_count = 0
    for index, capability in enumerate(capabilities):
        priority = capability.get("priority")
        if priority not in {"must", "should", "could"}:
            checks.error(f"requirements.capabilities[{index}].priority is invalid.")
        if priority == "must":
            must_count += 1
            if capability.get("status") != "confirmed":
                checks.error(f"Must capability {capability.get('id')} must be confirmed.")
            source = checks.require_text(capability.get("source"), f"requirements.capabilities[{index}].source")
            if source and source.lower().startswith(("ai", "assumption")):
                checks.error(f"Must capability {capability.get('id')} cannot use an AI-assumption source.")
        for name in ("preconditions", "inputs", "boundaries"):
            _strings(capability.get(name, []), checks, f"requirements.capabilities[{index}].{name}", non_empty=False)
        checks.require_text(capability.get("action"), f"requirements.capabilities[{index}].action")
        checks.require_text(capability.get("observable_result"), f"requirements.capabilities[{index}].observable_result")
    if must_count == 0:
        checks.error("Requirements brief must contain at least one Must capability.")

    scenario_types: set[str] = set()
    for index, scenario in enumerate(scenarios):
        scenario_type = scenario.get("type")
        if scenario_type not in {"happy", "boundary", "failure", "non_goal"}:
            checks.error(f"requirements.scenarios[{index}].type is invalid.")
        else:
            scenario_types.add(scenario_type)
        applicability = scenario.get("applicability")
        if applicability not in {"required", "not_applicable"}:
            checks.error(f"requirements.scenarios[{index}].applicability is invalid.")
        if applicability == "not_applicable":
            checks.require_text(scenario.get("na_reason"), f"requirements.scenarios[{index}].na_reason")
            checks.require_text(scenario.get("na_source"), f"requirements.scenarios[{index}].na_source")
        else:
            checks.require_text(scenario.get("expected"), f"requirements.scenarios[{index}].expected")
        if scenario.get("status") != "confirmed":
            checks.error(f"requirements.scenarios[{index}] must be confirmed.")
        checks.require_text(scenario.get("source"), f"requirements.scenarios[{index}].source")
    if "happy" not in scenario_types:
        checks.error("Requirements brief needs a happy-path scenario.")
    if not ({"boundary", "failure"} & scenario_types):
        checks.error("Requirements brief needs a boundary or failure scenario.")
    if "non_goal" not in scenario_types:
        checks.error("Requirements brief needs a non-goal scenario.")

    for index, question in enumerate(questions):
        blocking = question.get("blocking")
        if not isinstance(blocking, bool):
            checks.error(f"requirements.open_questions[{index}].blocking must be boolean.")
        status = question.get("status")
        if blocking and status != "resolved":
            checks.error(f"Blocking question {question.get('id')} is unresolved.")
        if status == "resolved":
            for field in ("resolution", "resolved_by", "resolved_source"):
                checks.require_text(question.get(field), f"requirements.open_questions[{index}].{field}")
        elif status == "open" and not blocking:
            checks.require_text(question.get("owner"), f"requirements.open_questions[{index}].owner")
            checks.require_text(question.get("resolution_target"), f"requirements.open_questions[{index}].resolution_target")
        elif status not in {"open", "resolved"}:
            checks.error(f"requirements.open_questions[{index}].status is invalid.")
    if rounds and rounds[-1].get("result") != "confirmed":
        checks.error("The final calibration round must be confirmed.")
    for index, round_item in enumerate(rounds):
        if round_item.get("result") not in {"confirmed", "corrected"}:
            checks.error(f"requirements.calibration_rounds[{index}].result is invalid.")
        checks.require_text(round_item.get("source"), f"requirements.calibration_rounds[{index}].source")
        if not isinstance(round_item.get("changed_requirement_ids", []), list):
            checks.error(f"requirements.calibration_rounds[{index}].changed_requirement_ids must be an array.")

    approval = data.get("approval")
    if not isinstance(approval, dict):
        checks.error("requirements.approval must be an object.")
        approval = {}
    for field in ("approved_by", "approved_at", "source"):
        checks.require_text(approval.get(field), f"requirements.approval.{field}")
    approved = approval.get("approved_fingerprint")
    if approved != brief.fingerprint:
        checks.error(
            f"Requirements approval fingerprint is stale; current fingerprint is {brief.fingerprint}."
        )
    if not brief.markdown.strip():
        checks.error("Requirements human-readable Markdown must not be empty.")
    return brief


def check_governance(paths: WorkflowPaths, checks: Checks) -> None:
    for key in ("protocol", "project_rules", "project", "plan", "decisions", "backlog", "workflow_doc"):
        target = paths.tracked(key)
        if not target.is_file():
            checks.error(f"Missing required workflow file: {paths.relative(target)}")
    root_agents = paths.root / "AGENTS.md"
    if not root_agents.is_file():
        checks.error("Missing root AGENTS.md discovery entry.")
    else:
        text = root_agents.read_text(encoding="utf-8")
        if text.count("BEGIN CODEX WORKFLOW ENTRY") != 1 or text.count("END CODEX WORKFLOW ENTRY") != 1:
            checks.error("Root AGENTS.md must contain exactly one V3 managed entry block.")

    backlog = paths.tracked("backlog")
    if backlog.is_file():
        text = backlog.read_text(encoding="utf-8")
        try:
            metadata = read_embedded_json(text, BASELINE_MARKER)
        except WorkflowDataError as exc:
            checks.error(str(exc))
            metadata = {}
        if metadata.get("workflow_schema_version") != 3:
            checks.error("Backlog workflow_schema_version must be 3.")
        if metadata.get("requirements_gate_mode") not in {"legacy_warn", "required"}:
            checks.error("Backlog requirements_gate_mode is invalid.")
        gate_mode = metadata.get("requirements_gate_mode")
        rows = backlog_rows(text)
        ids = [row["id"] for row in rows]
        if len(ids) != len(set(ids)):
            checks.error("Backlog task IDs must be unique.")
        allowed = {"draft", "blocked", "ready", "done", "removed"}
        if gate_mode == "legacy_warn":
            allowed.add("verified")
        for row in rows:
            if row["status"] not in allowed:
                checks.error(f"Backlog {row['id']} has invalid durable status {row['status']!r}.")
            if row["status"] == "verified":
                checks.warn(
                    f"Backlog {row['id']} is legacy verified; bind exact integration evidence and reconcile before done."
                )
            if row["blocking_type"] != "dependencies" and row["blocking_type"] != "none" and not row["blocking_type"].startswith("manual:"):
                checks.error(f"Backlog {row['id']} has invalid blocking type.")
        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {row["id"]: row for row in rows}

        def visit(task_id: str) -> None:
            if task_id in visiting:
                checks.error(f"Backlog dependency cycle includes {task_id}.")
                return
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in by_id[task_id]["dependencies"]:
                if dependency not in by_id:
                    checks.error(f"Backlog {task_id} references missing dependency {dependency}.")
                else:
                    visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in by_id:
            visit(task_id)


def _baseline(paths: WorkflowPaths, checks: Checks) -> dict[str, Any] | None:
    values: list[tuple[str, dict[str, Any]]] = []
    for key in ("project", "backlog"):
        path = paths.tracked(key)
        if not path.is_file():
            continue
        try:
            values.append((key, read_embedded_json(path.read_text(encoding="utf-8"), BASELINE_MARKER)))
        except WorkflowDataError as exc:
            checks.error(f"{key}: {exc}")
    if len(values) != 2:
        return None
    project_value, backlog_value = values[0][1], values[1][1]
    fields = ("brief_id", "revision", "approval_fingerprint")
    if any(project_value.get(field) != backlog_value.get(field) for field in fields):
        checks.error("PROJECT and Backlog requirements baselines do not match.")
    return backlog_value


def _validate_record_basics(paths: WorkflowPaths, record: dict[str, Any], checks: Checks, *, final: bool) -> None:
    if record.get("version") != 3:
        checks.error("Task record version must be 3 for V3 commands.")
        return
    if not isinstance(record.get("generation"), int) or record.get("generation", -1) < 0:
        checks.error("Task record generation must be a non-negative integer.")
    if record.get("phase") not in {"coordinator", "developer", "review", "integration"}:
        checks.error("Task record phase is invalid.")
    task_id = checks.require_text(record.get("task_id"), "task.task_id")
    allowed_status = {"planned", "authorized", "in_progress", "completed"}
    if record.get("status") not in allowed_status:
        checks.error("Task status is invalid.")
    if final and record.get("status") != "completed":
        checks.error("Final gate requires task.status=completed.")
    checks.require_text(record.get("request"), "task.request")
    source = record.get("source")
    if not isinstance(source, dict):
        checks.error("task.source must be an object.")
        source = {}
    source_type = source.get("type")
    if source_type not in {"mvp_backlog", "incident", "maintenance", "user_directive"}:
        checks.error("task.source.type is invalid.")
    checks.require_text(source.get("reference"), "task.source.reference")
    checks.require_text(source.get("priority_reason"), "task.source.priority_reason")
    scope = record.get("scope")
    if not isinstance(scope, dict):
        checks.error("task.scope must be an object.")
        scope = {}
    _strings(scope.get("in"), checks, "task.scope.in")
    _strings(scope.get("out"), checks, "task.scope.out")
    allowed_paths = _strings(scope.get("allowed_paths"), checks, "task.scope.allowed_paths")
    resource_keys = _strings(scope.get("resource_keys"), checks, "task.scope.resource_keys")
    authorization = record.get("implementation_authorization")
    if not isinstance(authorization, dict) or authorization.get("authorized") is not True:
        checks.error("Task requires explicit implementation authorization.")
    else:
        for field in ("authorized_by", "authorized_at", "source"):
            checks.require_text(authorization.get(field), f"task.implementation_authorization.{field}")
    base_commit = record.get("base_commit")
    if not isinstance(base_commit, str):
        checks.error("task.base_commit must be a Git object ID.")
    else:
        try:
            resolved_base = rev_parse(paths, base_commit)
            if resolved_base != base_commit:
                checks.error("task.base_commit must be the full exact object ID.")
        except WorkflowDataError as exc:
            checks.error(str(exc))

    acceptance = _objects(record.get("acceptance"), checks, "task.acceptance")
    for index, item in enumerate(acceptance):
        checks.require_text(item.get("id"), f"task.acceptance[{index}].id")
        checks.require_text(item.get("criterion"), f"task.acceptance[{index}].criterion")
        if final:
            if item.get("status") != "passed":
                checks.error(f"Acceptance {item.get('id')} has not passed.")
            if not isinstance(item.get("evidence"), list) or not item.get("evidence"):
                checks.error(f"Acceptance {item.get('id')} lacks evidence.")

    lane = record.get("lane")
    if not isinstance(lane, dict):
        checks.error("task.lane must be an object.")
        return
    mode = lane.get("mode")
    if mode not in {"single", "local_worktree", "remote_preassigned", "remote_claimed"}:
        checks.error("task.lane.mode is invalid.")
    for field in ("lane_id", "branch", "base_ref", "base_commit", "claim_id"):
        checks.require_text(lane.get(field), f"task.lane.{field}")
    if lane.get("base_commit") != record.get("base_commit"):
        checks.error("task.lane.base_commit must equal task.base_commit.")
    if not isinstance(lane.get("owner_generation"), int) or lane.get("owner_generation", 0) < 1:
        checks.error("task.lane.owner_generation must be >= 1.")
    if lane.get("allowed_paths") != allowed_paths or lane.get("resource_keys") != resource_keys:
        checks.error("Lane scope must exactly match task scope paths and resources.")
    assignment = lane.get("assignment")
    if not isinstance(assignment, dict):
        checks.error("task.lane.assignment must be an object.")
    elif assignment.get("assignment_generation") != lane.get("owner_generation"):
        checks.error("Assignment generation must equal lane owner generation.")
    branch = current_branch(paths)
    if branch != lane.get("branch"):
        checks.error(f"Current branch {branch!r} does not match lane branch {lane.get('branch')!r}.")

    if mode != "single":
        pointer = paths.lane_runtime / "lane.json"
        try:
            pointer_data = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            checks.error("Current worktree has no readable V3 lane pointer.")
        else:
            for field in ("lane_id", "task_id", "claim_id", "owner_generation", "branch"):
                expected = task_id if field == "task_id" else lane.get(field)
                if pointer_data.get(field) != expected:
                    checks.error(f"Lane pointer {field} does not match task record.")

    if source_type == "mvp_backlog":
        baseline = _baseline(paths, checks)
        source_baseline = source.get("requirements_baseline")
        if not isinstance(source_baseline, dict):
            checks.error("Backlog tasks require source.requirements_baseline.")
        elif baseline:
            for field in ("brief_id", "revision", "approval_fingerprint"):
                if source_baseline.get(field) != baseline.get(field):
                    checks.error(f"Task requirements baseline field {field} is stale.")
            brief_id = baseline.get("brief_id")
            if brief_id:
                requirements_gate(paths.tracked("requirements") / f"{brief_id}.md", checks)


def _delivery_snapshot(paths: WorkflowPaths, record: dict[str, Any], checks: Checks) -> dict[str, Any] | None:
    verification = record.get("verification")
    if not isinstance(verification, dict):
        checks.error("task.verification must be an object.")
        return None
    commit = verification.get("delivery_commit")
    if not isinstance(commit, str):
        checks.error("verification.delivery_commit must seal the delivery.")
        return None
    try:
        if rev_parse(paths, commit) != commit:
            checks.error("verification.delivery_commit must be an exact full object ID.")
            return None
        delivery = canonical_delivery(paths, record.get("base_commit"), commit)
    except WorkflowDataError as exc:
        checks.error(str(exc))
        return None
    computed_snapshot = snapshot_id(record, delivery["delivery_hash"])
    delivery["snapshot_id"] = computed_snapshot
    for field in ("delivery_hash", "patch_hash", "changed_paths"):
        if verification.get(field) != delivery[field]:
            checks.error(f"verification.{field} does not match the canonical delivery.")
    if verification.get("snapshot_id") != computed_snapshot:
        checks.error("verification.snapshot_id does not match the canonical delivery.")
    lane = record.get("lane") or {}
    patterns = lane.get("allowed_paths") or []
    for path in delivery["changed_paths"]:
        if not allowed_path(path, patterns):
            checks.error(f"Delivery path is outside the lane allowlist: {path}")
    return delivery


def task_gate(paths: WorkflowPaths, record: dict[str, Any], checks: Checks, *, final: bool) -> dict[str, Any] | None:
    _validate_record_basics(paths, record, checks, final=final)
    if not final:
        return None
    delivery = _delivery_snapshot(paths, record, checks)
    if delivery is None:
        return None
    verification = record["verification"]
    commit = verification["delivery_commit"]
    try:
        if rev_parse(paths, "HEAD") != commit:
            checks.error("Final gate must run at the exact sealed delivery commit.")
    except WorkflowDataError as exc:
        checks.error(str(exc))
    record_path_prefix = ".codex-workflow/state/runs/"
    for path in status_paths(paths):
        if not is_mutable_control_path(path) and not path.startswith(record_path_prefix):
            checks.error(f"Uncommitted delivery change is outside mutable workflow state: {path}")

    developer = record.get("developer")
    if not isinstance(developer, dict):
        checks.error("task.developer must be an object.")
        developer = {}
    developer_id = checks.require_text(developer.get("agent_id"), "task.developer.agent_id")
    if developer.get("snapshot_id") != delivery["snapshot_id"]:
        checks.error("Developer evidence is not bound to the sealed snapshot.")
    commands = _objects(developer.get("commands"), checks, "task.developer.commands")
    successes = 0
    for index, command in enumerate(commands):
        checks.require_text(command.get("command"), f"task.developer.commands[{index}].command")
        if command.get("exit_code") == 0:
            successes += 1
        elif command.get("expected_failure") is not True:
            checks.error(f"Developer command failed: {command.get('command')}")
        checks.require_text(command.get("result"), f"task.developer.commands[{index}].result")
    if successes == 0:
        checks.error("Developer evidence requires at least one successful command.")
    checks.require_text(developer.get("handoff"), "task.developer.handoff")

    review = record.get("review")
    if not isinstance(review, dict):
        checks.error("task.review must be an object.")
        review = {}
    reviewer_id = checks.require_text(review.get("agent_id"), "task.review.agent_id")
    if reviewer_id and reviewer_id == developer_id:
        checks.error("Developer and Reviewer agent IDs must differ.")
    if review.get("status") != "pass":
        checks.error("Independent review status must be pass.")
    if review.get("snapshot_id") != delivery["snapshot_id"]:
        checks.error("Reviewer evidence is not bound to the sealed snapshot.")
    _strings(review.get("requirement_checklist"), checks, "task.review.requirement_checklist")
    checks.require_text(review.get("summary"), "task.review.summary")
    findings = review.get("findings")
    if not isinstance(findings, dict) or set(findings) != {"p0", "p1", "p2", "p3"}:
        checks.error("Review findings must contain p0, p1, p2, and p3.")
    else:
        if findings.get("p0") != 0 or findings.get("p1") != 0:
            checks.error("Unresolved P0/P1 findings block the gate.")
        accepted = review.get("accepted_findings")
        if not isinstance(accepted, list) or len(accepted) < int(findings.get("p2", 0)):
            checks.error("Every unresolved P2 finding requires explicit acceptance.")

    retrospective = record.get("process_retrospective")
    if not isinstance(retrospective, dict) or retrospective.get("completed") is not True:
        checks.error("Process retrospective must be completed.")
    proposals = record.get("rule_proposals")
    if not isinstance(proposals, list):
        checks.error("task.rule_proposals must be an array.")
    else:
        for proposal in proposals:
            if not isinstance(proposal, dict) or proposal.get("status") not in {"recorded", "rejected", "deferred", "implemented"}:
                checks.error("Every rule proposal needs a final disposition.")
    return delivery


def integration_preflight(paths: WorkflowPaths, record: dict[str, Any], checks: Checks) -> None:
    task_gate(paths, record, checks, final=True)
    verification = record.get("verification") or {}
    integration = record.get("integration") or {}
    if verification.get("status") != "passed":
        checks.error("Integration requires verification.status=passed.")
    if integration.get("status") not in {"pending", "queued", "merged_pending_closeout"}:
        checks.error("Integration status is not ready for integration.")
    mode = integration.get("mode")
    if mode not in {"local_bootstrap", "remote_pr_ci"}:
        checks.error("Integration mode has not been prepared.")
    if mode == "local_bootstrap":
        target_ref = integration.get("target_ref")
        snapshot = verification.get("snapshot_id")
        approvals = record.get("human_approvals") or []
        matches = [
            approval for approval in approvals
            if isinstance(approval, dict)
            and approval.get("kind") == "local_bootstrap"
            and approval.get("task_id") == record.get("task_id")
            and approval.get("target_ref") == target_ref
            and approval.get("snapshot_id") == snapshot
            and approval.get("delivery_hash") == verification.get("delivery_hash")
        ]
        if not matches:
            checks.error("Local bootstrap lacks an exact snapshot-bound human approval.")


def closeout_gate(paths: WorkflowPaths, record: dict[str, Any], checks: Checks) -> None:
    integration = record.get("integration")
    if not isinstance(integration, dict):
        checks.error("task.integration must be an object.")
        return
    if integration.get("status") not in {"merged_pending_closeout", "integrated"}:
        checks.error("Closeout gate requires merged_pending_closeout or integrated state.")
    for field in ("target_ref", "result_commit", "closeout_commit", "closeout_state_fingerprint"):
        checks.require_text(integration.get(field), f"task.integration.{field}")
    target_ref = integration.get("target_ref")
    closeout_commit = integration.get("closeout_commit")
    if isinstance(target_ref, str) and isinstance(closeout_commit, str):
        try:
            target_oid = rev_parse(paths, target_ref)
            if not is_ancestor(paths, closeout_commit, target_oid):
                checks.error("Target ref does not contain the exact closeout commit.")
        except WorkflowDataError as exc:
            checks.error(str(exc))


def main() -> None:
    args = parser().parse_args()
    checks = Checks()
    try:
        paths = WorkflowPaths.discover(Path.cwd())
    except (WorkflowPathError, OSError) as exc:
        raise SystemExit(f"[workflow-check] ERROR: {exc}") from exc

    check_governance(paths, checks)
    mode = args.mode
    payload: dict[str, Any] | None = None

    if mode == "start":
        try:
            paths.ensure_runtime()
            atomic_write_json(
                paths.shared_runtime / "audit" / "last-session-check.json",
                {"checked_at": utc_now(), "worktree": str(paths.root)},
            )
        except OSError as exc:
            checks.warn(f"Unable to write SessionStart heartbeat: {exc}")
    elif mode == "manual":
        if paths.layout.get("parallel", {}).get("mode") == "local_worktree":
            try:
                paths.ensure_runtime()
                lock_probe(paths.shared_runtime / "locks" / "advisory-probe.lock")
            except Exception as exc:
                checks.error(str(exc))
    elif mode in {"requirements-snapshot", "requirements-gate"}:
        if not args.target:
            checks.error(f"{mode} requires a requirements brief path.")
        else:
            candidate = Path(args.target)
            if not candidate.is_absolute():
                candidate = paths.root / candidate
            try:
                candidate = candidate.resolve()
                requirements_root = paths.tracked("requirements")
                if requirements_root not in candidate.parents:
                    raise WorkflowPathError("Requirements brief must be under the configured requirements directory.")
                brief = read_requirements_brief(candidate)
                payload = {
                    "brief_id": brief.metadata.get("brief_id"),
                    "revision": brief.metadata.get("revision"),
                    "target_release": brief.metadata.get("target_release"),
                    "fingerprint": brief.fingerprint,
                }
                if mode == "requirements-gate":
                    requirements_gate(candidate, checks)
            except (OSError, WorkflowDataError, WorkflowPathError) as exc:
                checks.error(str(exc))
    elif mode in {"preflight", "snapshot", "gate", "integration-preflight", "closeout-gate"}:
        if not args.target:
            checks.error(f"{mode} requires a task-record path.")
        else:
            try:
                _, record = load_record(paths, args.target)
            except WorkflowDataError as exc:
                checks.error(str(exc))
                record = None
            if record is not None:
                if record.get("version") == 2:
                    if mode in {"preflight", "gate"}:
                        checks.warn("Legacy V2 record accepted for historical compatibility; it cannot auto-closeout.")
                        if record.get("status") not in {"planned", "authorized", "in_progress", "completed"}:
                            checks.error("Legacy task status is invalid.")
                        if mode == "gate" and record.get("status") != "completed":
                            checks.error("Legacy final gate requires completed status.")
                    else:
                        checks.error(f"{mode} is unavailable for legacy V2 records.")
                elif mode == "preflight":
                    task_gate(paths, record, checks, final=False)
                elif mode == "snapshot":
                    _validate_record_basics(paths, record, checks, final=False)
                    try:
                        head = rev_parse(paths, "HEAD")
                        delivery = canonical_delivery(paths, record.get("base_commit"), head)
                        delivery["snapshot_id"] = snapshot_id(record, delivery["delivery_hash"])
                        payload = delivery
                    except WorkflowDataError as exc:
                        checks.error(str(exc))
                elif mode == "gate":
                    delivery = task_gate(paths, record, checks, final=True)
                    if delivery:
                        payload = delivery
                elif mode == "integration-preflight":
                    integration_preflight(paths, record, checks)
                elif mode == "closeout-gate":
                    closeout_gate(paths, record, checks)

    if payload is not None:
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        elif mode == "requirements-snapshot":
            print(f"REQUIREMENTS_SHA256={payload['fingerprint']}")
        elif "snapshot_id" in payload:
            print(f"DELIVERY_SHA256={payload['delivery_hash']}")
            print(f"PATCH_SHA256={payload['patch_hash']}")
            print(f"SNAPSHOT_ID={payload['snapshot_id']}")
    checks.finish(mode)


if __name__ == "__main__":
    main()
