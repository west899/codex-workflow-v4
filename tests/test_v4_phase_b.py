from __future__ import annotations

import sys
import unittest

from support import PACKAGE_ROOT

BIN_PATH = PACKAGE_ROOT / "payload/.codex-workflow/bin"
sys.path.insert(0, str(BIN_PATH))
from workflow_common import (  # noqa: E402
    WorkflowDataError,
    apply_supersede_resolution,
    assert_v4_decision_write_allowed,
    classify_independent_architecture_impact,
    maybe_validate_v4_backlog_focus,
    parse_guardrail_registry,
    phase_b_pending_queued_recovery,
    validate_backlog_fixed_columns,
    validate_contract_backlog_focus,
    validate_independent_architecture_impact,
    validate_rolling_requirements,
    validate_v4_architecture_delivery,
    validate_v4_backlog_focus,
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
            "none",
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
            self.assertEqual(
                phase_b_pending_queued_recovery("abandon", status), "abandon_only"
            )


if __name__ == "__main__":
    unittest.main()
