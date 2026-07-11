from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from support import (
    basic_v3_record,
    commit_all,
    configure_git,
    create_baseline,
    git,
    install_project,
    run,
    workflow_command,
    write_record,
)


class RemoteClaimRecoveryTests(unittest.TestCase):
    def _assert_ok(self, result) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)

    def _clone_task_branch(self, root: Path, bare: Path, branch: str, name: str) -> Path:
        clone = root / name
        self._assert_ok(run(["git", "clone", str(bare), str(clone)], cwd=root))
        configure_git(clone)
        git(clone, "fetch", "origin", f"{branch}:refs/remotes/origin/{branch}")
        if git(clone, "rev-parse", "--verify", branch, check=False).returncode == 0:
            git(clone, "switch", branch)
        else:
            git(clone, "switch", "-c", branch, f"origin/{branch}")
        return clone

    def _prepare_expired_claim(self, root: Path) -> dict[str, object]:
        seed = root / "seed"
        self._assert_ok(install_project(seed))
        layout_path = seed / ".codex-workflow/layout.json"
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        layout["remote"].update({"mode": "remote_claimed", "atomic_claims": True, "remote_name": "origin"})
        layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        create_baseline(seed)
        bare = root / "remote.git"
        self._assert_ok(run(["git", "init", "--bare", str(bare)], cwd=root))
        git(seed, "remote", "add", "origin", str(bare))
        git(seed, "push", "-u", "origin", "main")
        git(seed, "--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main")

        owner = self._clone_task_branch(root, bare, "main", "owner")
        task_id = "MVP-REMOTE"
        branch = "codex/task/MVP-REMOTE-owner"
        git(owner, "switch", "-c", branch, "main")
        base = git(owner, "rev-parse", "main").stdout.strip()
        owner_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        record = basic_v3_record(
            base,
            task_id=task_id,
            allowed_paths=["src/remote/**"],
            resources=["path:src/remote"],
        )
        record["status"] = "in_progress"
        record["phase"] = "developer"
        record["lane"].update(
            {
                "lane_id": "lane-MVP-REMOTE-owner",
                "mode": "remote_claimed",
                "branch": branch,
                "base_ref": "main",
                "base_commit": base,
                "claim_id": claim_id,
                "owner_generation": 1,
                "assignment": {
                    "assigned_owner_id": owner_id,
                    "assignment_generation": 1,
                    "assigned_at": "2026-07-11T00:00:00Z",
                    "assigned_by": "test-owner",
                },
            }
        )
        record_path = write_record(owner, record)
        task_head = commit_all(owner, "prepare remote claimed task")
        relative = record_path.relative_to(owner).as_posix()
        claimed = run(
            workflow_command(
                owner,
                "workflow_lane.py",
                "remote-claim",
                relative,
                "--lease-seconds",
                "1",
                "--apply",
            ),
            cwd=owner,
        )
        self._assert_ok(claimed)
        self.assertTrue((owner / ".git" / "codex-workflow-v3" / "lane.json").is_file())

        unpublished_path = owner / "src" / "remote" / "unpublished.txt"
        unpublished_path.parent.mkdir(parents=True, exist_ok=True)
        unpublished_path.write_text("must remain local\n", encoding="utf-8")
        unpublished_commit = commit_all(owner, "unpublished owner delivery")
        time.sleep(1.2)
        expired_heartbeat = run(
            workflow_command(owner, "workflow_lane.py", "remote-heartbeat", relative, "--apply"),
            cwd=owner,
        )
        self.assertNotEqual(expired_heartbeat.returncode, 0)
        self.assertIn("expired remote lease", expired_heartbeat.stderr)
        return {
            "bare": bare,
            "owner": owner,
            "owner_id": owner_id,
            "task_id": task_id,
            "branch": branch,
            "relative": relative,
            "claim_id": claim_id,
            "task_head": task_head,
            "unpublished_commit": unpublished_commit,
        }

    def test_expired_claim_competition_handoff_and_resume_preserve_unpublished_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._prepare_expired_claim(root)
            bare = fixture["bare"]
            branch = fixture["branch"]
            relative = fixture["relative"]
            task_id = fixture["task_id"]
            claim_ref = f"refs/heads/codex/claims/{task_id}"
            candidate_a = self._clone_task_branch(root, bare, branch, "candidate-a")
            candidate_b = self._clone_task_branch(root, bare, branch, "candidate-b")
            candidate_a_owner = str(uuid.uuid4())
            candidate_b_owner = str(uuid.uuid4())
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            command_a = workflow_command(
                candidate_a,
                "workflow_lane.py",
                "remote-takeover",
                relative,
                "--owner-id",
                candidate_a_owner,
                "--approved-by",
                "test-maintainer",
                "--approval-ref",
                "incident-001",
                "--apply",
            )
            command_b = workflow_command(
                candidate_b,
                "workflow_lane.py",
                "remote-takeover",
                relative,
                "--owner-id",
                candidate_b_owner,
                "--approved-by",
                "test-maintainer",
                "--approval-ref",
                "incident-001",
                "--apply",
            )
            process_a = subprocess.Popen(command_a, cwd=candidate_a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
            process_b = subprocess.Popen(command_b, cwd=candidate_b, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
            output_a = process_a.communicate()
            output_b = process_b.communicate()
            self.assertEqual(sorted([process_a.returncode, process_b.returncode]), [0, 1], [output_a, output_b])
            winner, winner_owner = (
                (candidate_a, candidate_a_owner)
                if process_a.returncode == 0
                else (candidate_b, candidate_b_owner)
            )

            claim = json.loads(
                git(root, "--git-dir", str(bare), "show", f"{claim_ref}:claim.json").stdout
            )
            self.assertEqual(claim["owner_id"], winner_owner)
            self.assertEqual(claim["owner_generation"], 2)
            self.assertEqual(claim["lease_revision"], 2)
            self.assertEqual(claim["transfer"]["kind"], "stale_takeover")
            self.assertEqual(claim["transfer"]["approval"]["approved_by"], "test-maintainer")
            claim_oid = git(root, "--git-dir", str(bare), "rev-parse", claim_ref).stdout.strip()
            for resource_ref in claim["resource_refs"]:
                self.assertEqual(
                    git(root, "--git-dir", str(bare), "rev-parse", resource_ref).stdout.strip(),
                    claim_oid,
                )
            transferred_record = json.loads(
                git(root, "--git-dir", str(bare), "show", f"refs/heads/{branch}:{relative}").stdout
            )
            self.assertEqual(transferred_record["lane"]["owner_generation"], 2)
            self.assertEqual(transferred_record["lane"]["assignment"]["assigned_owner_id"], winner_owner)

            stale_heartbeat = run(
                workflow_command(fixture["owner"], "workflow_lane.py", "remote-heartbeat", relative, "--apply"),
                cwd=fixture["owner"],
            )
            self.assertNotEqual(stale_heartbeat.returncode, 0)
            self.assertIn("ownership mismatch", stale_heartbeat.stderr)
            self.assertEqual(git(fixture["owner"], "rev-parse", "HEAD").stdout.strip(), fixture["unpublished_commit"])
            self.assertEqual(
                git(fixture["owner"], "cat-file", "-e", f"{fixture['unpublished_commit']}^{{commit}}", check=False).returncode,
                0,
            )

            handoff_owner = str(uuid.uuid4())
            handed_off = run(
                workflow_command(
                    winner,
                    "workflow_lane.py",
                    "remote-handoff",
                    relative,
                    "--owner-id",
                    winner_owner,
                    "--to-owner-id",
                    handoff_owner,
                    "--apply",
                ),
                cwd=winner,
            )
            self._assert_ok(handed_off)
            handoff_claim = json.loads(
                git(root, "--git-dir", str(bare), "show", f"{claim_ref}:claim.json").stdout
            )
            self.assertEqual(handoff_claim["owner_id"], handoff_owner)
            self.assertEqual(handoff_claim["owner_generation"], 3)
            self.assertEqual(handoff_claim["lease_revision"], 3)
            self.assertEqual(handoff_claim["transfer"]["kind"], "handoff")
            self.assertFalse((winner / ".git" / "codex-workflow-v3" / "lane.json").exists())

            recipient = self._clone_task_branch(root, bare, branch, "recipient")
            resumed = run(
                workflow_command(
                    recipient,
                    "workflow_lane.py",
                    "resume-remote",
                    relative,
                    "--owner-id",
                    handoff_owner,
                    "--apply",
                ),
                cwd=recipient,
            )
            self._assert_ok(resumed)
            pointer = json.loads((recipient / ".git" / "codex-workflow-v3" / "lane.json").read_text(encoding="utf-8"))
            self.assertEqual(pointer["owner_id"], handoff_owner)
            self.assertEqual(pointer["owner_generation"], 3)
            refreshed = run(
                workflow_command(recipient, "workflow_lane.py", "remote-heartbeat", relative, "--apply"),
                cwd=recipient,
            )
            self._assert_ok(refreshed)
            final_claim = json.loads(
                git(root, "--git-dir", str(bare), "show", f"{claim_ref}:claim.json").stdout
            )
            self.assertEqual(final_claim["lease_revision"], 4)


if __name__ == "__main__":
    unittest.main()
