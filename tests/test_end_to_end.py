from __future__ import annotations

import json
import os
import subprocess
import tempfile
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
    record_relative,
    run,
    workflow_command,
    write_record,
)


class RemoteClaimEndToEndTests(unittest.TestCase):
    def _prepare_clone(self, clone: Path, suffix: str) -> str:
        configure_git(clone)
        branch = f"codex/task/MVP-REMOTE-{suffix}"
        git(clone, "switch", "-c", branch, "main")
        base = git(clone, "rev-parse", "main").stdout.strip()
        record = basic_v3_record(
            base,
            task_id="MVP-REMOTE",
            allowed_paths=["src/remote/**"],
            resources=["path:src/remote"],
        )
        owner = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        record["status"] = "in_progress"
        record["phase"] = "developer"
        record["lane"].update(
            {
                "lane_id": f"lane-MVP-REMOTE-{suffix}",
                "mode": "remote_claimed",
                "branch": branch,
                "base_ref": "main",
                "base_commit": base,
                "claim_id": claim_id,
                "owner_generation": 1,
                "assignment": {
                    "assigned_owner_id": owner,
                    "assignment_generation": 1,
                    "assigned_at": "2026-07-11T00:00:00Z",
                    "assigned_by": "test-coordinator",
                },
            }
        )
        write_record(clone, record)
        commit_all(clone, f"candidate {suffix}")
        return ".codex-workflow/state/runs/MVP-REMOTE.json"

    def test_bare_remote_two_clone_competition_has_one_atomic_winner_and_cas_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "seed"
            self.assertEqual(install_project(seed).returncode, 0)
            layout_path = seed / ".codex-workflow/layout.json"
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
            layout["remote"].update({"mode": "remote_claimed", "atomic_claims": True, "remote_name": "origin"})
            layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            create_baseline(seed)
            bare = root / "remote.git"
            run(["git", "init", "--bare", str(bare)], cwd=root)
            git(seed, "remote", "add", "origin", str(bare))
            git(seed, "push", "-u", "origin", "main")
            clone_a = root / "clone-a"
            clone_b = root / "clone-b"
            run(["git", "clone", str(bare), str(clone_a)], cwd=root)
            run(["git", "clone", str(bare), str(clone_b)], cwd=root)
            # A bare repository initialized without a symbolic main HEAD may clone
            # without checking out; explicitly establish the same base in both.
            for clone in (clone_a, clone_b):
                if run(["git", "rev-parse", "--verify", "main"], cwd=clone).returncode != 0:
                    git(clone, "switch", "-c", "main", "origin/main")
            record_a = self._prepare_clone(clone_a, "a")
            record_b = self._prepare_clone(clone_b, "b")

            command_a = workflow_command(clone_a, "workflow_lane.py", "remote-claim", record_a, "--apply")
            command_b = workflow_command(clone_b, "workflow_lane.py", "remote-claim", record_b, "--apply")
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            process_a = subprocess.Popen(command_a, cwd=clone_a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
            process_b = subprocess.Popen(command_b, cwd=clone_b, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
            output_a = process_a.communicate()
            output_b = process_b.communicate()
            self.assertEqual(sorted([process_a.returncode, process_b.returncode]), [0, 1], [output_a, output_b])
            winner, record = (clone_a, record_a) if process_a.returncode == 0 else (clone_b, record_b)
            loser_output = output_b if process_a.returncode == 0 else output_a
            self.assertTrue("Atomic remote claim failed" in loser_output[1] or "already exists" in loser_output[1])

            claim_ref = "refs/heads/codex/claims/MVP-REMOTE"
            claim_json = run(["git", "--git-dir", str(bare), "show", f"{claim_ref}:claim.json"], cwd=root)
            self.assertEqual(claim_json.returncode, 0, claim_json.stderr)
            claim = json.loads(claim_json.stdout)
            self.assertEqual(claim["lease_revision"], 1)
            self.assertEqual(len(claim["resource_refs"]), 1)

            heartbeat = run(
                workflow_command(winner, "workflow_lane.py", "remote-heartbeat", record, "--apply"),
                cwd=winner,
            )
            self.assertEqual(heartbeat.returncode, 0, heartbeat.stderr)
            advanced_json = run(["git", "--git-dir", str(bare), "show", f"{claim_ref}:claim.json"], cwd=root)
            advanced = json.loads(advanced_json.stdout)
            self.assertEqual(advanced["lease_revision"], 2)
            self.assertEqual(advanced["claim_id"], claim["claim_id"])
            self.assertEqual(advanced["owner_generation"], claim["owner_generation"])

    def test_remote_claim_fails_closed_when_server_does_not_advertise_atomic_push(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "seed"
            self.assertEqual(install_project(seed).returncode, 0)
            layout_path = seed / ".codex-workflow/layout.json"
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
            layout["remote"].update({"mode": "remote_claimed", "atomic_claims": True, "remote_name": "origin"})
            layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            create_baseline(seed)
            bare = root / "remote.git"
            run(["git", "init", "--bare", str(bare)], cwd=root)
            run(["git", "--git-dir", str(bare), "config", "receive.advertiseAtomic", "false"], cwd=root)
            git(seed, "remote", "add", "origin", str(bare))
            git(seed, "push", "origin", "main")
            clone = root / "clone"
            run(["git", "clone", str(bare), str(clone)], cwd=root)
            if run(["git", "rev-parse", "--verify", "main"], cwd=clone).returncode != 0:
                git(clone, "switch", "-c", "main", "origin/main")
            record = self._prepare_clone(clone, "atomic-off")
            claimed = run(workflow_command(clone, "workflow_lane.py", "remote-claim", record, "--apply"), cwd=clone)
            self.assertNotEqual(claimed.returncode, 0)
            self.assertIn("Atomic remote claim failed", claimed.stderr)
            refs = run(["git", "--git-dir", str(bare), "for-each-ref", "refs/heads/codex/claims", "--format=%(refname)"], cwd=root)
            self.assertEqual(refs.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()

