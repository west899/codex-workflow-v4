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


class LocalFaultRecoveryTests(unittest.TestCase):
    def _assert_ok(self, result) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)

    def _runtime(self, target: Path) -> Path:
        common = Path(git(target, "rev-parse", "--git-common-dir").stdout.strip())
        if not common.is_absolute():
            common = target / common
        return common / "codex-workflow-v4"

    def _pointer(self, lane_path: Path) -> Path:
        git_dir = Path(git(lane_path, "rev-parse", "--git-dir").stdout.strip())
        if not git_dir.is_absolute():
            git_dir = lane_path / git_dir
        return git_dir / "codex-workflow-v4" / "lane.json"

    def _create_verified_lane(
        self,
        *,
        root: Path,
        task_id: str,
        changed_path: str,
        integration_mode: str,
    ) -> dict[str, object]:
        target = root / "project"
        self._assert_ok(install_project(target, parallel_mode="local_worktree"))
        if integration_mode == "local_bootstrap":
            layout_path = target / ".codex-workflow/layout.json"
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
            layout["integration_policy"]["local_bootstrap"].update(
                {
                    "enabled": True,
                    "allowed_task_ids": [task_id],
                    "expires_after_task": task_id,
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
        record_path = write_record(
            target,
            basic_v3_record(
                base,
                task_id=task_id,
                allowed_paths=[changed_path],
                resources=[f"path:{changed_path.rsplit('/', 1)[0]}"],
            ),
        )
        commit_all(target, "authorize fault-recovery task")
        relative = record_relative(record_path, target)
        lane_path = root / "lane"
        claimed = run(
            workflow_command(
                target,
                "workflow_lane.py",
                "claim",
                task_id,
                "--record",
                relative,
                "--worktree",
                str(lane_path),
                "--apply",
            ),
            cwd=target,
        )
        self._assert_ok(claimed)
        lane_id = re.search(r"id=(\S+)", claimed.stdout).group(1)
        branch = re.search(r"branch=(\S+)", claimed.stdout).group(1)

        product = lane_path / changed_path
        product.parent.mkdir(parents=True, exist_ok=True)
        product.write_text(f"{task_id} delivery\n", encoding="utf-8")
        delivery_commit = commit_all(lane_path, f"delivery {task_id}")
        lane_record_path = lane_path / relative
        record = json.loads(lane_record_path.read_text(encoding="utf-8"))
        record["status"] = "completed"
        record["phase"] = "integration"
        record["verification"].update(
            {
                "status": "passed",
                "delivery_commit": delivery_commit,
                "delivery_hash": f"delivery-{task_id}",
                "snapshot_id": f"snapshot-{task_id}",
                "changed_paths": [changed_path],
            }
        )
        record["integration"].update(
            {
                "status": "pending",
                "mode": integration_mode,
                "policy_id": "IP-001",
                "source_ref": f"refs/heads/{branch}",
                "target_ref": (
                    "refs/heads/main"
                    if integration_mode == "local_bootstrap"
                    else "refs/remotes/origin/main"
                ),
            }
        )
        lane_record_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "target": target,
            "lane_path": lane_path,
            "relative": relative,
            "lane_id": lane_id,
            "branch": branch,
            "delivery_commit": delivery_commit,
        }

    def _queue(self, fixture: dict[str, object]) -> str:
        target = fixture["target"]
        queued = run(
            workflow_command(
                target,
                "workflow_lane.py",
                "queue",
                fixture["lane_id"],
                "--apply",
            ),
            cwd=target,
        )
        self._assert_ok(queued)
        return re.search(r"queue=(\S+)", queued.stdout).group(1)

    def _assert_branch_and_worktree_preserved(self, fixture: dict[str, object]) -> None:
        self.assertTrue(fixture["lane_path"].is_dir())
        branch = fixture["branch"]
        self.assertEqual(
            git(fixture["target"], "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode,
            0,
        )

    def test_queue_interruption_rebuilds_and_requires_explicit_takeover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._create_verified_lane(
                root=Path(directory),
                task_id="MVP-001",
                changed_path="src/queue/feature.txt",
                integration_mode="remote_pr_ci",
            )
            target = fixture["target"]
            lane_path = fixture["lane_path"]
            relative = fixture["relative"]
            lane_id = fixture["lane_id"]
            runtime = self._runtime(target)

            interrupted = run(
                workflow_command(target, "workflow_lane.py", "queue", lane_id, "--apply"),
                cwd=target,
                env_extra={"CODEX_WORKFLOW_TEST_FAIL_AT": "queue-after-record"},
            )
            self.assertNotEqual(interrupted.returncode, 0)
            self.assertIn("Injected workflow failure at queue-after-record", interrupted.stderr)
            queued_record = json.loads((lane_path / relative).read_text(encoding="utf-8"))
            self.assertEqual(queued_record["integration"]["status"], "queued")
            self.assertEqual(list((runtime / "queue").glob("*.json")), [])

            rebuilt = run(workflow_command(target, "workflow_lane.py", "rebuild", "--apply"), cwd=target)
            self._assert_ok(rebuilt)
            self.assertIn("LANE_REGISTRY_REBUILT lanes=1 queues=1", rebuilt.stdout)
            queue_files = list((runtime / "queue").glob("*.json"))
            self.assertEqual(len(queue_files), 1)
            rebuilt_queue = json.loads(queue_files[0].read_text(encoding="utf-8"))
            self.assertEqual(rebuilt_queue["changed_paths"], ["src/queue/feature.txt"])
            rows = json.loads(
                run(workflow_command(target, "workflow_lane.py", "list", "--all", "--json"), cwd=target).stdout
            )
            self.assertEqual(rows[0]["effective_status"], "stale")

            recovered = run(
                workflow_command(target, "workflow_lane.py", "recover", lane_id, "--takeover", "--apply"),
                cwd=target,
            )
            self._assert_ok(recovered)
            recovered_record = json.loads((lane_path / relative).read_text(encoding="utf-8"))
            self.assertEqual(recovered_record["lane"]["owner_generation"], 2)
            recovered_queue = json.loads(queue_files[0].read_text(encoding="utf-8"))
            self.assertEqual(recovered_queue["owner_generation"], 2)
            self._assert_branch_and_worktree_preserved(fixture)

    def test_confirm_interruption_reconciles_partial_runtime_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._create_verified_lane(
                root=Path(directory),
                task_id="MVP-001",
                changed_path="src/closeout/feature.txt",
                integration_mode="local_bootstrap",
            )
            target = fixture["target"]
            lane_path = fixture["lane_path"]
            relative = fixture["relative"]
            lane_id = fixture["lane_id"]
            queue_id = self._queue(fixture)
            commit_all(lane_path, "queue fault-recovery task")
            git(target, "merge", "--ff-only", fixture["branch"])
            result_commit = git(target, "rev-parse", "main").stdout.strip()
            prepared = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "prepare-local-closeout",
                    relative,
                    "--target-ref",
                    "refs/heads/main",
                    "--result-commit",
                    result_commit,
                    "--apply",
                ),
                cwd=target,
            )
            self._assert_ok(prepared)
            closeout_commit = re.search(r"commit=(\S+)", prepared.stdout).group(1)
            runtime = self._runtime(target)
            claim_path = runtime / "claims" / "MVP-001.json"
            registry_path = runtime / "registry" / "lanes" / f"{lane_id}.json"
            queue_path = runtime / "queue" / f"{queue_id}.json"
            pointer_path = self._pointer(lane_path)

            interrupted = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "confirm-closeout",
                    relative,
                    "--target-ref",
                    "refs/heads/main",
                    "--closeout-commit",
                    closeout_commit,
                    "--apply",
                ),
                cwd=target,
                env_extra={"CODEX_WORKFLOW_TEST_FAIL_AT": "confirm-after-first-release"},
            )
            self.assertNotEqual(interrupted.returncode, 0)
            self.assertIn("Injected workflow failure at confirm-after-first-release", interrupted.stderr)
            self.assertFalse(claim_path.exists())
            self.assertTrue(registry_path.is_file())
            self.assertTrue(queue_path.is_file())
            self.assertTrue(pointer_path.is_file())
            journal = json.loads((runtime / "audit" / "closeout-MVP-001.json").read_text(encoding="utf-8"))
            self.assertFalse(journal["confirmed"])

            reconciled = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "reconcile",
                    relative,
                    "--target-ref",
                    "refs/heads/main",
                    "--apply",
                ),
                cwd=target,
            )
            self._assert_ok(reconciled)
            for bucket in ("registry/lanes", "claims", "resources", "queue"):
                self.assertEqual(list((runtime / bucket).glob("*.json")), [], bucket)
            self.assertFalse(pointer_path.exists())
            recovered_journal = json.loads((runtime / "audit" / "closeout-MVP-001.json").read_text(encoding="utf-8"))
            self.assertTrue(recovered_journal["confirmed"])
            self._assert_branch_and_worktree_preserved(fixture)

    def test_refresh_interruption_discards_old_queue_during_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._create_verified_lane(
                root=Path(directory),
                task_id="MVP-001",
                changed_path="src/refresh/feature.txt",
                integration_mode="remote_pr_ci",
            )
            target = fixture["target"]
            lane_path = fixture["lane_path"]
            relative = fixture["relative"]
            lane_id = fixture["lane_id"]
            queue_id = self._queue(fixture)
            commit_all(lane_path, "queue refresh fault-recovery task")
            advanced = target / "docs" / "base-advance.txt"
            advanced.parent.mkdir(parents=True, exist_ok=True)
            advanced.write_text("advance base\n", encoding="utf-8")
            new_base = commit_all(target, "advance main base")
            git(lane_path, "rebase", "main")
            runtime = self._runtime(target)
            old_queue = runtime / "queue" / f"{queue_id}.json"
            self.assertTrue(old_queue.is_file())

            interrupted = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "refresh-base",
                    lane_id,
                    "--base",
                    "main",
                    "--apply",
                ),
                cwd=target,
                env_extra={"CODEX_WORKFLOW_TEST_FAIL_AT": "refresh-base-after-record"},
            )
            self.assertNotEqual(interrupted.returncode, 0)
            self.assertIn("Injected workflow failure at refresh-base-after-record", interrupted.stderr)
            refreshed_record = json.loads((lane_path / relative).read_text(encoding="utf-8"))
            self.assertEqual(refreshed_record["base_commit"], new_base)
            self.assertEqual(refreshed_record["integration"]["status"], "not_ready")
            self.assertTrue(old_queue.is_file())

            rebuilt = run(workflow_command(target, "workflow_lane.py", "rebuild", "--apply"), cwd=target)
            self._assert_ok(rebuilt)
            self.assertIn("LANE_REGISTRY_REBUILT lanes=1 queues=0", rebuilt.stdout)
            self.assertFalse(old_queue.exists())
            recovered = run(
                workflow_command(target, "workflow_lane.py", "recover", lane_id, "--takeover", "--apply"),
                cwd=target,
            )
            self._assert_ok(recovered)
            registry = json.loads((runtime / "registry" / "lanes" / f"{lane_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(registry["base_commit"], new_base)
            self.assertEqual(registry["owner_generation"], 2)
            self.assertEqual(list((runtime / "queue").glob("*.json")), [])
            self._assert_branch_and_worktree_preserved(fixture)


if __name__ == "__main__":
    unittest.main()
