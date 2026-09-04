from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from support import (
    PACKAGE_ROOT,
    basic_v3_record,
    basic_v4_record,
    configure_v4_architecture_baseline,
    create_baseline,
    install_project,
    run,
    workflow_command,
    write_record,
)

BIN_PATH = PACKAGE_ROOT / "payload/.codex-workflow/bin"
sys.path.insert(0, str(BIN_PATH))
from workflow_common import (  # noqa: E402
    WorkflowDataError,
    _backlog_focus_wip_lines,
    _product_card_lines,
    derive_v4_product_summary,
    redact_v4_human_value,
    render_workflow_status,
    requirements_impact_path,
    v4_stop_hook_next_action,
)


def _v4_card(
    *,
    method: str,
    entrypoint: str,
    supporting: bool = False,
    snapshot: bool = True,
    blocking_id: str | None = None,
    direction: str = "awaiting_human",
    placeholders: list[str] | None = None,
) -> dict:
    record = {
        "version": 4,
        "task_id": "MVP-SUP-001" if supporting else "MVP-001",
        "status": "completed" if supporting else "in_progress",
        "phase": "developer",
        "request": "Deliver one directly observable core result.",
        "acceptance": [
            {
                "id": "AC-001",
                "criterion": "The core user result is directly observable.",
                "status": "pending",
            }
        ],
        "delivery_contract": {
            "kind": "supporting" if supporting else "core_slice",
            "focus_slice_id": "MVP-001",
            "supports_task_id": "MVP-001" if supporting else None,
            "acceptance_ids": ["AC-001"],
            "checkpoint": {"mode": "required", "reason": "observe", "source": "user:test"},
            "observation": {
                "method": method,
                "entrypoint_ref": entrypoint,
                "fixture_ref": "fixture:core",
                "healthcheck_ref": None,
                "steps": ["Open the entry.", "Perform the core action.", "Check the result."],
                "cleanup_ref": None,
            },
            "known_placeholders": placeholders or ["Uses test fixture data."],
            "execution_mode": "formal",
            "architecture": {
                "declared_impact": "within_guardrails",
                "guardrails": [{"id": "ARCH-G-001"}],
            },
        },
        "decision_log": [],
        "verification": {
            "status": "pending",
            "snapshot_id": "snap-001" if snapshot else None,
            "changed_paths": ["tests/test_core.py"],
        },
        "integration": {"status": "not_ready"},
    }
    if blocking_id:
        record["decision_log"].append(
            {
                "id": blocking_id,
                "kind": "product_decision",
                "status": "open",
                "affected_scope": ["current_slice", "user_flow"],
                "latest_decision_point": "before_review",
                "current_delivery_independent": False,
                "question": "Which confirmation step should the core action use?",
                "options": [
                    {"id": "A", "label": "Confirm first"},
                    {"id": "B", "label": "Commit directly"},
                ],
            }
        )
    if direction == "changes_requested":
        snapshot_id = record["verification"].get("snapshot_id")
        record["decision_log"].append(
            {
                "id": "HD-CP",
                "kind": "product_checkpoint",
                "status": "resolved",
                "resolution": {"outcome": "changes_requested"},
                "binding": {"snapshot_id": snapshot_id} if snapshot_id else {},
            }
        )
    return record


class V4ProductSummaryTests(unittest.TestCase):
    def test_v3_record_has_no_product_card(self) -> None:
        summary = derive_v4_product_summary({"version": 3, "status": "in_progress", "phase": "developer"})
        self.assertFalse(summary["has_product_card"])
        self.assertEqual(summary["reason"], "v3_technical_only")
        self.assertIsNone(summary["focus_slice_id"])
        self.assertIsNone(v4_stop_hook_next_action({"version": 3, "status": "in_progress"}))

    def test_supporting_completion_does_not_claim_core_complete(self) -> None:
        summary = derive_v4_product_summary(_v4_card(method="cli", entrypoint="project-script:observe", supporting=True))
        self.assertTrue(summary["supporting_cannot_claim_core_complete"])
        self.assertIn("不等于核心已完成", summary["core_result"])
        self.assertNotIn("核心已完成", summary["core_result"].replace("不等于核心已完成", ""))

    def test_four_surfaces_each_have_one_next_action(self) -> None:
        fixtures = (
            ("browser", "project-script:preview", "UI preview"),
            ("api", "project-script:api-observe", "API"),
            ("cli", "project-script:observe-core-slice", "CLI"),
            ("data", "project-script:data-proof", "data proof"),
        )
        for method, entrypoint, label in fixtures:
            with self.subTest(method=method):
                record = _v4_card(method=method, entrypoint=entrypoint)
                action = v4_stop_hook_next_action(record)
                self.assertIsNotNone(action)
                assert action is not None
                self.assertEqual(action["kind"], "observe")
                self.assertEqual(action["text"].count("。"), 2)
                self.assertIn(label, action["text"])
                self.assertIn("不要批准、不要改状态、不要集成", action["text"])
                self.assertNotIn("token", action["text"].lower())
                summary = derive_v4_product_summary(record)
                self.assertEqual(summary["observation"]["surface_label"], label)
                self.assertEqual(summary["observation"]["entrypoint_ref"], entrypoint)

    def test_blocking_decision_wins_and_keeps_existing_options(self) -> None:
        record = _v4_card(
            method="cli",
            entrypoint="project-script:observe",
            blocking_id="HD-010",
        )
        action = v4_stop_hook_next_action(record)
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action["kind"], "wait_decision")
        self.assertEqual(action["decision_id"], "HD-010")
        self.assertIn("HD-010", action["text"])
        summary = derive_v4_product_summary(record)
        self.assertEqual(summary["blocking_decisions"][0]["id"], "HD-010")
        self.assertEqual(
            [item["id"] for item in summary["blocking_decisions"][0]["options"]],
            ["A", "B"],
        )

    def test_changes_requested_returns_to_developer(self) -> None:
        action = v4_stop_hook_next_action(
            _v4_card(method="cli", entrypoint="project-script:observe", direction="changes_requested")
        )
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action["kind"], "return_developer")
        self.assertIn("回到 Developer", action["text"])
        self.assertIn("不要报告 verified 或 done", action["text"])

    def test_stale_changes_requested_does_not_override_missing_current_observation(self) -> None:
        record = _v4_card(method="cli", entrypoint="project-script:new")
        record["verification"]["snapshot_id"] = "new-snap"
        record["decision_log"] = [
            {
                "id": "HD-CP",
                "kind": "product_checkpoint",
                "status": "resolved",
                "resolution": {"outcome": "changes_requested"},
                "binding": {"snapshot_id": "old-snap"},
            }
        ]
        summary = derive_v4_product_summary(record)
        self.assertEqual(summary["product_direction"], "awaiting_human")
        self.assertTrue(summary["observation"]["missing_current_receipt"])
        action = v4_stop_hook_next_action(record)
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action["kind"], "observe")
        self.assertIn("先按 CLI 入口观察当前结果", action["text"])
        self.assertNotIn("回到 Developer", action["text"])

    def test_sensitive_display_text_is_omitted(self) -> None:
        record = _v4_card(
            method="api",
            entrypoint="https://example.test/observe?token=abc&sig=deadbeef",
            placeholders=["Contact owner@example.com before continuing."],
        )
        summary = derive_v4_product_summary(record)
        blob = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("owner@example.com", blob)
        self.assertNotIn("sig=deadbeef", blob)
        self.assertIn("[omitted]", blob)
        self.assertEqual(redact_v4_human_value("ghp_abcdefghijklmnop"), "[omitted]")

    def test_stale_receipt_is_not_treated_as_current_result(self) -> None:
        record = _v4_card(method="cli", entrypoint="project-script:new")
        record["verification"]["snapshot_id"] = "new-snap"
        record["decision_log"] = [
            {
                "id": "HD-CP",
                "kind": "product_checkpoint",
                "status": "open",
                "observation_receipt": {
                    "snapshot_id": "old-snap",
                    "entrypoint": {"reference": "project-script:old", "status": "ready"},
                    "real_components": ["OLD RESULT"],
                    "temporary_components": ["old temp"],
                    "material_changes": ["old change"],
                    "normalized_result": {
                        "surface": "cli",
                        "assertions": [
                            {"id": "OBS-001", "status": "passed", "actual": "OLD RESULT"}
                        ],
                    },
                },
            }
        ]
        summary = derive_v4_product_summary(record)
        self.assertNotEqual(summary["core_result"], "OLD RESULT")
        self.assertIn("directly observable", summary["core_result"])
        self.assertEqual(summary["observation"]["entrypoint_ref"], "project-script:new")
        self.assertFalse(summary["observation"]["from_receipt"])
        self.assertTrue(summary["observation"]["missing_current_receipt"])
        self.assertEqual(summary["observation"]["health"], "no_current_receipt")

    def test_browser_receipt_keeps_ui_preview_label(self) -> None:
        record = _v4_card(method="browser", entrypoint="project-script:preview")
        record["verification"]["snapshot_id"] = "snap-1"
        record["decision_log"] = [
            {
                "id": "HD-CP",
                "kind": "product_checkpoint",
                "status": "open",
                "observation_receipt": {
                    "snapshot_id": "snap-1",
                    "entrypoint": {"reference": "project-script:preview", "status": "ready"},
                    "real_components": ["button works"],
                    "temporary_components": [],
                    "material_changes": [],
                    "normalized_result": {
                        "surface": "ui",
                        "assertions": [
                            {"id": "OBS-001", "status": "passed", "actual": "button works"}
                        ],
                    },
                },
            }
        ]
        summary = derive_v4_product_summary(record)
        self.assertEqual(summary["observation"]["surface"], "browser")
        self.assertEqual(summary["observation"]["surface_label"], "UI preview")
        self.assertEqual(summary["core_result"], "button works")
        rendered = "\n".join(_product_card_lines("MVP-001", summary))
        self.assertIn("UI preview", rendered)
        self.assertNotIn("可观察入口：ui ", rendered)

    def test_invalid_continuation_is_not_shown_as_equivalent(self) -> None:
        record = _v4_card(method="cli", entrypoint="project-script:observe")
        record["verification"]["snapshot_id"] = "new-snap"
        record["decision_log"] = [
            {
                "id": "HD-CP",
                "kind": "product_checkpoint",
                "status": "resolved",
                "resolution": {"outcome": "accepted"},
                "binding": {"snapshot_id": "old-snap"},
                "continuations": [{"target_snapshot_id": "new-snap"}],
            }
        ]
        summary = derive_v4_product_summary(record)
        self.assertIsNone(summary["continuation"])

    def test_exploratory_mode_is_visible_in_real_vs_temporary(self) -> None:
        record = _v4_card(method="cli", entrypoint="project-script:observe")
        record["delivery_contract"]["execution_mode"] = "exploratory"
        summary = derive_v4_product_summary(record)
        self.assertEqual(summary["real_vs_temporary"]["execution_mode"], "exploratory")
        rendered = "\n".join(_product_card_lines("MVP-001", summary))
        self.assertIn("exploratory（非正式产品进度）", rendered)
        self.assertNotIn("checks referenced", rendered)
        self.assertIn("live=not_checked", rendered)


class JsonIntegerBoundaryTests(unittest.TestCase):
    def test_requirements_impact_path_rejects_bool_revision(self) -> None:
        class Paths:
            def tracked(self, key: str) -> Path:
                raise AssertionError("invalid revision must fail before path lookup")

        with self.assertRaisesRegex(WorkflowDataError, "impact report identity"):
            requirements_impact_path(Paths(), "REQ-001", True)  # type: ignore[arg-type]

    def test_backlog_focus_wip_lines_ignore_bool_limit(self) -> None:
        lines = _backlog_focus_wip_lines(
            {
                "status": "ok",
                "unconfirmed_core_slice_ids": ["MVP-001"],
                "limit": True,
            }
        )
        self.assertEqual(lines, [])
        shown = _backlog_focus_wip_lines(
            {
                "status": "ok",
                "unconfirmed_core_slice_ids": ["MVP-001"],
                "limit": 1,
            }
        )
        self.assertTrue(any("WIP 上限 1" in item for item in shown))


class V4StatusRenderTests(unittest.TestCase):
    def test_human_product_section_precedes_technical_details_and_keeps_old_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            base = create_baseline(target)
            v4 = basic_v4_record(base)
            v4["verification"]["snapshot_id"] = "a" * 64
            v4["verification"]["delivery_commit"] = base
            v4["decision_log"] = [
                {
                    "id": "HD-010",
                    "kind": "product_decision",
                    "status": "open",
                    "affected_scope": ["current_slice"],
                    "latest_decision_point": "before_review",
                    "current_delivery_independent": False,
                    "question": "Keep the current confirmation step?",
                    "options": [
                        {"id": "A", "label": "Keep it"},
                        {"id": "B", "label": "Remove it"},
                    ],
                }
            ]
            write_record(target, v4)
            supporting = basic_v4_record(base, task_id="MVP-SUP-001")
            supporting["delivery_contract"]["kind"] = "supporting"
            supporting["delivery_contract"]["supports_task_id"] = "MVP-001"
            supporting["delivery_contract"]["focus_slice_id"] = "MVP-001"
            supporting["status"] = "completed"
            supporting["contract_fingerprint"] = v4["contract_fingerprint"]
            write_record(target, supporting)
            synced = run(
                workflow_command(target, "workflow_state.py", "sync-status", "--apply"),
                cwd=target,
            )
            self.assertEqual(synced.returncode, 0, synced.stderr)
            text = (target / ".codex-workflow/state/STATUS.md").read_text(encoding="utf-8")
            product_at = text.index("## 产品状态")
            technical_at = text.index("## 技术交付")
            json_at = text.index("<!-- CODEX_WORKFLOW_STATUS_JSON_START -->")
            self.assertLess(product_at, technical_at)
            self.assertLess(technical_at, json_at)
            self.assertIn("当前焦点", text)
            self.assertIn("可观察入口", text)
            self.assertIn("真实与临时", text)
            self.assertIn("本次实质变化", text)
            self.assertIn("待决定", text)
            self.assertIn("HD-010", text)
            self.assertIn("supporting 完成不等于核心已完成", text)
            self.assertIn("live=", text)
            self.assertNotIn("checks referenced", text)
            snapshot = json.loads(
                text.split("<!-- CODEX_WORKFLOW_STATUS_JSON_START -->", 1)[1].split(
                    "<!-- CODEX_WORKFLOW_STATUS_JSON_END -->", 1
                )[0]
            )
            for key in (
                "schema_version",
                "requirements_baseline",
                "requirements_contract",
                "backlog_counts",
                "task_records",
                "requirements_impacts",
                "status_fingerprint",
                "generated_at",
            ):
                self.assertIn(key, snapshot)
            for entry in snapshot["task_records"]:
                for key in (
                    "task_id",
                    "record",
                    "status",
                    "phase",
                    "verification",
                    "integration",
                    "product",
                ):
                    self.assertIn(key, entry)

    def test_v3_status_does_not_invent_focus_or_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            base = create_baseline(target)
            write_record(target, basic_v3_record(base))
            synced = run(
                workflow_command(target, "workflow_state.py", "sync-status", "--apply"),
                cwd=target,
            )
            self.assertEqual(synced.returncode, 0, synced.stderr)
            text = (target / ".codex-workflow/state/STATUS.md").read_text(encoding="utf-8")
            self.assertIn("当前没有 V4 产品卡片", text)
            self.assertIn("无 V4 产品卡片，仅技术状态", text)
            snapshot = json.loads(
                text.split("<!-- CODEX_WORKFLOW_STATUS_JSON_START -->", 1)[1].split(
                    "<!-- CODEX_WORKFLOW_STATUS_JSON_END -->", 1
                )[0]
            )
            product = snapshot["task_records"][0]["product"]
            self.assertFalse(product["has_product_card"])
            self.assertIsNone(product["focus_slice_id"])
            self.assertIsNone(product["product_direction"])

    def test_render_keeps_legacy_snapshot_keys_without_records(self) -> None:
        rendered = render_workflow_status(
            {
                "schema_version": 1,
                "requirements_baseline": {"brief_id": None, "revision": None},
                "requirements_contract": {"status": "not_configured"},
                "backlog_counts": {
                    "draft": 1,
                    "blocked": 0,
                    "ready": 0,
                    "done": 0,
                    "removed": 0,
                },
                "task_records": [],
                "requirements_impacts": [],
                "status_fingerprint": "a" * 64,
                "generated_at": "2026-08-24T00:00:00Z",
            }
        )
        self.assertIn("## 产品状态", rendered)
        self.assertIn("当前没有 V4 产品卡片", rendered)
        self.assertLess(rendered.index("## 产品状态"), rendered.index("## 技术交付"))
        self.assertIn("schema_version", rendered)

    def test_status_architecture_live_verified_and_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            fingerprint = configure_v4_architecture_baseline(target)
            base = create_baseline(target)
            record = basic_v4_record(base, architecture_fingerprint=fingerprint)
            write_record(target, record)
            synced = run(
                workflow_command(target, "workflow_state.py", "sync-status", "--apply"),
                cwd=target,
            )
            self.assertEqual(synced.returncode, 0, synced.stderr)
            text = (target / ".codex-workflow/state/STATUS.md").read_text(encoding="utf-8")
            self.assertIn("live=verified", text)
            self.assertNotIn("checks referenced", text)

            stale = json.loads(
                (target / ".codex-workflow/state/runs/MVP-001.json").read_text(encoding="utf-8")
            )
            stale["delivery_contract"]["architecture"]["baseline"]["fingerprint"] = "a" * 64
            write_record(target, stale)
            resynced = run(
                workflow_command(target, "workflow_state.py", "sync-status", "--apply"),
                cwd=target,
            )
            self.assertEqual(resynced.returncode, 0, resynced.stderr)
            stale_text = (target / ".codex-workflow/state/STATUS.md").read_text(encoding="utf-8")
            self.assertIn("live=stale", stale_text)
            self.assertNotIn("live=verified", stale_text)


if __name__ == "__main__":
    unittest.main()
