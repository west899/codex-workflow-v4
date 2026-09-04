from __future__ import annotations

import json
import os
import subprocess
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


class PersistentRoleLockTests(unittest.TestCase):
    def _assert_ok(self, result) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)

    def _runtime(self, target: Path) -> Path:
        common = Path(git(target, "rev-parse", "--git-common-dir").stdout.strip())
        if not common.is_absolute():
            common = target / common
        return common / "codex-workflow-v4"

    def _lock_path(self, target: Path, role: str) -> Path:
        return self._runtime(target) / "locks" / f"{role}.lock.json"

    def _make_stale(self, target: Path, role: str) -> dict:
        path = self._lock_path(target, role)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["heartbeat_at"] = "2000-01-01T00:00:00Z"
        payload["expires_at"] = "2000-01-01T00:00:01Z"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def _acquire(self, target: Path, role: str) -> dict:
        acquired = run(
            workflow_command(target, "workflow_lane.py", "lock-acquire", role, "--apply"),
            cwd=target,
        )
        self._assert_ok(acquired)
        return json.loads(acquired.stdout)

    def _release(self, target: Path, role: str, payload: dict) -> None:
        released = run(
            workflow_command(
                target,
                "workflow_lane.py",
                "lock-release",
                role,
                "--token",
                payload["token"],
                "--generation",
                str(payload["generation"]),
                "--apply",
            ),
            cwd=target,
        )
        self._assert_ok(released)

    def _ready_claim_project(self, root: Path) -> tuple[Path, Path]:
        target = root / "project"
        self._assert_ok(install_project(target, parallel_mode="local_worktree"))
        backlog = target / ".codex-workflow/state/MVP_BACKLOG.md"
        backlog.write_text(
            backlog.read_text(encoding="utf-8").replace("| draft | none |", "| ready | none |", 1),
            encoding="utf-8",
        )
        base = create_baseline(target)
        record_path = write_record(target, basic_v3_record(base))
        commit_all(target, "authorize local claim")
        return target, record_path

    def _pending_local_closeout(self, root: Path) -> tuple[Path, Path, str]:
        target = root / "project"
        self._assert_ok(install_project(target))
        layout_path = target / ".codex-workflow/layout.json"
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        layout["integration_policy"]["local_bootstrap"].update(
            {
                "enabled": True,
                "allowed_task_ids": ["MVP-001"],
                "expires_after_task": "MVP-001",
            }
        )
        layout_path.write_text(
            json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        backlog_path = target / ".codex-workflow/state/MVP_BACKLOG.md"
        backlog_path.write_text(
            backlog_path.read_text(encoding="utf-8").replace(
                "| draft | none |", "| ready | none |", 1
            ),
            encoding="utf-8",
        )
        base = create_baseline(target)
        record = basic_v3_record(base)
        record["status"] = "completed"
        record["phase"] = "integration"
        record["lane"].update(
            {
                "lane_id": "lane-MVP-001-single",
                "mode": "single",
                "branch": "main",
                "base_ref": "main",
                "base_commit": base,
                "claim_id": "00000000-0000-4000-8000-000000000001",
                "owner_generation": 1,
                "assignment": {
                    "assigned_owner_id": "00000000-0000-4000-8000-000000000002",
                    "assignment_generation": 1,
                    "assigned_at": "2026-07-11T00:00:00Z",
                    "assigned_by": "test",
                },
            }
        )
        record["verification"].update(
            {
                "status": "passed",
                "delivery_commit": base,
                "delivery_hash": "delivery-test",
                "snapshot_id": "snapshot-test",
                "changed_paths": ["src/feature.txt"],
            }
        )
        record["integration"].update(
            {
                "status": "pending",
                "mode": "local_bootstrap",
                "policy_id": "IP-001",
                "source_ref": "refs/heads/main",
                "target_ref": "refs/heads/main",
            }
        )
        record_path = write_record(target, record)
        result_commit = commit_all(target, "prepare pending local integration")
        return target, record_path, result_commit

    def test_stale_coordinator_lock_blocks_claim_until_explicit_takeover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path = self._ready_claim_project(root)
            acquired = self._acquire(target, "coordinator")
            stale = self._make_stale(target, "coordinator")
            worktree = root / "lane"
            blocked = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "claim",
                    "MVP-001",
                    "--record",
                    record_relative(record_path, target),
                    "--worktree",
                    str(worktree),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("stale", blocked.stderr)
            self.assertFalse(worktree.exists())
            self.assertFalse((self._runtime(target) / "claims" / "MVP-001.json").exists())

            taken_over = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "lock-takeover",
                    "coordinator",
                    "--expected-token",
                    stale["token"],
                    "--expected-generation",
                    str(stale["generation"]),
                    "--approved-by",
                    "test-user",
                    "--approval-ref",
                    "user:test",
                    "--apply",
                ),
                cwd=target,
            )
            self._assert_ok(taken_over)
            recovered = json.loads(taken_over.stdout)
            self.assertEqual(recovered["generation"], acquired["generation"] + 1)
            self._release(target, "coordinator", recovered)
            audit = list((self._runtime(target) / "audit").glob("lock-takeover-coordinator-*.json"))
            self.assertEqual(len(audit), 1)

    def test_stale_integrator_lock_rejects_closeout_without_state_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target, record_path, result_commit = self._pending_local_closeout(Path(directory))
            self._acquire(target, "integrator")
            self._make_stale(target, "integrator")
            before_record = record_path.read_bytes()
            backlog_path = target / ".codex-workflow/state/MVP_BACKLOG.md"
            before_backlog = backlog_path.read_bytes()
            before_head = git(target, "rev-parse", "HEAD").stdout.strip()

            closeout = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "prepare-local-closeout",
                    record_relative(record_path, target),
                    "--target-ref",
                    "refs/heads/main",
                    "--result-commit",
                    result_commit,
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(closeout.returncode, 0)
            self.assertIn("Persistent integrator lock is stale", closeout.stderr)
            self.assertEqual(record_path.read_bytes(), before_record)
            self.assertEqual(backlog_path.read_bytes(), before_backlog)
            self.assertEqual(git(target, "rev-parse", "HEAD").stdout.strip(), before_head)

    def test_explicit_integrator_lease_spans_manual_closeout_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target, record_path, result_commit = self._pending_local_closeout(Path(directory))
            lease = self._acquire(target, "integrator")
            closeout = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "prepare-local-closeout",
                    record_relative(record_path, target),
                    "--target-ref",
                    "refs/heads/main",
                    "--result-commit",
                    result_commit,
                    "--integrator-token",
                    lease["token"],
                    "--integrator-generation",
                    str(lease["generation"]),
                    "--apply",
                ),
                cwd=target,
            )
            self._assert_ok(closeout)
            current = json.loads(self._lock_path(target, "integrator").read_text(encoding="utf-8"))
            self.assertEqual(current["state"], "active")
            self.assertEqual(current["token"], lease["token"])
            self.assertEqual(current["generation"], lease["generation"])
            self._release(target, "integrator", lease)

    def test_lock_heartbeat_rejects_wrong_token_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            lease = self._acquire(target, "coordinator")
            path = self._lock_path(target, "coordinator")
            before = path.read_bytes()
            heartbeat = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "lock-heartbeat",
                    "coordinator",
                    "--token",
                    "00000000-0000-4000-8000-000000000099",
                    "--generation",
                    str(lease["generation"]),
                ),
                cwd=target,
            )
            self.assertNotEqual(heartbeat.returncode, 0)
            self.assertIn("token/generation", heartbeat.stderr)
            self.assertEqual(path.read_bytes(), before)
            self._release(target, "coordinator", lease)

    def test_competing_stale_takeovers_have_one_cas_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            acquired = self._acquire(target, "coordinator")
            stale = self._make_stale(target, "coordinator")
            command = workflow_command(
                target,
                "workflow_lane.py",
                "lock-takeover",
                "coordinator",
                "--expected-token",
                stale["token"],
                "--expected-generation",
                str(stale["generation"]),
                "--approved-by",
                "test-user",
                "--approval-ref",
                "user:test",
                "--apply",
            )
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            first = subprocess.Popen(
                command,
                cwd=target,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )
            second = subprocess.Popen(
                command,
                cwd=target,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )
            outputs = [first.communicate(), second.communicate()]
            self.assertEqual(sorted([first.returncode, second.returncode]), [0, 1], outputs)
            state = json.loads(self._lock_path(target, "coordinator").read_text(encoding="utf-8"))
            self.assertEqual(state["generation"], acquired["generation"] + 1)
            self.assertEqual(state["state"], "active")
            self._release(target, "coordinator", state)


if __name__ == "__main__":
    unittest.main()
