#!/usr/bin/env python3
"""Deterministic read-side gates for Workflow V3 and Phase A V4."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

# ``doctor`` promises that an ordinary Python invocation will not create local
# bytecode.  Set this before importing package-local modules; executing this
# file as ``__main__`` does not cache the file itself.
sys.dont_write_bytecode = True

from workflow_common import (
    WorkflowDataError,
    WorkflowJSONError,
    WorkflowJSONResourceError,
    WorkflowJSONSyntaxError,
    allowed_path,
    architecture_baseline_fingerprint,
    backlog_rows,
    maybe_load_guardrail_registry,
    maybe_validate_v4_backlog_focus,
    canonical_delivery,
    closeout_state_fingerprint,
    current_requirements_baseline,
    current_branch,
    derive_v4_decision_blocking,
    git,
    is_ancestor,
    is_mutable_control_path,
    load_record,
    local_bootstrap_policy_gate,
    parse_json_resource,
    prepare_review_evidence,
    read_embedded_json,
    read_requirements_brief,
    requirements_impact,
    rev_parse,
    snapshot_id,
    status_paths,
    utc_now,
    validate_developer_evidence,
    validate_independent_architecture_impact,
    validate_v4_architecture_delivery,
    validate_v4_contract_identity,
    validate_v4_current_observation_continuations,
    validate_v4_decision_references,
    validate_v4_live_architecture_baseline,
    validate_v4_live_dependencies,
    validate_v4_live_focus_relationship,
    validate_v4_live_requirements_baseline,
    rolling_must_future_candidates,
    validate_rolling_requirements,
    validate_v4_planning_risk,
    validate_v4_retrospective,
    v4_action_blockers,
    workflow_status_is_current,
)
from workflow_lock import canonical_resource_key, lock_probe
from workflow_paths import (
    WorkflowPathError,
    WorkflowPathOSError,
    WorkflowPathResourceError,
    WorkflowPathRuntimeError,
    WorkflowPathValueError,
    WorkflowPaths,
    atomic_write_json,
    resolve_path,
)


PLACEHOLDER = re.compile(r"(?:<[^>]+>|\b(?:TBD|TODO|placeholder)\b|\.\.\.)", re.IGNORECASE)
BASELINE_MARKER = "CODEX_REQUIREMENTS_BASELINE"
SHA256 = re.compile(r"[0-9a-f]{64}")


class DoctorFinding:
    """One independently reported doctor domain."""

    def __init__(self, status: str, *details: str) -> None:
        if status not in {"PASS", "WARN", "FAIL", "INVALID", "UNKNOWN"}:
            raise ValueError(f"Unsupported doctor status: {status}")
        self.status = status
        self.details = list(details)


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
    result = argparse.ArgumentParser(description="Check supported Codex Workflow state.")
    result.add_argument(
        "mode",
        choices=(
            "start", "manual", "doctor", "stop", "preflight", "snapshot", "gate",
            "requirements-snapshot", "requirements-gate", "requirements-impact",
            "rolling-promotion",
            "status", "integration-preflight", "closeout-gate",
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


def requirements_gate(paths: WorkflowPaths, path: Path, checks: Checks) -> Any:
    try:
        brief = read_requirements_brief(
            path,
            schema_path=paths.tracked("schemas") / "requirements-v1.schema.json",
        )
    except WorkflowJSONResourceError:
        raise
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
    try:
        validate_rolling_requirements(data)
    except WorkflowDataError as exc:
        checks.error(str(exc))

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
    for key in ("protocol", "project_rules", "project", "plan", "decisions", "backlog", "status", "workflow_doc"):
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
        except WorkflowJSONResourceError:
            raise
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
        try:
            local_bootstrap_policy_gate(paths.layout.get("integration_policy"), text)
        except WorkflowDataError as exc:
            checks.error(str(exc))
        v4_records: list[dict[str, Any]] = []
        runs = paths.tracked("runs")
        if runs.is_dir():
            for path in sorted(runs.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    checks.error(f"Unable to read task record {path.name}: {exc}")
                    continue
                if isinstance(payload, dict) and payload.get("version") == 4:
                    v4_records.append(payload)
        if v4_records:
            for payload in v4_records:
                try:
                    maybe_validate_v4_backlog_focus(text, payload)
                except WorkflowDataError as exc:
                    checks.error(str(exc))


def _baseline(paths: WorkflowPaths, checks: Checks) -> dict[str, Any] | None:
    try:
        baseline, current, brief_path = current_requirements_baseline(paths)
        if current is not None and baseline != current:
            assert brief_path is not None
            checks.error(
                "Configured Requirements Brief differs from the PROJECT/Backlog baseline; "
                "run requirements-impact for "
                + paths.relative(brief_path)
                + " and apply its reviewed result before continuing."
            )
        return baseline
    except WorkflowJSONResourceError:
        raise
    except (OSError, WorkflowDataError, WorkflowPathError) as exc:
        checks.error(str(exc))
        return None


def _v4_architecture_gate(
    paths: WorkflowPaths, record: dict[str, Any], checks: Checks
) -> None:
    try:
        validate_v4_live_architecture_baseline(paths, record)
        changed_paths = (record.get("verification") or {}).get("changed_paths") or []
        if not isinstance(changed_paths, list):
            changed_paths = []
        registry = maybe_load_guardrail_registry(
            paths.tracked("decisions").read_text(encoding="utf-8")
        )
        validate_independent_architecture_impact(
            record, changed_paths, registry, project_root=paths.root
        )
        if changed_paths:
            validate_v4_architecture_delivery(
                record, changed_paths, registry, project_root=paths.root
            )
    except (OSError, WorkflowDataError, WorkflowPathError, WorkflowJSONResourceError) as exc:
        checks.error(str(exc))


def _v4_decision_reference_gate(record: dict[str, Any], checks: Checks) -> None:
    try:
        validate_v4_decision_references(record)
    except WorkflowDataError as exc:
        checks.error(str(exc))


def _v4_dependency_checkpoint_gate(
    paths: WorkflowPaths, record: dict[str, Any], checks: Checks
) -> None:
    try:
        validate_v4_live_dependencies(paths, record)
    except (OSError, WorkflowDataError, WorkflowPathError, WorkflowJSONResourceError) as exc:
        checks.error(str(exc))


def _v4_continuation_delivery_gate(
    paths: WorkflowPaths, record: dict[str, Any], checks: Checks
) -> None:
    try:
        validate_v4_current_observation_continuations(record)
    except WorkflowDataError as exc:
        checks.error(str(exc))
    current_snapshot = (record.get("verification") or {}).get("snapshot_id")
    current_commit = (record.get("verification") or {}).get("delivery_commit")
    if not isinstance(current_snapshot, str) or not isinstance(current_commit, str):
        return
    for decision in record.get("decision_log", []):
        if not isinstance(decision, dict) or decision.get("kind") != "product_checkpoint":
            continue
        source_commit = (decision.get("binding") or {}).get("delivery_commit")
        for continuation in decision.get("continuations") or []:
            if (
                not isinstance(continuation, dict)
                or continuation.get("target_snapshot_id") != current_snapshot
            ):
                continue
            if not isinstance(source_commit, str):
                checks.error("V4 observation continuation source commit is invalid.")
                continue
            try:
                changed_paths = canonical_delivery(paths, source_commit, current_commit)[
                    "changed_paths"
                ]
            except WorkflowDataError as exc:
                checks.error(str(exc))
                continue
            if continuation.get("changed_paths") != changed_paths:
                checks.error(
                    "V4 observation continuation changed_paths do not match the exact commit delta."
                )


def _v4_contract_gate(
    paths: WorkflowPaths,
    record: dict[str, Any],
    checks: Checks,
    *,
    action: str,
) -> None:
    def capture(callback: Any) -> None:
        try:
            callback()
        except (OSError, WorkflowDataError, WorkflowPathError, WorkflowJSONResourceError) as exc:
            checks.error(str(exc))

    capture(lambda: validate_v4_live_requirements_baseline(paths, record))
    capture(lambda: validate_v4_live_focus_relationship(paths, record))
    capture(
        lambda: maybe_validate_v4_backlog_focus(
            paths.tracked("backlog").read_text(encoding="utf-8"), record
        )
    )
    capture(lambda: validate_v4_planning_risk(record))
    capture(lambda: validate_v4_live_architecture_baseline(paths, record))
    capture(lambda: validate_v4_live_dependencies(paths, record))
    contract = record.get("delivery_contract") or {}
    kind = contract.get("kind")
    task_id = record.get("task_id")
    if kind == "core_slice" and contract.get("focus_slice_id") != task_id:
        checks.error("V4 core slice focus_slice_id must equal task_id.")
    if kind == "supporting" and (
        contract.get("supports_task_id") != contract.get("focus_slice_id")
    ):
        checks.error("V4 supporting task must support its exact focus slice.")
    for decision in record.get("decision_log", []):
        if not isinstance(decision, dict):
            continue
        if decision.get("kind") == "product_checkpoint":
            expected_blocking = (
                (contract.get("checkpoint") or {}).get("mode") == "required"
            )
        else:
            if decision.get("blocking") is False:
                affected = decision.get("affected_scope")
                if (
                    not isinstance(affected, list)
                    or not affected
                    or any(
                        not isinstance(item, str) or not item.startswith("future:")
                        for item in affected
                    )
                    or decision.get("current_delivery_independent") is not True
                ):
                    checks.error(
                        f"V4 non-blocking decision {decision.get('id')} is not future-only and independent."
                    )
            try:
                expected_blocking = derive_v4_decision_blocking(record, decision)
            except WorkflowDataError as exc:
                checks.error(str(exc))
                continue
        if (
            decision.get("status") == "open"
            and decision.get("blocking") is False
            and expected_blocking is True
        ):
            checks.error(
                f"V4 decision {decision.get('id')} was classified non-blocking past its safe boundary."
            )
    _v4_architecture_gate(paths, record, checks)
    _v4_decision_reference_gate(record, checks)
    _v4_continuation_delivery_gate(paths, record, checks)
    _v4_dependency_checkpoint_gate(paths, record, checks)
    try:
        for blocker in v4_action_blockers(record, action):
            checks.error(f"V4 {action} blocked: {blocker}.")
    except WorkflowDataError as exc:
        checks.error(str(exc))
    capture(lambda: validate_v4_contract_identity(record))


def _canonical_resource_key_set(
    keys: Any, *, skip_invalid: bool
) -> set[str] | None:
    if not isinstance(keys, list):
        return None
    canonical: set[str] = set()
    for key in keys:
        if not isinstance(key, str):
            if skip_invalid:
                continue
            return None
        try:
            canonical.add(canonical_resource_key(key))
        except ValueError:
            if skip_invalid:
                continue
            return None
    return canonical


def _validate_record_basics(
    paths: WorkflowPaths,
    record: dict[str, Any],
    checks: Checks,
    *,
    final: bool,
    action: str | None = None,
) -> None:
    version = record.get("version")
    if version not in {3, 4}:
        checks.error("Task record version must be 3 or 4 for supported commands.")
        return
    if version == 4:
        _v4_contract_gate(paths, record, checks, action=action or ("gate" if final else "preflight"))
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
    if lane.get("allowed_paths") != allowed_paths:
        checks.error("Lane scope must exactly match task scope paths and resources.")
    elif record.get("version") == 4:
        lane_keys = _canonical_resource_key_set(
            lane.get("resource_keys"), skip_invalid=True
        )
        scope_keys = _canonical_resource_key_set(resource_keys, skip_invalid=False)
        if lane_keys is None or scope_keys is None or not scope_keys.issubset(lane_keys):
            checks.error("Lane resource keys must include every task scope resource.")
    elif lane.get("resource_keys") != resource_keys:
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

    if version == 4:
        if not isinstance(source.get("requirements_baseline"), dict):
            checks.error("Every V4 task source requires source.requirements_baseline.")
    elif source_type == "mvp_backlog":
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
                requirements_gate(
                    paths,
                    paths.tracked("requirements") / f"{brief_id}.md",
                    checks,
                )


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
    if record.get("version") == 4:
        try:
            validate_v4_architecture_delivery(record, delivery["changed_paths"])
        except WorkflowDataError as exc:
            checks.error(str(exc))
    return delivery


def task_gate(paths: WorkflowPaths, record: dict[str, Any], checks: Checks, *, final: bool) -> dict[str, Any] | None:
    _validate_record_basics(
        paths,
        record,
        checks,
        final=final,
        action="gate" if final else "preflight",
    )
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
    review = record.get("review")
    if not isinstance(review, dict):
        checks.error("task.review must be an object.")
        review = {}
    developer_id = checks.require_text(developer.get("agent_id"), "task.developer.agent_id")
    if developer.get("snapshot_id") != delivery["snapshot_id"]:
        checks.error("Developer evidence is not bound to the sealed snapshot.")
    contract_version = developer.get("evidence_contract_version")
    review_contract_version = review.get("evidence_contract_version")
    claim_fingerprints: dict[str, str] = {}
    if contract_version == 1:
        if review_contract_version != 1:
            checks.error("Developer and Review evidence must use Evidence Contract v1 together.")
        try:
            claim_fingerprints = validate_developer_evidence(
                paths,
                developer,
                snapshot_id_value=delivery["snapshot_id"],
                delivery=delivery,
            )
        except WorkflowDataError as exc:
            checks.error(str(exc))
        if claim_fingerprints and review_contract_version == 1:
            try:
                review_for_validation = dict(review)
                if record.get("version") == 4:
                    review_for_validation.pop("observation_equivalence", None)
                prepare_review_evidence(
                    paths,
                    review_for_validation,
                    claim_fingerprints=claim_fingerprints,
                )
            except WorkflowDataError as exc:
                checks.error(str(exc))
    elif contract_version is None:
        integration = record.get("integration") or {}
        if integration.get("status") == "integrated" and review_contract_version is None:
            checks.warn(
                "Historical integrated task uses legacy Developer/Review evidence without scoped claims."
            )
        else:
            checks.error(
                "Final gate requires Evidence Contract v1; re-record scoped Developer evidence and independent Review."
            )
        checks.require_text(developer.get("handoff"), "task.developer.handoff")
    else:
        checks.error(f"Unsupported Developer evidence contract version: {contract_version!r}.")

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
        if not isinstance(accepted, list):
            checks.error("Review accepted_findings must be an array.")
        else:
            for severity in ("p2", "p3"):
                accepted_count = sum(
                    1
                    for item in accepted
                    if isinstance(item, dict) and item.get("severity") == severity
                )
                if accepted_count != findings.get(severity):
                    checks.error(
                        f"Every {severity.upper()} finding requires exactly one explicit "
                        f"{severity.upper()} acceptance."
                    )

    retrospective = record.get("process_retrospective")
    if record.get("version") == 4:
        try:
            validate_v4_retrospective(record, retrospective if isinstance(retrospective, dict) else {})
        except WorkflowDataError as exc:
            checks.error(str(exc))
    elif not isinstance(retrospective, dict) or retrospective.get("completed") is not True:
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
    if record.get("version") == 4:
        try:
            for blocker in v4_action_blockers(record, "prepare-integration"):
                checks.error(f"V4 prepare-integration blocked: {blocker}.")
        except WorkflowDataError as exc:
            checks.error(str(exc))
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
        try:
            local_bootstrap_policy_gate(
                paths.layout.get("integration_policy"),
                paths.tracked("backlog").read_text(encoding="utf-8"),
                task_id=record.get("task_id"),
            )
        except (OSError, WorkflowDataError) as exc:
            checks.error(str(exc))
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
    version = record.get("version")
    if version == 2:
        checks.error("V2 records are read-only history and cannot close out.")
        return
    if version not in {3, 4}:
        checks.error(f"Unsupported task record version for closeout: {version!r}.")
        return
    integration = record.get("integration")
    if not isinstance(integration, dict):
        checks.error("task.integration must be an object.")
        return
    if integration.get("status") not in {"merged_pending_closeout", "integrated"}:
        checks.error("Closeout gate requires merged_pending_closeout or integrated state.")
    for field in ("target_ref", "result_commit", "closeout_state_fingerprint"):
        checks.require_text(integration.get(field), f"task.integration.{field}")
    if version == 4 and integration.get("closeout_state_fingerprint"):
        if integration.get("closeout_fingerprint_version") != 4:
            checks.error("V4 closeout requires closeout_fingerprint_version=4.")
    target_ref = integration.get("target_ref")
    result_commit = integration.get("result_commit")
    closeout_commit = integration.get("closeout_commit")
    if closeout_commit is not None and (not isinstance(closeout_commit, str) or not closeout_commit.strip()):
        checks.error("task.integration.closeout_commit must be null or non-empty text.")
    if isinstance(target_ref, str) and isinstance(result_commit, str):
        try:
            target_oid = rev_parse(paths, target_ref)
            if not is_ancestor(paths, result_commit, target_oid):
                checks.error("Target ref does not contain the exact integrated result commit.")
            if isinstance(closeout_commit, str) and not is_ancestor(paths, closeout_commit, target_oid):
                checks.error("Target ref does not contain the exact closeout commit.")
        except WorkflowDataError as exc:
            checks.error(str(exc))


def _doctor_package(paths: WorkflowPaths) -> DoctorFinding:
    """Check installed package-owned bytes against the ownership manifest."""

    boundary = (
        "This check begins after Python loaded doctor; the manifest is consistency "
        "evidence, not a signed supply-chain trust root."
    )
    manifest_path = paths.root / ".codex-workflow" / "install" / "manifest.json"
    try:
        raw = manifest_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return DoctorFinding("FAIL", "The V3 install manifest is missing.", boundary)
    except UnicodeDecodeError as exc:
        return DoctorFinding("INVALID", f"The V3 install manifest is not valid UTF-8: {exc}", boundary)
    except OSError as exc:
        return DoctorFinding("UNKNOWN", f"The V3 install manifest could not be read: {exc}", boundary)
    try:
        manifest = parse_json_resource(raw, label="The V3 install manifest")
    except WorkflowJSONError as exc:
        return DoctorFinding("INVALID", str(exc), boundary)
    if not isinstance(manifest, dict):
        return DoctorFinding("INVALID", "The V3 install manifest root must be an object.", boundary)

    invalid: list[str] = []
    if manifest.get("package") != "codex-workflow-v3":
        invalid.append("manifest package is not codex-workflow-v3")
    if manifest.get("protocol_version") != 3:
        invalid.append("manifest protocol_version is not 3")
    version = manifest.get("version")
    if not isinstance(version, str) or not version.strip():
        invalid.append("manifest version is missing")
    entries = manifest.get("files")
    if not isinstance(entries, dict):
        invalid.append("manifest files must be an object")
        entries = {}

    package_files = 0
    drift: list[str] = []
    unknown: list[str] = []
    for relative, entry in entries.items():
        if not isinstance(relative, str) or not isinstance(entry, dict):
            invalid.append("manifest contains a malformed file entry")
            continue
        owner = entry.get("ownership")
        if owner not in {"package", "project", "merge"}:
            invalid.append(f"{relative}: unsupported ownership")
            continue
        hash_field = "seed_sha256" if owner == "project" else "managed_sha256"
        expected = entry.get(hash_field)
        if not isinstance(expected, str) or not SHA256.fullmatch(expected):
            invalid.append(f"{relative}: invalid {hash_field}")
            continue
        if owner != "package":
            continue
        package_files += 1
        try:
            target = paths.tracked(relative, key=False)
        except WorkflowPathError as exc:
            invalid.append(f"{relative}: {exc}")
            continue
        if not target.is_file():
            drift.append(f"{relative}: package-owned file is missing")
            continue
        try:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError as exc:
            unknown.append(f"{relative}: package-owned file could not be read: {exc}")
            continue
        if actual != expected:
            drift.append(f"{relative}: package-owned content drift")

    if package_files == 0:
        invalid.append("manifest contains no package-owned files")
    if invalid:
        return DoctorFinding("INVALID", *invalid, boundary)
    if unknown:
        return DoctorFinding("UNKNOWN", *unknown, boundary)
    if drift:
        return DoctorFinding("FAIL", *drift, boundary)
    return DoctorFinding(
        "PASS",
        f"{package_files} package-owned files match the V3 install manifest.",
        boundary,
    )


def _doctor_governance(paths: WorkflowPaths) -> DoctorFinding:
    checks = Checks()
    try:
        check_governance(paths, checks)
        baseline = _baseline(paths, checks)
        if baseline and isinstance(baseline.get("brief_id"), str):
            requirements_gate(
                paths,
                paths.tracked("requirements") / f"{baseline['brief_id']}.md",
                checks,
            )
    except UnicodeDecodeError as exc:
        return DoctorFinding("INVALID", f"Project governance is not valid UTF-8: {exc}")
    except WorkflowJSONError as exc:
        return DoctorFinding("INVALID", str(exc))
    except (OSError, WorkflowDataError, WorkflowPathError) as exc:
        return DoctorFinding("UNKNOWN", f"Project governance could not be read reliably: {exc}")
    if checks.errors:
        return DoctorFinding("FAIL", *checks.errors, *checks.warnings)
    if checks.warnings:
        return DoctorFinding("WARN", *checks.warnings)
    return DoctorFinding(
        "PASS",
        "Required governance files, Backlog structure and Requirements baseline are valid.",
    )


def _managed_hook_records(hooks: Any) -> tuple[list[tuple[str, dict[str, Any], dict[str, Any]]], list[str]]:
    records: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    invalid: list[str] = []
    if not isinstance(hooks, dict):
        return records, ["hooks must be an object"]
    for event, groups in hooks.items():
        if not isinstance(event, str) or not isinstance(groups, list):
            invalid.append(f"Hook event {event!r} must contain an array")
            continue
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                invalid.append(f"Hook event {event!r} group {group_index} is malformed")
                continue
            for handler_index, handler in enumerate(group["hooks"]):
                if not isinstance(handler, dict):
                    invalid.append(
                        f"Hook event {event!r} handler {group_index}:{handler_index} is malformed"
                    )
                    continue
                commands = (
                    handler.get("command"),
                    handler.get("commandWindows"),
                    handler.get("command_windows"),
                )
                if any(
                    isinstance(command, str)
                    and ("workflow_check.py" in command or "codex_stop_hook.py" in command)
                    for command in commands
                ):
                    records.append((event, group, handler))
    return records, invalid


def _doctor_hooks(paths: WorkflowPaths) -> DoctorFinding:
    hook_path = paths.root / ".codex" / "hooks.json"
    try:
        raw = hook_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return DoctorFinding(
            "FAIL",
            "The project Hook configuration .codex/hooks.json is missing.",
            "Hook configuration only; it does not prove that a Hook ran.",
        )
    except UnicodeDecodeError as exc:
        return DoctorFinding(
            "INVALID",
            f"The project Hook configuration is not valid UTF-8: {exc}",
            "Hook configuration only; it does not prove that a Hook ran.",
        )
    except OSError as exc:
        return DoctorFinding(
            "UNKNOWN",
            f"The project Hook configuration could not be read: {exc}",
            "Hook configuration only; it does not prove that a Hook ran.",
        )
    try:
        payload = parse_json_resource(raw, label="The project Hook configuration")
    except WorkflowJSONError as exc:
        return DoctorFinding(
            "INVALID",
            str(exc),
            "Hook configuration only; it does not prove that a Hook ran.",
        )
    if not isinstance(payload, dict):
        return DoctorFinding(
            "INVALID",
            "The project Hook configuration root must be an object.",
            "Hook configuration only; it does not prove that a Hook ran.",
        )

    records, invalid = _managed_hook_records(payload.get("hooks"))
    expected = {
        "workflow_check.py": {
            "event": "SessionStart",
            "matcher": "startup|resume|clear|compact",
            "command": (
                'python3 "$(git rev-parse --show-toplevel)/.codex-workflow/bin/'
                'workflow_check.py" start'
            ),
            "commandWindows": (
                "powershell -NoProfile -Command \"$root = git rev-parse --show-toplevel; "
                "py -3 (Join-Path $root '.codex-workflow/bin/workflow_check.py') start\""
            ),
            "statusMessage": "Checking project workflow",
        },
        "codex_stop_hook.py": {
            "event": "Stop",
            "matcher": None,
            "command": (
                'python3 "$(git rev-parse --show-toplevel)/.codex-workflow/bin/'
                'codex_stop_hook.py"'
            ),
            "commandWindows": (
                "powershell -NoProfile -Command \"$root = git rev-parse --show-toplevel; "
                "py -3 (Join-Path $root '.codex-workflow/bin/codex_stop_hook.py')\""
            ),
            "statusMessage": "Checking completion evidence",
        },
    }
    for script, contract in expected.items():
        matches = [record for record in records if any(
            isinstance(record[2].get(field), str) and script in record[2][field]
            for field in ("command", "commandWindows", "command_windows")
        )]
        if len(matches) != 1:
            invalid.append(f"managed {script} Hook must appear exactly once")
            continue
        event, group, handler = matches[0]
        if event != contract["event"]:
            invalid.append(f"managed {script} Hook is attached to the wrong event")
        if group.get("matcher") != contract["matcher"]:
            invalid.append(f"managed {script} Hook matcher is invalid")
        for field in ("command", "commandWindows", "statusMessage"):
            if handler.get(field) != contract[field]:
                invalid.append(f"managed {script} Hook {field} is invalid")
        if handler.get("type") != "command" or handler.get("timeout") != 30:
            invalid.append(f"managed {script} Hook type or timeout is invalid")

    boundary = "Hook configuration only; it does not prove that a Hook ran."
    if invalid:
        return DoctorFinding("FAIL", *invalid, boundary)
    return DoctorFinding(
        "PASS",
        "The managed SessionStart and Stop Hooks are configured.",
        boundary,
    )


def _parse_observation_time(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid checked_at: expected a non-empty ISO-8601 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("invalid checked_at: expected an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("checked_at must include a timezone")
    return parsed


def _doctor_observation(paths: WorkflowPaths) -> DoctorFinding:
    try:
        observation_path = paths.shared_runtime / "audit" / "last-session-check.json"
    except RecursionError:
        raise
    except (WorkflowPathError, OSError) as exc:
        return DoctorFinding(
            "UNKNOWN",
            f"The startup observation location could not be resolved reliably: {exc}",
        )
    try:
        raw = observation_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return DoctorFinding(
            "WARN",
            "No startup observation record exists for this worktree.",
            "This does not mean the package is broken and does not prove current session identity.",
        )
    except UnicodeDecodeError as exc:
        return DoctorFinding("INVALID", f"The startup observation is not valid UTF-8: {exc}")
    except OSError as exc:
        return DoctorFinding("UNKNOWN", f"The startup observation could not be read: {exc}")
    try:
        observation = parse_json_resource(raw, label="The startup observation")
    except WorkflowJSONError as exc:
        return DoctorFinding("INVALID", str(exc))
    if not isinstance(observation, dict):
        return DoctorFinding("INVALID", "The startup observation root must be an object.")
    try:
        _parse_observation_time(observation.get("checked_at"))
    except ValueError as exc:
        return DoctorFinding("INVALID", str(exc))
    checked_at = observation["checked_at"]
    recorded_worktree = observation.get("worktree")
    if not isinstance(recorded_worktree, str) or not recorded_worktree.strip():
        return DoctorFinding("INVALID", "The startup observation has an invalid worktree.")
    try:
        resolved_worktree = resolve_path(
            Path(recorded_worktree),
            label="startup observation worktree",
            expand_user=True,
        )
    except (WorkflowPathOSError, WorkflowPathValueError) as exc:
        return DoctorFinding("INVALID", f"The startup observation worktree is invalid: {exc}")
    except WorkflowPathError as exc:
        return DoctorFinding(
            "UNKNOWN",
            f"The startup observation worktree could not be resolved reliably: {exc}",
        )
    if resolved_worktree != paths.root:
        return DoctorFinding(
            "INVALID",
            "The startup observation belongs to a different worktree.",
        )
    return DoctorFinding(
        "PASS",
        f"Recorded at {checked_at} for worktree {paths.root}.",
        "This historical record does not prove a current or unique session identity.",
    )


def _doctor_safe_line(value: Any) -> str:
    """Render untrusted diagnostic text without terminal line or encoding injection."""

    rendered: list[str] = []
    for character in str(value):
        if character == "\r":
            rendered.append(r"\r")
        elif character == "\n":
            rendered.append(r"\n")
        elif character == "\t":
            rendered.append(r"\t")
        elif character.isprintable():
            rendered.append(character)
        else:
            codepoint = ord(character)
            escape = f"\\u{codepoint:04x}" if codepoint <= 0xFFFF else f"\\U{codepoint:08x}"
            rendered.append(escape)
    return "".join(rendered)


def _print_doctor(findings: list[tuple[str, DoctorFinding]], action: str) -> None:
    print("Codex Workflow doctor")
    print()
    for label, finding in findings:
        print(f"{_doctor_safe_line(label)}: {_doctor_safe_line(finding.status)}")
        for detail in finding.details:
            print(f"  {_doctor_safe_line(detail)}")
        print()
    print("PRIMARY NEXT ACTION:")
    print(f"  {_doctor_safe_line(action)}")


def _doctor_action(
    package: DoctorFinding,
    governance: DoctorFinding,
    hooks: DoctorFinding,
    observation: DoctorFinding,
) -> str:
    if package.status != "PASS":
        return (
            "Run the external package verifier, then use the normal ownership-aware installer "
            "if repair is needed; do not force-overwrite project governance."
        )
    if governance.status != "PASS":
        return "Inspect and restore the missing or invalid project governance, then run doctor again."
    if hooks.status != "PASS":
        return "Review and correct the managed entries in .codex/hooks.json, then run doctor again."
    if observation.status in {"FAIL", "INVALID", "UNKNOWN"}:
        return (
            "Review and trust the project Hooks, then start or resume a new Codex session to "
            "replace the invalid observation and run doctor again in that session."
        )
    if observation.status == "WARN":
        return (
            "Review and trust the project Hooks, then start or resume a new Codex session and "
            "run doctor again in that session."
        )
    return (
        "No corrective action is required. Start new work as a V4 focus core slice; "
        "keep any in-progress V3 task on the V3 closeout path. "
        "Run workflow_check.py status to see the product summary."
    )


def doctor(paths: WorkflowPaths) -> int:
    package = _doctor_package(paths)
    governance = _doctor_governance(paths)
    hooks = _doctor_hooks(paths)
    observation = _doctor_observation(paths)
    findings = [
        ("PACKAGE", package),
        ("GOVERNANCE", governance),
        ("HOOK CONFIG", hooks),
        ("STARTUP OBSERVATION", observation),
    ]
    _print_doctor(findings, _doctor_action(package, governance, hooks, observation))
    reliable = (
        package.status == "PASS"
        and governance.status == "PASS"
        and hooks.status == "PASS"
        and observation.status in {"PASS", "WARN"}
    )
    return 0 if reliable else 1


def _doctor_discovery_failure(exc: Exception) -> int:
    package = DoctorFinding(
        "UNKNOWN",
        f"Python loaded doctor, but the Git worktree or V3 layout could not be discovered: {exc}",
        "workflow_check.py or an import failure remains an external bootstrap failure.",
    )
    skipped = DoctorFinding("UNKNOWN", "Not checked because worktree discovery failed.")
    findings = [
        ("PACKAGE", package),
        ("GOVERNANCE", skipped),
        ("HOOK CONFIG", skipped),
        ("STARTUP OBSERVATION", skipped),
    ]
    _print_doctor(
        findings,
        "Run the external package verifier and inspect the Git worktree/layout before retrying doctor.",
    )
    return 1


def main() -> None:
    args = parser().parse_args()
    try:
        paths = WorkflowPaths.discover(Path.cwd())
    except UnicodeDecodeError as exc:
        if args.mode == "doctor":
            raise SystemExit(_doctor_discovery_failure(exc)) from exc
        raise
    except WorkflowPathResourceError as exc:
        if args.mode == "doctor":
            raise SystemExit(_doctor_discovery_failure(exc)) from exc
        if exc.__cause__ is not None:
            raise exc.__cause__
        raise
    except (WorkflowPathError, OSError) as exc:
        if args.mode == "doctor":
            raise SystemExit(_doctor_discovery_failure(exc)) from exc
        raise SystemExit(f"[workflow-check] ERROR: {exc}") from exc

    if args.mode == "doctor":
        raise SystemExit(doctor(paths))

    checks = Checks()

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
        _baseline(paths, checks)
        if paths.layout.get("parallel", {}).get("mode") == "local_worktree":
            try:
                paths.ensure_runtime()
                lock_probe(paths.shared_runtime / "locks" / "advisory-probe.lock")
            except Exception as exc:
                checks.error(str(exc))
    elif mode in {"requirements-snapshot", "requirements-gate", "requirements-impact", "rolling-promotion"}:
        if not args.target:
            checks.error(f"{mode} requires a requirements brief path.")
        else:
            candidate = Path(args.target)
            if not candidate.is_absolute():
                candidate = paths.root / candidate
            try:
                candidate = resolve_path(candidate, label="Requirements brief")
                requirements_root = paths.tracked("requirements")
                if requirements_root not in candidate.parents:
                    raise WorkflowPathError("Requirements brief must be under the configured requirements directory.")
                brief = read_requirements_brief(
                    candidate,
                    schema_path=paths.tracked("schemas") / "requirements-v1.schema.json",
                )
                payload = {
                    "brief_id": brief.metadata.get("brief_id"),
                    "revision": brief.metadata.get("revision"),
                    "target_release": brief.metadata.get("target_release"),
                    "fingerprint": brief.fingerprint,
                }
                if mode == "requirements-gate":
                    requirements_gate(paths, candidate, checks)
                elif mode == "rolling-promotion":
                    pending = rolling_must_future_candidates(brief.metadata)
                    payload = {
                        **payload,
                        "must_promote": pending,
                        "action": (
                            "Create a new Brief revision; do not confirm future candidates as Must."
                        ),
                    }
                    if pending:
                        checks.error(
                            "Future candidates must be promoted out of the approved Must contract: "
                            + ", ".join(pending)
                        )
                elif mode == "requirements-impact":
                    requirements_gate(paths, candidate, checks)
                    if not checks.errors:
                        payload = requirements_impact(paths, candidate)
            except (OSError, WorkflowDataError, WorkflowPathError) as exc:
                checks.error(str(exc))
    elif mode == "status":
        try:
            snapshot, current = workflow_status_is_current(paths)
            payload = {**snapshot, "status_document_current": current}
            if not current:
                checks.error("Workflow status document is stale; run workflow_state.py sync-status --apply.")
            contract = snapshot.get("requirements_contract")
            if isinstance(contract, dict) and contract.get("status") == "drift":
                checks.error(
                    "Requirements Brief differs from the managed baseline; run requirements-impact and apply its reviewed result."
                )
            elif isinstance(contract, dict) and contract.get("status") == "invalid":
                checks.error(
                    "Current Requirements Brief is not a valid approved contract: "
                    + str(contract.get("reason") or "unknown error")
                )
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
        elif mode == "requirements-impact":
            print(f"REQUIREMENTS_IMPACT_ID={payload['analysis_id']}")
            print(f"REQUIREMENTS_IMPACT_STATUS={payload['status']}")
        elif mode == "rolling-promotion":
            print("MUST_PROMOTE=" + ",".join(payload.get("must_promote") or []))
        elif mode == "status":
            print(f"STATUS_SHA256={payload['status_fingerprint']}")
        elif "snapshot_id" in payload:
            print(f"DELIVERY_SHA256={payload['delivery_hash']}")
            print(f"PATCH_SHA256={payload['patch_hash']}")
            print(f"SNAPSHOT_ID={payload['snapshot_id']}")
    checks.finish(mode)


if __name__ == "__main__":
    main()
