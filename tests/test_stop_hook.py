from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import (
    approved_requirements,
    basic_v3_record,
    basic_v4_record,
    configure_v4_architecture_baseline,
    create_baseline,
    install_project,
    run,
    workflow_command,
    write_record,
)


def _write_lane_pointer(target: Path, record: dict) -> None:
    pointer = target / ".git/codex-workflow-v4/lane.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    lane = record["lane"]
    pointer.write_text(
        json.dumps(
            {
                "task_id": record["task_id"],
                "lane_id": lane["lane_id"],
                "claim_id": lane["claim_id"],
                "owner_generation": lane["owner_generation"],
                "branch": lane["branch"],
                "record": f".codex-workflow/state/runs/{record['task_id']}.json",
            }
        ),
        encoding="utf-8",
    )


def _stop_hook(target: Path, *, active: bool = False) -> dict:
    result = run(
        workflow_command(target, "codex_stop_hook.py"),
        cwd=target,
        input_text=json.dumps({"cwd": str(target), "stop_hook_active": active}),
    )
    payload = json.loads(result.stdout)
    return payload


class StopHookTests(unittest.TestCase):
    def test_coordinator_without_lane_continues_and_broken_pointer_blocks_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            create_baseline(target)
            event = json.dumps({"cwd": str(target), "stop_hook_active": False})
            clean = run(workflow_command(target, "codex_stop_hook.py"), cwd=target, input_text=event)
            self.assertEqual(json.loads(clean.stdout), {"continue": True})

            pointer = target / ".git/codex-workflow-v4/lane.json"
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text("{broken", encoding="utf-8")
            broken = run(workflow_command(target, "codex_stop_hook.py"), cwd=target, input_text=event)
            payload = json.loads(broken.stdout)
            self.assertEqual(payload["decision"], "block")
            self.assertIn("unreadable", payload["reason"])

            repeated = run(
                workflow_command(target, "codex_stop_hook.py"),
                cwd=target,
                input_text=json.dumps({"cwd": str(target), "stop_hook_active": True}),
            )
            repeated_payload = json.loads(repeated.stdout)
            self.assertTrue(repeated_payload["continue"])
            self.assertIn("Do not claim completion", repeated_payload["systemMessage"])

    def test_v4_blocking_decision_is_the_only_next_action(self) -> None:
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
            record_path = write_record(target, record)
            request_path = target / "hd-003.json"
            request_path.write_text(
                json.dumps(
                    {
                        "id": "HD-003",
                        "kind": "product_decision",
                        "affected_scope": ["current_slice", "user_flow"],
                        "latest_decision_point": "before_review",
                        "current_delivery_independent": False,
                        "question": "Which data retention option should this slice use?",
                        "options": [
                            {
                                "id": "A",
                                "label": "Retain 30 days",
                                "impact": "Keeps a short window.",
                                "reversibility": "reversible",
                            },
                            {
                                "id": "B",
                                "label": "Retain 90 days",
                                "impact": "Increases stored data.",
                                "reversibility": "costly",
                            },
                        ],
                        "recommendation": {
                            "option_id": "A",
                            "reason": "Smaller retention is easier to reverse.",
                        },
                        "product_context": {
                            "user_behavior": "unchanged",
                            "data_contract": "changes",
                            "public_interface": "unchanged",
                            "dependencies": "unchanged",
                        },
                    }
                ),
                encoding="utf-8",
            )
            applied = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    record_path.relative_to(target).as_posix(),
                    "--decision-json",
                    str(request_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            _write_lane_pointer(target, current)
            payload = _stop_hook(target)
            self.assertTrue(payload["continue"])
            message = payload["systemMessage"]
            self.assertIn("HD-003", message)
            self.assertIn("不要批准、不要改状态、不要集成", message)
            self.assertNotIn("mark-verified", message)
            self.assertNotIn("prepare-integration", message)
            self.assertNotIn("confirm-closeout", message)
            repeated = _stop_hook(target, active=True)
            self.assertTrue(repeated["continue"])
            self.assertIn("HD-003", repeated["systemMessage"])
            self.assertNotIn("Do not claim completion", repeated["systemMessage"])

    def test_v4_four_surfaces_do_not_leak_secrets(self) -> None:
        fixtures = (
            ("browser", "project-script:preview"),
            ("api", "project-script:api-observe"),
            ("cli", "project-script:observe-core-slice"),
            ("data", "project-script:data-proof"),
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            base = create_baseline(target)
            for method, entrypoint in fixtures:
                with self.subTest(method=method):
                    record = basic_v4_record(base, task_id=f"MVP-{method.upper()}")
                    record["delivery_contract"]["observation"]["method"] = method
                    record["delivery_contract"]["observation"]["entrypoint_ref"] = entrypoint
                    record["delivery_contract"]["known_placeholders"] = [
                        "Uses test fixture data."
                    ]
                    record["verification"]["snapshot_id"] = "c" * 64
                    write_record(target, record)
                    _write_lane_pointer(target, record)
                    payload = _stop_hook(target)
                    self.assertTrue(payload["continue"])
                    message = payload["systemMessage"]
                    self.assertEqual(message.count("。"), 2)
                    self.assertIn("不要批准、不要改状态、不要集成", message)
                    self.assertNotIn("ghp_", message)
                    self.assertNotIn("@example.com", message)
                    self.assertNotIn("token=", message)
                    self.assertNotIn("sig=", message)

    def test_v3_in_progress_keeps_lifecycle_wording(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            base = create_baseline(target)
            record = basic_v3_record(base)
            record["status"] = "in_progress"
            record["lane"]["lane_id"] = "lane-MVP-001-single"
            record["lane"]["mode"] = "single"
            record["lane"]["branch"] = "main"
            record["lane"]["claim_id"] = "00000000-0000-4000-8000-000000000001"
            record["lane"]["owner_generation"] = 1
            write_record(target, record)
            _write_lane_pointer(target, record)
            payload = _stop_hook(target)
            self.assertTrue(payload["continue"])
            self.assertIn("remains in_progress", payload["systemMessage"])
            self.assertNotIn("HD-", payload["systemMessage"])


if __name__ == "__main__":
    unittest.main()
