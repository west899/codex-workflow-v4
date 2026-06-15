from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import basic_record, create_baseline, install_project, run, write_task


class EndToEndWorkflowTests(unittest.TestCase):
    def test_task_lifecycle_from_install_through_integration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "project"
            installed = install_project(target)
            self.assertEqual(installed.returncode, 0, installed.stderr)

            started = run(
                ["python3", "scripts/workflow_check.py", "start"],
                cwd=target,
            )
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertTrue(
                (target / ".codex-log" / "last-session-check.json").is_file()
            )
            base = create_baseline(target)

            backlog = target / "docs" / "MVP_BACKLOG.md"
            backlog.write_text(
                "# MVP Backlog\n\n"
                "> 状态：approved\n\n"
                "## 任务拆分\n\n"
                "| ID | 优先级 | 可观察交付结果 | 依赖 | 验收来源 | 风险 | 状态 | 任务记录 | 集成证据 |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| MVP-001 | Must | result.txt 可验证 | 无 | AC-001 | 无 | active | .agent/runs/e2e-task.json | - |\n"
            )

            record = basic_record(base)
            record["task_id"] = "e2e-task"
            record["source"] = {
                "type": "mvp_backlog",
                "reference": "MVP-001",
                "priority_reason": "First approved item",
            }
            task = write_task(target, record)
            task_relative = str(task.relative_to(target))

            preflight = run(
                ["python3", "scripts/workflow_check.py", "preflight", task_relative],
                cwd=target,
            )
            self.assertEqual(preflight.returncode, 0, preflight.stderr)

            record["status"] = "in_progress"
            write_task(target, record)
            in_progress_stop = self.run_stop(target)
            self.assertTrue(in_progress_stop["continue"])
            self.assertIn("in_progress", in_progress_stop["systemMessage"])

            (target / "result.txt").write_text("verified-result\n")
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

            record["status"] = "completed"
            record["acceptance"][0].update(
                {"status": "pass", "evidence": ["Exact content check passed"]}
            )
            record["developer"] = {
                "agent_id": "developer-e2e",
                "worktree_hash": worktree_hash,
                "commands": [
                    {
                        "command": "test exact result",
                        "exit_code": 0,
                        "expected_failure": False,
                        "result": "Content matched",
                    }
                ],
                "handoff": "Created and verified result.txt.",
            }
            record["review"] = {
                "agent_id": "reviewer-e2e",
                "reviewed_worktree_hash": worktree_hash,
                "status": "pass",
                "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
                "requirement_checklist": [
                    "result.txt contains the expected verified result"
                ],
                "accepted_findings": [],
                "summary": "No findings.",
            }
            record["process_retrospective"] = {
                "completed": True,
                "completed_by": "coordinator-e2e",
                "completed_at": "2026-06-15T00:10:00Z",
                "questions": {
                    "repeated_problem_found": False,
                    "guidance_gap_found": False,
                    "deterministic_check_candidate_found": False,
                },
                "summary": "No reusable process issue occurred in this fixture.",
            }
            write_task(target, record)

            gate = run(
                ["python3", "scripts/workflow_check.py", "gate", task_relative],
                cwd=target,
            )
            self.assertEqual(gate.returncode, 0, gate.stderr)

            backlog.write_text(backlog.read_text().replace("| active |", "| verified |"))
            verified_stop = self.run_stop(target)
            self.assertTrue(verified_stop["continue"])
            self.assertIn("gate passed", verified_stop["systemMessage"])

            for command in (
                ["git", "add", "result.txt", "docs/MVP_BACKLOG.md", task_relative],
                ["git", "commit", "-m", "complete MVP-001"],
            ):
                result = run(command, cwd=target)
                self.assertEqual(result.returncode, 0, result.stderr)
            merged = run(["git", "rev-parse", "HEAD"], cwd=target).stdout.strip()
            backlog.write_text(
                backlog.read_text().replace(
                    "| verified | .agent/runs/e2e-task.json | - |",
                    f"| done | .agent/runs/e2e-task.json | {merged} |",
                )
            )
            (target / ".agent" / "active-task").unlink()
            integrated_stop = self.run_stop(target)
            self.assertTrue(integrated_stop["continue"])

            manual = run(
                ["python3", "scripts/workflow_check.py", "manual"],
                cwd=target,
            )
            self.assertEqual(manual.returncode, 0, manual.stderr)
            reinstalled = install_project(target)
            self.assertEqual(reinstalled.returncode, 0, reinstalled.stderr)
            self.assertFalse((target / ".codex-workflow-backup").exists())

    @staticmethod
    def run_stop(target: Path) -> dict:
        result = run(
            ["python3", "scripts/codex_stop_hook.py"],
            cwd=target,
            input_text=json.dumps(
                {
                    "cwd": str(target),
                    "hook_event_name": "Stop",
                    "stop_hook_active": False,
                }
            ),
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        return json.loads(result.stdout)


if __name__ == "__main__":
    unittest.main()
