from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from support import (
    basic_v3_record,
    commit_all,
    create_baseline,
    developer_evidence_v1,
    install_project,
    record_relative,
    review_evidence_v1,
    run,
    workflow_command,
    write_record,
)


class LocalBootstrapExpiryTests(unittest.TestCase):
    def _assert_ok(self, result) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)

    def _write_json(self, path: Path, payload: dict) -> Path:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return path

    def _prepare_verified_task(self, root: Path, *, expiry_status: str) -> dict[str, object]:
        target = root / "project"
        self._assert_ok(install_project(target))
        layout_path = target / ".codex-workflow/layout.json"
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        layout["integration_policy"]["local_bootstrap"].update(
            {
                "enabled": True,
                "allowed_task_ids": ["OPS-001", "MVP-001"],
                "expires_after_task": "MVP-001",
                "require_ff_only": True,
            }
        )
        layout_path.write_text(
            json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        backlog_path = target / ".codex-workflow/state/MVP_BACKLOG.md"
        backlog = backlog_path.read_text(encoding="utf-8").replace(
            "| draft | none |", f"| {expiry_status} | none |", 1
        )
        backlog += (
            "| OPS-001 | Must | Bootstrap candidate | 无 | AC-001 | none | ready | none | - | - | - |\n"
        )
        backlog_path.write_text(backlog, encoding="utf-8")
        base = create_baseline(target)

        record = basic_v3_record(
            base,
            task_id="OPS-001",
            allowed_paths=["src/expiry/**"],
            resources=["path:src/expiry"],
        )
        record["status"] = "in_progress"
        record["phase"] = "developer"
        record["lane"].update(
            {
                "lane_id": "lane-OPS-001-expiry",
                "mode": "single",
                "branch": "main",
                "base_ref": "main",
                "base_commit": base,
                "claim_id": str(uuid.uuid4()),
                "owner_generation": 1,
                "assignment": {
                    "assigned_owner_id": str(uuid.uuid4()),
                    "assignment_generation": 1,
                    "assigned_at": "2026-07-11T00:00:00Z",
                    "assigned_by": "test-coordinator",
                },
            }
        )
        record_path = write_record(target, record)
        relative = record_relative(record_path, target)
        product = target / "src/expiry/feature.txt"
        product.parent.mkdir(parents=True)
        product.write_text("bootstrap candidate\n", encoding="utf-8")
        delivery_commit = commit_all(target, "bootstrap delivery")

        developer = self._write_json(
            root / "developer.json",
            developer_evidence_v1("developer-expiry"),
        )
        self._assert_ok(
            run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    relative,
                    "--evidence-json",
                    str(developer),
                    "--delivery-commit",
                    delivery_commit,
                    "--apply",
                ),
                cwd=target,
            )
        )
        current = json.loads(record_path.read_text(encoding="utf-8"))
        review = self._write_json(
            root / "review.json",
            review_evidence_v1("reviewer-expiry", current),
        )
        self._assert_ok(
            run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-review",
                    relative,
                    "--review-json",
                    str(review),
                    "--apply",
                ),
                cwd=target,
            )
        )
        acceptance = self._write_json(
            root / "acceptance.json",
            {
                "acceptance": [
                    {
                        "id": "AC-001",
                        "status": "passed",
                        "evidence": ["developer command and reviewer report"],
                    }
                ],
                "process_retrospective": {
                    "completed": True,
                    "completed_by": "coordinator-expiry",
                    "completed_at": "2026-07-11T00:00:00Z",
                    "questions": {
                        "repeated_problem_found": False,
                        "guidance_gap_found": False,
                        "deterministic_check_candidate_found": False,
                    },
                    "summary": "No reusable process gap found.",
                },
                "rule_proposals": [],
                "remaining_risks": [],
            },
        )
        self._assert_ok(
            run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "complete-task",
                    relative,
                    "--acceptance-json",
                    str(acceptance),
                    "--apply",
                ),
                cwd=target,
            )
        )
        self._assert_ok(
            run(workflow_command(target, "workflow_check.py", "gate", relative), cwd=target)
        )
        self._assert_ok(
            run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "mark-verified",
                    relative,
                    "--apply",
                ),
                cwd=target,
            )
        )
        current = json.loads(record_path.read_text(encoding="utf-8"))
        approval = self._write_json(
            root / "approval.json",
            {
                "kind": "local_bootstrap",
                "task_id": "OPS-001",
                "target_ref": "refs/heads/main",
                "snapshot_id": current["verification"]["snapshot_id"],
                "delivery_hash": current["verification"]["delivery_hash"],
                "approved_by": "test-user",
                "approved_at": "2026-07-11T00:00:00Z",
                "source": "user:test",
            },
        )
        self._assert_ok(
            run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-approval",
                    relative,
                    "--approval-json",
                    str(approval),
                    "--apply",
                ),
                cwd=target,
            )
        )
        return {
            "target": target,
            "record_path": record_path,
            "relative": relative,
            "backlog_path": backlog_path,
        }

    def test_manual_rejects_expiry_task_outside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            layout_path = target / ".codex-workflow/layout.json"
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
            layout["integration_policy"]["local_bootstrap"].update(
                {
                    "enabled": True,
                    "allowed_task_ids": ["OPS-001"],
                    "expires_after_task": "OPS-999",
                }
            )
            layout_path.write_text(
                json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            checked = run(
                workflow_command(target, "workflow_check.py", "manual"), cwd=target
            )
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("expires_after_task", checked.stderr)

    def test_prepare_refuses_after_expiry_task_is_done_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._prepare_verified_task(Path(directory), expiry_status="done")
            before = fixture["record_path"].read_bytes()
            prepared = run(
                workflow_command(
                    fixture["target"],
                    "workflow_state.py",
                    "prepare-integration",
                    fixture["relative"],
                    "--mode",
                    "local_bootstrap",
                    "--apply",
                ),
                cwd=fixture["target"],
            )
            self.assertNotEqual(prepared.returncode, 0)
            self.assertIn("expired after MVP-001", prepared.stderr)
            self.assertEqual(fixture["record_path"].read_bytes(), before)

    def test_integration_preflight_fails_when_policy_expires_after_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._prepare_verified_task(Path(directory), expiry_status="ready")
            prepared = run(
                workflow_command(
                    fixture["target"],
                    "workflow_state.py",
                    "prepare-integration",
                    fixture["relative"],
                    "--mode",
                    "local_bootstrap",
                    "--apply",
                ),
                cwd=fixture["target"],
            )
            self._assert_ok(prepared)
            backlog = fixture["backlog_path"].read_text(encoding="utf-8").replace(
                "| ready | none |", "| done | none |", 1
            )
            fixture["backlog_path"].write_text(backlog, encoding="utf-8")
            generation = json.loads(
                fixture["record_path"].read_text(encoding="utf-8")
            )["generation"]
            checked = run(
                workflow_command(
                    fixture["target"],
                    "workflow_check.py",
                    "integration-preflight",
                    fixture["relative"],
                ),
                cwd=fixture["target"],
            )
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("expired after MVP-001", checked.stderr)
            self.assertEqual(
                json.loads(fixture["record_path"].read_text(encoding="utf-8"))["generation"],
                generation,
            )

    def test_local_closeout_cannot_bypass_expiry_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._prepare_verified_task(Path(directory), expiry_status="ready")
            prepared = run(
                workflow_command(
                    fixture["target"],
                    "workflow_state.py",
                    "prepare-integration",
                    fixture["relative"],
                    "--mode",
                    "local_bootstrap",
                    "--apply",
                ),
                cwd=fixture["target"],
            )
            self._assert_ok(prepared)
            commit_all(fixture["target"], "record prepared bootstrap")
            backlog = fixture["backlog_path"].read_text(encoding="utf-8").replace(
                "| ready | none |", "| done | none |", 1
            )
            fixture["backlog_path"].write_text(backlog, encoding="utf-8")
            result_commit = commit_all(fixture["target"], "expire local bootstrap")
            before_record = fixture["record_path"].read_bytes()
            before_backlog = fixture["backlog_path"].read_bytes()

            closeout = run(
                workflow_command(
                    fixture["target"],
                    "workflow_state.py",
                    "prepare-local-closeout",
                    fixture["relative"],
                    "--target-ref",
                    "refs/heads/main",
                    "--result-commit",
                    result_commit,
                    "--apply",
                ),
                cwd=fixture["target"],
            )
            self.assertNotEqual(closeout.returncode, 0)
            self.assertIn("expired after MVP-001", closeout.stderr)
            self.assertEqual(fixture["record_path"].read_bytes(), before_record)
            self.assertEqual(fixture["backlog_path"].read_bytes(), before_backlog)


if __name__ == "__main__":
    unittest.main()
