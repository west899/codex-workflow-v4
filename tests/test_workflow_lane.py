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
    install_project,
    record_relative,
    run,
    workflow_command,
    write_record,
)


class WorkflowLaneTests(unittest.TestCase):
    def test_two_local_worktree_lanes_are_isolated_and_conflicts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "project"
            installed = install_project(target, parallel_mode="local_worktree")
            self.assertEqual(installed.returncode, 0, installed.stderr)
            base = create_baseline(target)
            first_record = write_record(
                target,
                basic_v3_record(
                    base,
                    task_id="MVP-001",
                    allowed_paths=["src/first/**", "tests/first/**"],
                    resources=["path:src/first", "path:tests/first"],
                ),
            )
            second_record = write_record(
                target,
                basic_v3_record(
                    base,
                    task_id="OPS-001",
                    allowed_paths=["src/second/**", "tests/second/**"],
                    resources=["path:src/second", "path:tests/second"],
                ),
            )
            conflict_record = write_record(
                target,
                basic_v3_record(
                    base,
                    task_id="OPS-002",
                    allowed_paths=["src/first/nested/**"],
                    resources=["path:src/first/nested"],
                ),
            )
            commit_all(target, "task contracts")

            lane_a_path = root / "lane A 中文"
            lane_b_path = root / "lane B"
            claimed_a = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "claim",
                    "MVP-001",
                    "--base",
                    "main",
                    "--record",
                    record_relative(first_record, target),
                    "--worktree",
                    str(lane_a_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(claimed_a.returncode, 0, claimed_a.stderr)
            lane_a = re.search(r"id=(\S+)", claimed_a.stdout).group(1)
            claimed_b = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "claim",
                    "OPS-001",
                    "--base",
                    "main",
                    "--record",
                    record_relative(second_record, target),
                    "--worktree",
                    str(lane_b_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(claimed_b.returncode, 0, claimed_b.stderr)
            lane_b = re.search(r"id=(\S+)", claimed_b.stdout).group(1)

            preflight_a = run(
                workflow_command(target, "workflow_check.py", "preflight", record_relative(first_record, target)),
                cwd=lane_a_path,
            )
            preflight_b = run(
                workflow_command(target, "workflow_check.py", "preflight", record_relative(second_record, target)),
                cwd=lane_b_path,
            )
            self.assertEqual(preflight_a.returncode, 0, preflight_a.stderr)
            self.assertEqual(preflight_b.returncode, 0, preflight_b.stderr)

            first_file = lane_a_path / "src/first/result.txt"
            first_file.parent.mkdir(parents=True)
            first_file.write_text("lane A only\n", encoding="utf-8")
            self.assertFalse((lane_b_path / "src/first/result.txt").exists())

            duplicate = run(
                workflow_command(target, "workflow_lane.py", "claim", "MVP-001", "--record", record_relative(first_record, target), "--worktree", str(root / "duplicate"), "--apply"),
                cwd=target,
            )
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("already has a local claim", duplicate.stderr)

            conflict = run(
                workflow_command(target, "workflow_lane.py", "claim", "OPS-002", "--record", record_relative(conflict_record, target), "--worktree", str(root / "conflict"), "--apply"),
                cwd=target,
            )
            self.assertNotEqual(conflict.returncode, 0)
            # The configured maximum of two also fails closed before a third writer.
            self.assertIn("lane limit", conflict.stderr)

            listing = run(workflow_command(target, "workflow_lane.py", "list", "--all", "--json"), cwd=target)
            rows = json.loads(listing.stdout)
            self.assertEqual({row["lane_id"] for row in rows}, {lane_a, lane_b})
            self.assertEqual({row["effective_status"] for row in rows}, {"active"})

            heartbeat = run(workflow_command(target, "workflow_lane.py", "heartbeat", "--lane", lane_a), cwd=target)
            self.assertEqual(heartbeat.returncode, 0, heartbeat.stderr)
            self.assertIn("HEARTBEAT_OK", heartbeat.stdout)

            stop_a = run(
                workflow_command(target, "codex_stop_hook.py"),
                cwd=lane_a_path,
                input_text=json.dumps({"cwd": str(lane_a_path), "stop_hook_active": False}),
            )
            payload_a = json.loads(stop_a.stdout)
            self.assertTrue(payload_a["continue"])
            self.assertIn("remains in_progress", payload_a["systemMessage"])
            stop_main = run(
                workflow_command(target, "codex_stop_hook.py"),
                cwd=target,
                input_text=json.dumps({"cwd": str(target), "stop_hook_active": False}),
            )
            payload_main = json.loads(stop_main.stdout)
            self.assertIn("2 lane(s) remain active", payload_main["systemMessage"])

            released_a = run(workflow_command(target, "workflow_lane.py", "release", lane_a, "--abandon", "--apply"), cwd=target)
            released_b = run(workflow_command(target, "workflow_lane.py", "release", lane_b, "--abandon", "--apply"), cwd=target)
            self.assertEqual(released_a.returncode, 0, released_a.stderr)
            self.assertEqual(released_b.returncode, 0, released_b.stderr)
            self.assertTrue(lane_a_path.is_dir(), "release must preserve the worktree")


if __name__ == "__main__":
    unittest.main()

