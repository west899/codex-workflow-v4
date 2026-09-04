from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from support import (
    PACKAGE_ROOT,
    approved_requirements,
    basic_v4_record,
    commit_all,
    configure_v4_architecture_baseline,
    create_baseline,
    developer_evidence_v1,
    install_project,
    record_relative,
    run,
    sync_backlog_focus_from_record,
    workflow_command,
    write_record,
)

BIN_PATH = PACKAGE_ROOT / "payload/.codex-workflow/bin"
sys.path.insert(0, str(BIN_PATH))
from workflow_paths import WorkflowPaths  # noqa: E402
from workflow_common import (  # noqa: E402
    WorkflowDataError,
    apply_supersede_resolution,
    assert_v4_decision_write_allowed,
    classify_independent_architecture_impact,
    BACKLOG_FOCUS_MARKER,
    mark_backlog_focus_direction_confirmed,
    maybe_validate_v4_backlog_focus,
    parse_guardrail_registry,
    read_embedded_json,
    replace_embedded_json,
    phase_b_pending_queued_recovery,
    validate_backlog_fixed_columns,
    validate_contract_backlog_focus,
    validate_independent_architecture_impact,
    requirement_horizon,
    validate_fitness_ref,
    validate_rolling_requirements,
    validate_v4_architecture_delivery,
    validate_v4_backlog_focus,
    validate_v4_planning_risk,
    validate_v4_retrospective,
    v4_effective_risk_tier,
)


def _backlog(*, header=None, extra_columns=None, items=None, wip=1, row=True) -> str:
    header = header or [
        "ID",
        "优先级",
        "可观察交付结果",
        "依赖",
        "验收来源",
        "风险",
        "状态",
        "阻塞类型",
        "任务记录",
        "Lane/资源",
        "集成证据",
    ]
    if extra_columns:
        header = list(header) + list(extra_columns)
    items = items if items is not None else [
        {
            "id": "MVP-001",
            "kind": "core_slice",
            "focus_slice_id": "MVP-001",
            "supports_task_id": None,
            "direction_confirmed": False,
        }
    ]
    row_line = (
        "| MVP-001 | Must | core | 无 | REQ-F-001 | 无 | ready | none | rec | lane | - |\n"
        if row
        else ""
    )
    return (
        "<!-- CODEX_BACKLOG_FOCUS_START -->\n"
        + __import__("json").dumps(
            {
                "workflow_schema_version": 4,
                "wip": {"unconfirmed_core_slice_limit": wip},
                "items": items,
            },
            ensure_ascii=False,
        )
        + "\n<!-- CODEX_BACKLOG_FOCUS_END -->\n\n"
        + "| "
        + " | ".join(header)
        + " |\n"
        + "| "
        + " | ".join("---" for _ in header)
        + " |\n"
        + row_line
    )


class PhaseBBacklogTests(unittest.TestCase):
    def test_fixed_columns_and_appended_column_are_allowed(self) -> None:
        text = _backlog(extra_columns=["kind"])
        validate_backlog_fixed_columns(text)
        metadata = validate_v4_backlog_focus(text)
        self.assertEqual(metadata["unconfirmed_core_slice_ids"], ["MVP-001"])

    def test_insert_or_reorder_fixed_columns_fails_closed(self) -> None:
        inserted = [
            "ID",
            "kind",
            "优先级",
            "可观察交付结果",
            "依赖",
            "验收来源",
            "风险",
            "状态",
            "阻塞类型",
            "任务记录",
        ]
        with self.assertRaisesRegex(WorkflowDataError, "inserted or reordered"):
            validate_backlog_fixed_columns(_backlog(header=inserted))

    def test_supporting_missing_bindings_and_wip_overflow_fail(self) -> None:
        missing = [
            {
                "id": "MVP-002",
                "kind": "supporting",
                "focus_slice_id": "MVP-001",
                "supports_task_id": None,
                "direction_confirmed": True,
            }
        ]
        with self.assertRaisesRegex(WorkflowDataError, "missing supports_task_id"):
            validate_v4_backlog_focus(_backlog(items=missing, row=False))
        overflow = [
            {
                "id": "MVP-001",
                "kind": "core_slice",
                "focus_slice_id": "MVP-001",
                "supports_task_id": None,
                "direction_confirmed": False,
            },
            {
                "id": "MVP-003",
                "kind": "core_slice",
                "focus_slice_id": "MVP-003",
                "supports_task_id": None,
                "direction_confirmed": False,
            },
        ]
        with self.assertRaisesRegex(WorkflowDataError, "WIP overflow"):
            validate_v4_backlog_focus(_backlog(items=overflow, wip=1, row=False))

    def test_contract_backlog_mismatch_fails(self) -> None:
        metadata = validate_v4_backlog_focus(_backlog())
        record = {
            "version": 4,
            "task_id": "MVP-001",
            "delivery_contract": {
                "kind": "supporting",
                "focus_slice_id": "MVP-001",
                "supports_task_id": "MVP-001",
            },
        }
        with self.assertRaisesRegex(WorkflowDataError, "kind does not match"):
            validate_contract_backlog_focus(record, metadata)

    def test_live_helper_rejects_v4_task_absent_from_metadata(self) -> None:
        text = _backlog()
        omitted = {
            "version": 4,
            "task_id": "MVP-002",
            "delivery_contract": {
                "kind": "core_slice",
                "focus_slice_id": "MVP-002",
                "supports_task_id": None,
            },
        }
        with self.assertRaisesRegex(WorkflowDataError, "missing from Backlog focus metadata"):
            maybe_validate_v4_backlog_focus(text, omitted)
        with self.assertRaisesRegex(WorkflowDataError, "missing from Backlog focus metadata"):
            validate_v4_backlog_focus(text, omitted)

    def test_live_helper_rejects_v4_when_focus_marker_is_missing(self) -> None:
        omitted = {
            "version": 4,
            "task_id": "MVP-001",
            "delivery_contract": {
                "kind": "core_slice",
                "focus_slice_id": "MVP-001",
                "supports_task_id": None,
            },
        }
        with self.assertRaisesRegex(WorkflowDataError, "requires Backlog focus metadata"):
            maybe_validate_v4_backlog_focus("# MVP Backlog\n", omitted)
        self.assertIsNone(
            maybe_validate_v4_backlog_focus("# MVP Backlog\n", {"version": 3, "task_id": "MVP-001"})
        )


class PhaseBRequirementsAndRiskTests(unittest.TestCase):
    def test_future_candidate_cannot_enter_approved_must_contract(self) -> None:
        brief = {
            "status": "approved",
            "requirements": {
                "capabilities": [
                    {
                        "id": "REQ-F-099",
                        "priority": "must",
                        "horizon": "future_candidate",
                        "status": "confirmed",
                    }
                ]
            },
        }
        with self.assertRaisesRegex(WorkflowDataError, "Future candidate"):
            validate_rolling_requirements(brief)

    def test_current_slice_must_does_not_widen_v1_rule(self) -> None:
        brief = {
            "status": "approved",
            "requirements": {
                "capabilities": [
                    {
                        "id": "REQ-F-001",
                        "priority": "must",
                        "horizon": "current_slice",
                        "status": "confirmed",
                    }
                ]
            },
        }
        validate_rolling_requirements(brief)
        self.assertEqual(requirement_horizon({"id": "REQ-F-001"}), "current_slice")
        with self.assertRaisesRegex(WorkflowDataError, "horizon"):
            requirement_horizon({"id": "REQ-F-002", "horizon": "later"})

    def test_requirements_gate_rejects_future_must_and_invalid_horizon(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            approved_requirements(target)
            brief = target / ".codex-workflow/governance/requirements/REQ-001.md"
            start = "<!-- CODEX_REQUIREMENTS_JSON_START -->"
            end = "<!-- CODEX_REQUIREMENTS_JSON_END -->"
            text = brief.read_text(encoding="utf-8")
            payload = json.loads(text.split(start, 1)[1].split(end, 1)[0])
            payload["requirements"]["capabilities"][0]["horizon"] = "future_candidate"
            markdown = text.split(end, 1)[1]
            from support import requirements_fingerprint

            fingerprint = requirements_fingerprint(payload, markdown)
            payload["approval"]["approved_fingerprint"] = fingerprint
            brief.write_text(
                start + "\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n" + end + markdown,
                encoding="utf-8",
            )
            for relative in (
                Path(".codex-workflow/governance/PROJECT.md"),
                Path(".codex-workflow/state/MVP_BACKLOG.md"),
            ):
                path = target / relative
                block_start = "<!-- CODEX_REQUIREMENTS_BASELINE_START -->"
                block_end = "<!-- CODEX_REQUIREMENTS_BASELINE_END -->"
                current = path.read_text(encoding="utf-8")
                baseline = json.loads(current.split(block_start, 1)[1].split(block_end, 1)[0])
                baseline["approval_fingerprint"] = fingerprint
                path.write_text(
                    current.split(block_start, 1)[0]
                    + block_start
                    + "\n"
                    + json.dumps(baseline, ensure_ascii=False, indent=2)
                    + "\n"
                    + block_end
                    + current.split(block_end, 1)[1],
                    encoding="utf-8",
                )
            blocked = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "requirements-gate",
                    str(brief.relative_to(target)),
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0, blocked.stderr)
            self.assertIn("Future candidate", blocked.stderr)
            promotion = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "rolling-promotion",
                    str(brief.relative_to(target)),
                ),
                cwd=target,
            )
            self.assertNotEqual(promotion.returncode, 0, promotion.stderr)
            self.assertIn("REQ-F-001", promotion.stderr + promotion.stdout)

    def test_not_required_rejected_above_small_no_trigger(self) -> None:
        small = {
            "planning": {"level": "small"},
            "risk": {
                "product_scope": False,
                "sensitive_data": False,
                "destructive_change": False,
                "irreversible_architecture": False,
                "production_release": False,
            },
        }
        high = {
            "planning": {"level": "small"},
            "delivery_contract": {"kind": "core_slice"},
            "risk": {
                "product_scope": False,
                "sensitive_data": True,
                "destructive_change": False,
                "irreversible_architecture": False,
                "production_release": False,
            },
        }
        self.assertEqual(v4_effective_risk_tier(small), "small")
        self.assertEqual(v4_effective_risk_tier(high), "high")
        with self.assertRaisesRegex(WorkflowDataError, "planning.level"):
            v4_effective_risk_tier({"planning": {"level": "tiny"}, "risk": {}})
        completed = {
            "completed": True,
            "completed_by": "v4-developer",
            "completed_at": "2026-07-20T02:00:00Z",
            "questions": {
                "repeated_problem_found": False,
                "guidance_gap_found": False,
                "deterministic_check_candidate_found": False,
            },
            "summary": "done",
        }
        with self.assertRaisesRegex(WorkflowDataError, "exec_plan"):
            validate_v4_planning_risk(high)
        with self.assertRaisesRegex(WorkflowDataError, "exec_plan"):
            validate_v4_retrospective(high, completed)
        high["planning"]["exec_plan"] = ".codex-workflow/state/plans/HIGH.md"
        validate_v4_planning_risk(high)
        validate_v4_retrospective(high, completed)
        allowed = {
            "not_required": True,
            "not_required_reason": "small no-trigger supporting fix",
            "completed": False,
            "questions": {
                "repeated_problem_found": False,
                "guidance_gap_found": False,
                "deterministic_check_candidate_found": False,
            },
        }
        validate_v4_retrospective(small, allowed)
        with self.assertRaisesRegex(WorkflowDataError, "small/no-trigger"):
            validate_v4_retrospective(high, allowed)
        with self.assertRaisesRegex(WorkflowDataError, "must be completed"):
            validate_v4_retrospective(high, {"completed": False, "questions": {}})


class PhaseBArchitectureDecisionQueueTests(unittest.TestCase):
    def test_independent_impact_does_not_trust_declared_none(self) -> None:
        self.assertEqual(
            classify_independent_architecture_impact(["src/api/contract.json"]),
            "changes_guardrail",
        )
        record = {
            "delivery_contract": {
                "architecture": {"declared_impact": "none", "guardrails": []}
            },
            "decision_log": [],
        }
        with self.assertRaisesRegex(WorkflowDataError, "cannot be trusted"):
            validate_independent_architecture_impact(record, ["openapi.yaml"])
        ordinary = {
            "delivery_contract": {
                "architecture": {"declared_impact": "none", "guardrails": []}
            },
            "decision_log": [],
        }
        with self.assertRaisesRegex(WorkflowDataError, "declared_impact=none"):
            validate_independent_architecture_impact(ordinary, ["src/app.py"])

    def test_within_guardrails_requires_fitness_and_registry_id(self) -> None:
        record = {
            "delivery_contract": {
                "architecture": {
                    "declared_impact": "within_guardrails",
                    "guardrails": [
                        {
                            "id": "ARCH-G-001",
                            "verification_refs": ["test:tests/test_guard.py"],
                        }
                    ],
                }
            },
            "decision_log": [],
        }
        registry = parse_guardrail_registry(
            """<!-- CODEX_GUARDRAIL_REGISTRY_START -->
{
  "workflow_schema_version": 4,
  "status": "approved",
  "revision": 1,
  "fingerprint": null,
  "items": [
    {
      "id": "ARCH-G-001",
      "source": "DECISIONS.md",
      "owner": "human",
      "statement": "Keep module boundaries.",
      "fitness_refs": ["test:tests/test_guard.py"]
    }
  ]
}
<!-- CODEX_GUARDRAIL_REGISTRY_END -->"""
        )
        self.assertEqual(
            validate_independent_architecture_impact(record, ["src/app.py"], registry),
            "within_guardrails",
        )
        missing = {
            "delivery_contract": {
                "architecture": {"declared_impact": "within_guardrails", "guardrails": []}
            },
            "decision_log": [],
        }
        with self.assertRaisesRegex(WorkflowDataError, "fitness evidence"):
            validate_independent_architecture_impact(missing, ["src/app.py"])
        with self.assertRaisesRegex(WorkflowDataError, "fitness evidence"):
            validate_v4_architecture_delivery(missing, ["src/app.py"])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(WorkflowDataError, "does not exist"):
                validate_fitness_ref(
                    "test:tests/missing_guard.py",
                    project_root=Path(directory),
                )
        validate_fitness_ref("test:architecture-boundary")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            escaped = root / "outside.py"
            escaped.write_text("print(1)\n", encoding="utf-8")
            project = root / "project"
            project.mkdir()
            with self.assertRaisesRegex(WorkflowDataError, "escapes the project root"):
                validate_fitness_ref("test:../outside.py", project_root=project)
            install_project(project)
            validate_fitness_ref("test:architecture-boundary", project_root=project)

    def test_conflicting_decision_requires_explicit_supersede(self) -> None:
        decision = {
            "status": "resolved",
            "resolution": {"selected_option_id": "A", "source": "user:one"},
        }
        same = apply_supersede_resolution(
            decision, {"selected_option_id": "A", "source": "user:one"}, supersede=False
        )
        self.assertEqual(same, "idempotent")
        with self.assertRaisesRegex(WorkflowDataError, "explicit supersede"):
            apply_supersede_resolution(
                decision,
                {"selected_option_id": "B", "source": "user:two"},
                supersede=False,
            )
        result = apply_supersede_resolution(
            decision,
            {"selected_option_id": "B", "source": "user:two"},
            supersede=True,
        )
        self.assertEqual(result, "superseded")
        self.assertEqual(decision["resolution"]["selected_option_id"], "B")
        self.assertEqual(decision["resolution_history"][0]["selected_option_id"], "A")

    def test_pending_queued_new_decisions_and_reopen_fail_closed(self) -> None:
        for status in ("pending", "queued"):
            with self.assertRaisesRegex(WorkflowDataError, "leaves not_ready"):
                assert_v4_decision_write_allowed(status)
            with self.assertRaisesRegex(WorkflowDataError, "abandon-only"):
                phase_b_pending_queued_recovery("dequeue", status)
            with self.assertRaisesRegex(WorkflowDataError, "abandon-only"):
                phase_b_pending_queued_recovery("reopen", status)
            with self.assertRaisesRegex(WorkflowDataError, "cannot forge done"):
                phase_b_pending_queued_recovery("mark-done", status)
            with self.assertRaisesRegex(WorkflowDataError, "Unsupported pending/queued recovery command"):
                phase_b_pending_queued_recovery("forge-done", status)
            self.assertEqual(
                phase_b_pending_queued_recovery("abandon", status), "abandon_only"
            )
        with self.assertRaisesRegex(WorkflowDataError, "pending, queued"):
            phase_b_pending_queued_recovery("abandon", "not_ready")

    def test_pending_queued_recovery_command_is_abandon_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            configure_v4_architecture_baseline(target)
            base = create_baseline(target)
            record = basic_v4_record(base)
            record["integration"]["status"] = "queued"
            path = write_record(target, record)
            sync_backlog_focus_from_record(target, record)
            relative = record_relative(path, target)
            refused = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "pending-queued-recovery",
                    relative,
                    "--action",
                    "dequeue",
                ),
                cwd=target,
            )
            self.assertNotEqual(refused.returncode, 0, refused.stderr)
            self.assertIn("abandon-only", refused.stderr)
            before = path.read_bytes()
            sealed = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "pending-queued-recovery",
                    relative,
                    "--action",
                    "abandon",
                ),
                cwd=target,
            )
            self.assertEqual(sealed.returncode, 0, sealed.stderr)
            payload = json.loads(sealed.stdout)
            self.assertEqual(payload["recovery"], "abandon_only")
            self.assertFalse(payload["mutated"])
            applied = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "pending-queued-recovery",
                    relative,
                    "--action",
                    "abandon",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(applied.returncode, 0, applied.stderr)
            self.assertIn("never mutates", applied.stderr)
            self.assertEqual(path.read_bytes(), before)

    def test_preflight_rejects_v4_without_backlog_focus_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            configure_v4_architecture_baseline(target)
            base = create_baseline(target)
            record = basic_v4_record(base)
            path = write_record(target, record)
            backlog = target / ".codex-workflow/state/MVP_BACKLOG.md"
            backlog.write_text("# MVP Backlog\n", encoding="utf-8")
            blocked = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "preflight",
                    record_relative(path, target),
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0, blocked.stderr)
            self.assertIn("requires Backlog focus metadata", blocked.stderr)

    def test_record_developer_rejects_missing_focus_and_wip_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            _, requirements_fingerprint = approved_requirements(target)
            architecture_fingerprint = configure_v4_architecture_baseline(target)
            base = create_baseline(target)
            record = basic_v4_record(
                base,
                requirements_baseline={
                    "brief_id": "REQ-001",
                    "revision": 1,
                    "approval_fingerprint": requirements_fingerprint,
                },
                architecture_fingerprint=architecture_fingerprint,
            )
            path = write_record(target, record)
            sync_backlog_focus_from_record(target, record)
            src = target / "src"
            src.mkdir(parents=True, exist_ok=True)
            (src / "feature.txt").write_text("observable v4 delivery\n", encoding="utf-8")
            commit_all(target, "v4 delivery")
            evidence = target / "developer.json"
            evidence.write_text(
                json.dumps(developer_evidence_v1("v4-developer"), ensure_ascii=False),
                encoding="utf-8",
            )
            relative = record_relative(path, target)
            command = workflow_command(
                target,
                "workflow_state.py",
                "record-developer",
                relative,
                "--evidence-json",
                str(evidence),
                "--apply",
            )
            backlog = target / ".codex-workflow/state/MVP_BACKLOG.md"
            original = backlog.read_text(encoding="utf-8")
            start = "<!-- CODEX_BACKLOG_FOCUS_START -->"
            end = "<!-- CODEX_BACKLOG_FOCUS_END -->"
            stripped = original.split(start, 1)[0] + original.split(end, 1)[1]
            backlog.write_text(stripped, encoding="utf-8")
            missing = run(command, cwd=target)
            self.assertNotEqual(missing.returncode, 0, missing.stderr)
            self.assertIn("requires Backlog focus metadata", missing.stderr)
            payload = read_embedded_json(original, BACKLOG_FOCUS_MARKER)
            items = list(payload.get("items") or [])
            items.append(
                {
                    "id": "MVP-002",
                    "kind": "core_slice",
                    "focus_slice_id": "MVP-002",
                    "supports_task_id": None,
                    "direction_confirmed": False,
                }
            )
            payload["items"] = items
            backlog.write_text(
                replace_embedded_json(original, BACKLOG_FOCUS_MARKER, payload),
                encoding="utf-8",
            )
            overflow = run(command, cwd=target)
            self.assertNotEqual(overflow.returncode, 0, overflow.stderr)
            self.assertIn("WIP overflow", overflow.stderr)
            doctor = run(
                workflow_command(target, "workflow_check.py", "doctor"),
                cwd=target,
            )
            self.assertNotEqual(doctor.returncode, 0, doctor.stderr + doctor.stdout)
            self.assertIn("WIP overflow", doctor.stderr + doctor.stdout)

    def test_lane_claim_rejects_missing_backlog_focus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(
                install_project(target, parallel_mode="local_worktree").returncode, 0
            )
            _, requirements_fingerprint = approved_requirements(target)
            architecture_fingerprint = configure_v4_architecture_baseline(target)
            base = create_baseline(target)
            record = basic_v4_record(
                base,
                requirements_baseline={
                    "brief_id": "REQ-001",
                    "revision": 1,
                    "approval_fingerprint": requirements_fingerprint,
                },
                architecture_fingerprint=architecture_fingerprint,
            )
            write_record(target, record)
            sync_backlog_focus_from_record(target, record)
            commit_all(target, "authorized V4 claim fixture")
            backlog = target / ".codex-workflow/state/MVP_BACKLOG.md"
            original = backlog.read_text(encoding="utf-8")
            start = "<!-- CODEX_BACKLOG_FOCUS_START -->"
            end = "<!-- CODEX_BACKLOG_FOCUS_END -->"
            backlog.write_text(
                original.split(start, 1)[0] + original.split(end, 1)[1],
                encoding="utf-8",
            )
            claimed = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "claim",
                    record["task_id"],
                    "--base",
                    "main",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(claimed.returncode, 0, claimed.stderr)
            self.assertIn("requires Backlog focus metadata", claimed.stderr)

    def test_focus_confirmation_refuses_lane_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(
                install_project(target, parallel_mode="local_worktree").returncode, 0
            )
            _, requirements_fingerprint = approved_requirements(target)
            architecture_fingerprint = configure_v4_architecture_baseline(target)
            base = create_baseline(target)
            record = basic_v4_record(
                base,
                requirements_baseline={
                    "brief_id": "REQ-001",
                    "revision": 1,
                    "approval_fingerprint": requirements_fingerprint,
                },
                architecture_fingerprint=architecture_fingerprint,
            )
            write_record(target, record)
            sync_backlog_focus_from_record(target, record)
            commit_all(target, "authorized V4 claim fixture")
            claimed = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "claim",
                    record["task_id"],
                    "--base",
                    "main",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(claimed.returncode, 0, claimed.stderr)
            marker = "worktree="
            stdout = claimed.stdout + claimed.stderr
            self.assertIn(marker, stdout)
            worktree = Path(stdout.split(marker, 1)[1].split()[0])
            lane_paths = WorkflowPaths.discover(worktree)
            with self.assertRaisesRegex(WorkflowDataError, "coordinator/integration worktree"):
                mark_backlog_focus_direction_confirmed(lane_paths, record["task_id"])


if __name__ == "__main__":
    unittest.main()
