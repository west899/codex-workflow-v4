from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from support import (
    basic_v3_record,
    commit_all,
    create_baseline,
    developer_evidence_v1,
    git,
    install_project,
    record_relative,
    review_evidence_v1,
    run,
    workflow_command,
    write_record,
)


class LocalParallelCloseoutEndToEndTests(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict) -> Path:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def _assert_ok(self, result) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)

    def _runtime(self, target: Path) -> Path:
        common = Path(git(target, "rev-parse", "--git-common-dir").stdout.strip())
        if not common.is_absolute():
            common = target / common
        return common / "codex-workflow-v4"

    def _verify_and_queue(
        self,
        *,
        coordinator: Path,
        lane_path: Path,
        record: str,
        task_id: str,
        delivery_commit: str,
        lane_id: str,
        priority: int,
        evidence_root: Path,
        round_id: str,
    ) -> str:
        developer_evidence = self._write_json(
            evidence_root / f"{task_id}-{round_id}-developer.json",
            developer_evidence_v1(f"developer-{task_id}-{round_id}"),
        )
        self._assert_ok(
            run(
                workflow_command(
                    lane_path,
                    "workflow_state.py",
                    "record-developer",
                    record,
                    "--evidence-json",
                    str(developer_evidence),
                    "--delivery-commit",
                    delivery_commit,
                    "--apply",
                ),
                cwd=lane_path,
            )
        )
        current = json.loads((lane_path / record).read_text(encoding="utf-8"))
        review_evidence = self._write_json(
            evidence_root / f"{task_id}-{round_id}-review.json",
            review_evidence_v1(f"reviewer-{task_id}-{round_id}", current),
        )
        self._assert_ok(
            run(
                workflow_command(
                    lane_path,
                    "workflow_state.py",
                    "record-review",
                    record,
                    "--review-json",
                    str(review_evidence),
                    "--apply",
                ),
                cwd=lane_path,
            )
        )
        acceptance_evidence = self._write_json(
            evidence_root / f"{task_id}-{round_id}-acceptance.json",
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
                    "completed_by": "coordinator-1",
                    "completed_at": "2026-07-11T00:00:00Z",
                    "questions": {
                        "repeated_problem_found": False,
                        "guidance_gap_found": False,
                        "deterministic_check_candidate_found": False,
                    },
                    "summary": "No reusable workflow gap found.",
                },
                "rule_proposals": [],
                "remaining_risks": [],
            },
        )
        self._assert_ok(
            run(
                workflow_command(
                    lane_path,
                    "workflow_state.py",
                    "complete-task",
                    record,
                    "--acceptance-json",
                    str(acceptance_evidence),
                    "--apply",
                ),
                cwd=lane_path,
            )
        )
        self._assert_ok(
            run(workflow_command(lane_path, "workflow_check.py", "gate", record), cwd=lane_path)
        )
        self._assert_ok(
            run(
                workflow_command(lane_path, "workflow_state.py", "mark-verified", record, "--apply"),
                cwd=lane_path,
            )
        )
        current = json.loads((lane_path / record).read_text(encoding="utf-8"))
        approval = self._write_json(
            evidence_root / f"{task_id}-{round_id}-approval.json",
            {
                "kind": "local_bootstrap",
                "task_id": task_id,
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
                    lane_path,
                    "workflow_state.py",
                    "record-approval",
                    record,
                    "--approval-json",
                    str(approval),
                    "--apply",
                ),
                cwd=lane_path,
            )
        )
        self._assert_ok(
            run(
                workflow_command(
                    lane_path,
                    "workflow_state.py",
                    "prepare-integration",
                    record,
                    "--mode",
                    "local_bootstrap",
                    "--apply",
                ),
                cwd=lane_path,
            )
        )
        queued = run(
            workflow_command(
                coordinator,
                "workflow_lane.py",
                "queue",
                lane_id,
                "--priority",
                str(priority),
                "--apply",
            ),
            cwd=coordinator,
        )
        self._assert_ok(queued)
        return re.search(r"queue=(\S+)", queued.stdout).group(1)

    def _prepare_and_confirm_closeout(self, target: Path, record: str) -> str:
        result_commit = git(target, "rev-parse", "main").stdout.strip()
        prepared = run(
            workflow_command(
                target,
                "workflow_state.py",
                "prepare-local-closeout",
                record,
                "--target-ref",
                "refs/heads/main",
                "--result-commit",
                result_commit,
                "--apply",
            ),
            cwd=target,
        )
        self._assert_ok(prepared)
        closeout_commit = prepared.stdout.split("commit=", 1)[1].split()[0]
        confirmed = run(
            workflow_command(
                target,
                "workflow_state.py",
                "confirm-closeout",
                record,
                "--target-ref",
                "refs/heads/main",
                "--closeout-commit",
                closeout_commit,
                "--apply",
            ),
            cwd=target,
        )
        self._assert_ok(confirmed)
        return closeout_commit

    def test_two_local_lanes_close_serially_after_rebase_and_reverification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "project"
            self._assert_ok(install_project(target, parallel_mode="local_worktree"))
            layout_path = target / ".codex-workflow/layout.json"
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
            layout["integration_policy"]["local_bootstrap"].update(
                {
                    "enabled": True,
                    "allowed_task_ids": ["MVP-001", "MVP-002"],
                    "expires_after_task": "MVP-002",
                }
            )
            layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            backlog_path = target / ".codex-workflow/state/MVP_BACKLOG.md"
            backlog = backlog_path.read_text(encoding="utf-8").replace("| draft | none |", "| ready | none |", 1)
            backlog += "| MVP-002 | Must | Second isolated delivery | 无 | REQ-F-001 | none | ready | none | - | - |\n"
            backlog += "| OPS-001 | Should | Dependency unlock evidence | MVP-002 | REQ-F-001 | none | blocked | dependencies | - | - |\n"
            backlog_path.write_text(backlog, encoding="utf-8")
            base = create_baseline(target)
            first_record = write_record(
                target,
                basic_v3_record(
                    base,
                    task_id="MVP-001",
                    allowed_paths=["src/first/**"],
                    resources=["path:src/first"],
                ),
            )
            second_record = write_record(
                target,
                basic_v3_record(
                    base,
                    task_id="MVP-002",
                    allowed_paths=["src/second/**"],
                    resources=["path:src/second"],
                ),
            )
            commit_all(target, "authorize parallel local tasks")
            first_relative = record_relative(first_record, target)
            second_relative = record_relative(second_record, target)

            lane_a_path = root / "lane-a"
            lane_b_path = root / "lane-b"
            claimed_a = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "claim",
                    "MVP-001",
                    "--base",
                    "main",
                    "--record",
                    first_relative,
                    "--worktree",
                    str(lane_a_path),
                    "--apply",
                ),
                cwd=target,
            )
            self._assert_ok(claimed_a)
            lane_a = re.search(r"id=(\S+)", claimed_a.stdout).group(1)
            branch_a = re.search(r"branch=(\S+)", claimed_a.stdout).group(1)
            claimed_b = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "claim",
                    "MVP-002",
                    "--base",
                    "main",
                    "--record",
                    second_relative,
                    "--worktree",
                    str(lane_b_path),
                    "--apply",
                ),
                cwd=target,
            )
            self._assert_ok(claimed_b)
            lane_b = re.search(r"id=(\S+)", claimed_b.stdout).group(1)
            branch_b = re.search(r"branch=(\S+)", claimed_b.stdout).group(1)

            first_product = lane_a_path / "src/first/feature.txt"
            first_product.parent.mkdir(parents=True)
            first_product.write_text("first delivery\n", encoding="utf-8")
            first_delivery = commit_all(lane_a_path, "delivery-MVP-001")
            second_product = lane_b_path / "src/second/feature.txt"
            second_product.parent.mkdir(parents=True)
            second_product.write_text("second delivery\n", encoding="utf-8")
            second_delivery = commit_all(lane_b_path, "delivery-MVP-002")

            queue_b = self._verify_and_queue(
                coordinator=target,
                lane_path=lane_b_path,
                record=second_relative,
                task_id="MVP-002",
                delivery_commit=second_delivery,
                lane_id=lane_b,
                priority=20,
                evidence_root=root,
                round_id="first",
            )
            commit_all(lane_b_path, "queue-MVP-002")
            queue_a = self._verify_and_queue(
                coordinator=target,
                lane_path=lane_a_path,
                record=first_relative,
                task_id="MVP-001",
                delivery_commit=first_delivery,
                lane_id=lane_a,
                priority=10,
                evidence_root=root,
                round_id="first",
            )
            commit_all(lane_a_path, "queue-MVP-001")

            runtime = self._runtime(target)
            self.assertTrue((runtime / "queue" / f"{queue_a}.json").is_file())
            self.assertTrue((runtime / "queue" / f"{queue_b}.json").is_file())
            git(target, "merge", "--ff-only", branch_a)
            first_closeout = self._prepare_and_confirm_closeout(target, first_relative)
            self.assertTrue(git(target, "merge-base", "--is-ancestor", first_closeout, "main", check=False).returncode == 0)
            self.assertFalse((runtime / "queue" / f"{queue_a}.json").exists())
            self.assertFalse((runtime / "registry" / "lanes" / f"{lane_a}.json").exists())

            git(lane_b_path, "rebase", "main")
            refreshed = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "refresh-base",
                    lane_b,
                    "--base",
                    "main",
                    "--apply",
                ),
                cwd=target,
            )
            self._assert_ok(refreshed)
            refreshed_record = json.loads((lane_b_path / second_relative).read_text(encoding="utf-8"))
            self.assertEqual(refreshed_record["base_commit"], git(target, "rev-parse", "main").stdout.strip())
            self.assertEqual(refreshed_record["verification"]["status"], "pending")
            self.assertEqual(refreshed_record["integration"]["status"], "not_ready")
            self.assertEqual(refreshed_record["human_approvals"], [])
            self.assertFalse((runtime / "queue" / f"{queue_b}.json").exists())

            refreshed_delivery = commit_all(lane_b_path, "delivery-MVP-002-rerun")
            queue_b_after_refresh = self._verify_and_queue(
                coordinator=target,
                lane_path=lane_b_path,
                record=second_relative,
                task_id="MVP-002",
                delivery_commit=refreshed_delivery,
                lane_id=lane_b,
                priority=10,
                evidence_root=root,
                round_id="after-rebase",
            )
            commit_all(lane_b_path, "queue-MVP-002-after-rebase")
            self.assertTrue((runtime / "queue" / f"{queue_b_after_refresh}.json").is_file())
            git(target, "merge", "--ff-only", branch_b)
            second_closeout = self._prepare_and_confirm_closeout(target, second_relative)
            self.assertTrue(git(target, "merge-base", "--is-ancestor", second_closeout, "main", check=False).returncode == 0)

            final_first = json.loads((target / first_relative).read_text(encoding="utf-8"))
            final_second = json.loads((target / second_relative).read_text(encoding="utf-8"))
            self.assertEqual(final_first["integration"]["status"], "integrated")
            self.assertEqual(final_second["integration"]["status"], "integrated")
            statuses = {
                line.split("|")[1].strip(): line.split("|")[7].strip()
                for line in backlog_path.read_text(encoding="utf-8").splitlines()
                if line.startswith("| MVP-") or line.startswith("| OPS-")
            }
            self.assertEqual(statuses["MVP-001"], "done")
            self.assertEqual(statuses["MVP-002"], "done")
            self.assertEqual(statuses["OPS-001"], "ready")
            closeout_gate = run(
                workflow_command(target, "workflow_check.py", "closeout-gate", second_relative),
                cwd=target,
            )
            self._assert_ok(closeout_gate)
            for bucket in ("registry/lanes", "claims", "resources", "queue"):
                self.assertEqual(list((runtime / bucket).glob("*.json")), [], bucket)
            self.assertEqual(git(target, "status", "--porcelain").stdout, "")


if __name__ == "__main__":
    unittest.main()
