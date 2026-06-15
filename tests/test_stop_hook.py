from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import basic_record, create_baseline, install_project, run, write_task


class StopHookTests(unittest.TestCase):
    def install(self, temporary: str) -> tuple[Path, str]:
        target = Path(temporary) / "project"
        installed = install_project(target)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        return target, create_baseline(target)

    def stop(self, target: Path, *, active: bool = False):
        return run(
            ["python3", "scripts/codex_stop_hook.py"],
            cwd=target,
            input_text=json.dumps(
                {
                    "cwd": str(target),
                    "hook_event_name": "Stop",
                    "stop_hook_active": active,
                }
            ),
        )

    def test_in_progress_task_can_pause_without_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target, base = self.install(temporary)
            write_task(target, basic_record(base, status="in_progress"))

            result = self.stop(target)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["continue"])
            self.assertIn("remains in_progress", payload["systemMessage"])

    def test_invalid_completed_task_blocks_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target, base = self.install(temporary)
            write_task(target, basic_record(base, status="completed"))

            first = json.loads(self.stop(target).stdout)
            self.assertEqual(first["decision"], "block")
            second = json.loads(self.stop(target, active=True).stdout)
            self.assertTrue(second["continue"])
            self.assertIn("not verified", second["systemMessage"])

    def test_verified_task_is_allowed_to_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target, base = self.install(temporary)
            (target / "feature.txt").write_text("verified behavior\n")
            record = basic_record(base, status="completed")
            record["acceptance"][0].update(
                {"status": "pass", "evidence": ["feature.txt contains the result"]}
            )
            record["developer"] = {
                "agent_id": "developer-1",
                "worktree_hash": "pending",
                "commands": [
                    {
                        "command": "test -f feature.txt",
                        "exit_code": 0,
                        "expected_failure": False,
                        "result": "feature.txt exists",
                    }
                ],
                "handoff": "Implemented and verified feature.txt.",
            }
            record["review"] = {
                "agent_id": "reviewer-1",
                "reviewed_worktree_hash": "pending",
                "status": "pass",
                "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
                "requirement_checklist": ["feature.txt contains the expected result"],
                "accepted_findings": [],
                "summary": "No findings.",
            }
            record["process_retrospective"] = {
                "completed": True,
                "completed_by": "coordinator-1",
                "completed_at": "2026-06-15T00:00:00Z",
                "questions": {
                    "repeated_problem_found": False,
                    "guidance_gap_found": False,
                    "deterministic_check_candidate_found": False,
                },
                "summary": "No reusable process issue was observed in this fixture.",
            }
            task = write_task(target, record)
            task_relative = str(task.relative_to(target))

            snapshot = run(
                ["python3", "scripts/workflow_check.py", "snapshot", task_relative],
                cwd=target,
            )
            self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
            worktree_hash = next(
                line.split("=", 1)[1]
                for line in snapshot.stdout.splitlines()
                if line.startswith("WORKTREE_SHA256=")
            )
            record["developer"]["worktree_hash"] = worktree_hash
            record["review"]["reviewed_worktree_hash"] = worktree_hash
            write_task(target, record)

            gate = run(
                ["python3", "scripts/workflow_check.py", "gate", task_relative],
                cwd=target,
            )
            self.assertEqual(gate.returncode, 0, gate.stderr)

            result = self.stop(target)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["continue"])
            self.assertIn("gate passed", payload["systemMessage"])


if __name__ == "__main__":
    unittest.main()
