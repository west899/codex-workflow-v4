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
    git,
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

            # State-command transitions are covered elsewhere; this fixture isolates queue and recovery ownership.
            for lane_path, relative, changed_path in (
                (lane_a_path, record_relative(first_record, target), "src/first/result.txt"),
                (lane_b_path, record_relative(second_record, target), "src/second/result.txt"),
            ):
                record_path = lane_path / relative
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record["status"] = "completed"
                record["phase"] = "integration"
                record["verification"].update(
                    {
                        "status": "passed",
                        "delivery_commit": git(lane_path, "rev-parse", "HEAD").stdout.strip(),
                        "delivery_hash": f"delivery-{record['task_id']}",
                        "snapshot_id": f"snapshot-{record['task_id']}",
                        "changed_paths": [changed_path],
                    }
                )
                record["integration"].update(
                    {
                        "status": "pending",
                        "mode": "remote_pr_ci",
                        "policy_id": "IP-001",
                        "target_ref": "refs/remotes/origin/main",
                    }
                )
                record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            queued_a = run(
                workflow_command(target, "workflow_lane.py", "queue", lane_a, "--priority", "20", "--apply"),
                cwd=target,
            )
            queued_b = run(
                workflow_command(target, "workflow_lane.py", "queue", lane_b, "--priority", "10", "--apply"),
                cwd=target,
            )
            self.assertEqual(queued_a.returncode, 0, queued_a.stderr)
            self.assertEqual(queued_b.returncode, 0, queued_b.stderr)
            queue_a = re.search(r"queue=(\S+)", queued_a.stdout).group(1)
            queue_b = re.search(r"queue=(\S+)", queued_b.stdout).group(1)
            queued_rows = json.loads(run(workflow_command(target, "workflow_lane.py", "list", "--all", "--json"), cwd=target).stdout)
            self.assertEqual({row["effective_status"] for row in queued_rows}, {"queued"})

            registry_path = target / ".git/codex-workflow-v4/registry/lanes" / f"{lane_a}.json"
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            previous_generation = registry["owner_generation"]
            registry["expires_at"] = "2000-01-01T00:00:00Z"
            registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stale_heartbeat = run(workflow_command(target, "workflow_lane.py", "heartbeat", "--lane", lane_a), cwd=target)
            self.assertNotEqual(stale_heartbeat.returncode, 0)
            self.assertIn("Heartbeat refuses a stale lane", stale_heartbeat.stderr)
            taken_over = run(
                workflow_command(target, "workflow_lane.py", "recover", lane_a, "--takeover", "--apply"),
                cwd=target,
            )
            self.assertEqual(taken_over.returncode, 0, taken_over.stderr)
            recovered_registry = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(recovered_registry["owner_generation"], previous_generation + 1)
            recovered_record = json.loads((lane_a_path / record_relative(first_record, target)).read_text(encoding="utf-8"))
            self.assertEqual(recovered_record["lane"]["owner_generation"], previous_generation + 1)
            lane_git_dir = Path(git(lane_a_path, "rev-parse", "--git-dir").stdout.strip())
            if not lane_git_dir.is_absolute():
                lane_git_dir = lane_a_path / lane_git_dir
            pointer = json.loads((lane_git_dir / "codex-workflow-v4/lane.json").read_text(encoding="utf-8"))
            self.assertEqual(pointer["owner_generation"], previous_generation + 1)
            queue_payload = json.loads((target / ".git/codex-workflow-v4/queue" / f"{queue_a}.json").read_text(encoding="utf-8"))
            self.assertEqual(queue_payload["owner_generation"], previous_generation + 1)

            runtime_root = target / ".git/codex-workflow-v4"
            registry_before_rebuild = registry_path.read_bytes()
            dry_rebuild = run(workflow_command(target, "workflow_lane.py", "rebuild"), cwd=target)
            self.assertEqual(dry_rebuild.returncode, 0, dry_rebuild.stderr)
            self.assertEqual(registry_path.read_bytes(), registry_before_rebuild)
            registry_path.write_text("{broken", encoding="utf-8")
            rebuilt = run(workflow_command(target, "workflow_lane.py", "rebuild", "--apply"), cwd=target)
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
            self.assertIn("LANE_REGISTRY_REBUILT lanes=2 queues=2", rebuilt.stdout)
            self.assertTrue(list((runtime_root / "backups").glob("rebuild-*")))
            rebuilt_rows = json.loads(run(workflow_command(target, "workflow_lane.py", "list", "--all", "--json"), cwd=target).stdout)
            self.assertEqual({row["effective_status"] for row in rebuilt_rows}, {"stale"})
            rebuilt_queue_a = json.loads((runtime_root / "queue" / f"{queue_a}.json").read_text(encoding="utf-8"))
            rebuilt_queue_b = json.loads((runtime_root / "queue" / f"{queue_b}.json").read_text(encoding="utf-8"))
            self.assertEqual(rebuilt_queue_a["owner_generation"], previous_generation + 1)
            self.assertEqual(rebuilt_queue_b["owner_generation"], 1)

            recovered_after_rebuild = run(
                workflow_command(target, "workflow_lane.py", "recover", lane_b, "--takeover", "--apply"),
                cwd=target,
            )
            self.assertEqual(recovered_after_rebuild.returncode, 0, recovered_after_rebuild.stderr)
            rebuilt_queue_b = json.loads((runtime_root / "queue" / f"{queue_b}.json").read_text(encoding="utf-8"))
            self.assertEqual(rebuilt_queue_b["owner_generation"], 2)

            stop_a = run(
                workflow_command(target, "codex_stop_hook.py"),
                cwd=lane_a_path,
                input_text=json.dumps({"cwd": str(lane_a_path), "stop_hook_active": False}),
            )
            payload_a = json.loads(stop_a.stdout)
            self.assertTrue(payload_a["continue"])
            self.assertIn("is queued", payload_a["systemMessage"])
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

    def test_v3_expand_resources_keeps_exact_lane_and_scope_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "project"
            installed = install_project(target, parallel_mode="local_worktree")
            self.assertEqual(installed.returncode, 0, installed.stderr)
            base = create_baseline(target)
            record_path = write_record(
                target,
                basic_v3_record(
                    base,
                    task_id="MVP-EXPAND-V3",
                    allowed_paths=["src/expand/**", "tests/expand/**"],
                    resources=["path:src/expand", "path:tests/expand"],
                ),
            )
            commit_all(target, "authorized V3 expand fixture")
            lane_path = root / "lane-v3-expand"
            claimed = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "claim",
                    "MVP-EXPAND-V3",
                    "--base",
                    "main",
                    "--record",
                    record_relative(record_path, target),
                    "--worktree",
                    str(lane_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(claimed.returncode, 0, claimed.stderr)
            lane_id = re.search(r"id=(\S+)", claimed.stdout).group(1)
            expanded = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "expand-resources",
                    lane_id,
                    "--add",
                    "api:expand-extra",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(expanded.returncode, 0, expanded.stderr)
            relative = record_relative(record_path, target)
            after = json.loads((lane_path / relative).read_text(encoding="utf-8"))
            self.assertEqual(after["lane"]["resource_keys"], after["scope"]["resource_keys"])
            self.assertIn("api:expand-extra", after["scope"]["resource_keys"])
            self.assertIn("api:expand-extra", after["lane"]["resource_keys"])
            preflight = run(
                workflow_command(lane_path, "workflow_check.py", "preflight", relative),
                cwd=lane_path,
            )
            self.assertEqual(preflight.returncode, 0, preflight.stderr)

    def test_queue_rejects_overlapping_verified_paths_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "project"
            self.assertEqual(install_project(target, parallel_mode="local_worktree").returncode, 0)
            base = create_baseline(target)
            first_record = write_record(
                target,
                basic_v3_record(
                    base,
                    task_id="MVP-001",
                    allowed_paths=["src/shared/**"],
                    resources=["api:first"],
                ),
            )
            second_record = write_record(
                target,
                basic_v3_record(
                    base,
                    task_id="MVP-002",
                    allowed_paths=["src/shared/**"],
                    resources=["api:second"],
                ),
            )
            commit_all(target, "overlap task contracts")
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
                    "--record",
                    first_relative,
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
                    "MVP-002",
                    "--record",
                    second_relative,
                    "--worktree",
                    str(lane_b_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(claimed_b.returncode, 0, claimed_b.stderr)
            lane_b = re.search(r"id=(\S+)", claimed_b.stdout).group(1)

            for lane_path, relative, content, changed_path in (
                (lane_a_path, first_relative, "first\n", "src/shared/feature"),
                (lane_b_path, second_relative, "second\n", "src/shared/feature/child.txt"),
            ):
                product = lane_path / changed_path
                product.parent.mkdir(parents=True, exist_ok=True)
                product.write_text(content, encoding="utf-8")
                delivery_commit = commit_all(lane_path, f"delivery {lane_path.name}")
                record_path = lane_path / relative
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record["status"] = "completed"
                record["phase"] = "integration"
                record["verification"].update(
                    {
                        "status": "passed",
                        "delivery_commit": delivery_commit,
                        "delivery_hash": f"delivery-{record['task_id']}",
                        "snapshot_id": f"snapshot-{record['task_id']}",
                        "changed_paths": [changed_path],
                    }
                )
                record["integration"].update(
                    {
                        "status": "pending",
                        "mode": "remote_pr_ci",
                        "policy_id": "IP-001",
                        "target_ref": "refs/remotes/origin/main",
                    }
                )
                record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            queued_a = run(
                workflow_command(target, "workflow_lane.py", "queue", lane_a, "--apply"),
                cwd=target,
            )
            self.assertEqual(queued_a.returncode, 0, queued_a.stderr)
            second_record_path = lane_b_path / second_relative
            runtime = target / ".git/codex-workflow-v4"
            second_registry = runtime / "registry/lanes" / f"{lane_b}.json"
            before_record = second_record_path.read_bytes()
            before_registry = second_registry.read_bytes()
            queued_b = run(
                workflow_command(target, "workflow_lane.py", "queue", lane_b, "--apply"),
                cwd=target,
            )
            self.assertNotEqual(queued_b.returncode, 0)
            self.assertIn("actual changed paths overlap", queued_b.stderr)
            self.assertIn("src/shared/feature <-> src/shared/feature/child.txt", queued_b.stderr)
            self.assertEqual(second_record_path.read_bytes(), before_record)
            self.assertEqual(second_registry.read_bytes(), before_registry)
            queue_files = list((runtime / "queue").glob("*.json"))
            self.assertEqual(len(queue_files), 1)
            queued_payload = json.loads(queue_files[0].read_text(encoding="utf-8"))
            self.assertEqual(queued_payload["changed_paths"], ["src/shared/feature"])


if __name__ == "__main__":
    unittest.main()
