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
    developer_evidence_v1,
    git,
    install_project,
    record_relative,
    review_evidence_v1,
    run,
    workflow_command,
    write_record,
)


class RemoteClaimEndToEndTests(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict) -> Path:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

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

    def test_remote_pr_closeout_is_confirmed_by_a_second_clone_and_unblocks_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "seed"
            self.assertEqual(install_project(seed).returncode, 0)
            backlog_path = seed / ".codex-workflow/state/MVP_BACKLOG.md"
            backlog = backlog_path.read_text(encoding="utf-8").replace("| draft | none |", "| ready | none |", 1)
            backlog += "| OPS-001 | Should | Follow-up becomes available | MVP-001 | REQ-F-001 | none | blocked | dependencies | - | - | - |\n"
            backlog_path.write_text(backlog, encoding="utf-8")
            base = create_baseline(seed)
            record_path = write_record(
                seed,
                basic_v3_record(
                    base,
                    task_id="MVP-001",
                    allowed_paths=["src/remote/**"],
                    resources=["path:src/remote"],
                ),
            )
            commit_all(seed, "authorize remote task")
            owner_id = str(uuid.uuid4())
            relative = record_relative(record_path, seed)
            preassigned = run(
                workflow_command(seed, "workflow_lane.py", "preassign", relative, "--owner-id", owner_id, "--apply"),
                cwd=seed,
            )
            self.assertEqual(preassigned.returncode, 0, preassigned.stderr)
            assigned_main = commit_all(seed, "preassign remote task")
            assigned_record = json.loads(record_path.read_text(encoding="utf-8"))
            branch = assigned_record["lane"]["branch"]

            bare = root / "remote.git"
            initialized = run(["git", "init", "--bare", str(bare)], cwd=root)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            git(seed, "remote", "add", "origin", str(bare))
            git(seed, "push", "-u", "origin", "main")
            self.assertEqual(
                run(["git", "--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main"], cwd=root).returncode,
                0,
            )

            developer = root / "developer"
            recovery = root / "recovery"
            for clone in (developer, recovery):
                cloned = run(["git", "clone", str(bare), str(clone)], cwd=root)
                self.assertEqual(cloned.returncode, 0, cloned.stderr)
                configure_git(clone)

            wrong_branch = run(
                workflow_command(developer, "workflow_lane.py", "resume-remote", relative, "--owner-id", owner_id, "--apply"),
                cwd=developer,
            )
            self.assertNotEqual(wrong_branch.returncode, 0)
            self.assertIn("does not match remote assignment branch", wrong_branch.stderr)
            self.assertFalse((developer / ".git/codex-workflow-v3/lane.json").exists())
            git(developer, "switch", "-c", branch, "origin/main")
            resumed = run(
                workflow_command(developer, "workflow_lane.py", "resume-remote", relative, "--owner-id", owner_id, "--apply"),
                cwd=developer,
            )
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            resumed_again = run(
                workflow_command(developer, "workflow_lane.py", "resume-remote", relative, "--owner-id", owner_id, "--apply"),
                cwd=developer,
            )
            self.assertEqual(resumed_again.returncode, 0, resumed_again.stderr)
            self.assertIn("existing=true", resumed_again.stdout)
            preflight = run(workflow_command(developer, "workflow_check.py", "preflight", relative), cwd=developer)
            self.assertEqual(preflight.returncode, 0, preflight.stderr)

            product = developer / "src/remote/feature.txt"
            product.parent.mkdir(parents=True)
            product.write_text("remote delivery\n", encoding="utf-8")
            delivery_commit = commit_all(developer, "remote delivery")
            developer_evidence = self._write_json(
                root / "developer-evidence.json",
                developer_evidence_v1("developer-1"),
            )
            recorded = run(
                workflow_command(
                    developer,
                    "workflow_state.py",
                    "record-developer",
                    relative,
                    "--evidence-json",
                    str(developer_evidence),
                    "--delivery-commit",
                    delivery_commit,
                    "--apply",
                ),
                cwd=developer,
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            pending_record = json.loads((developer / relative).read_text(encoding="utf-8"))
            review_evidence = self._write_json(
                root / "review-evidence.json",
                review_evidence_v1("reviewer-1", pending_record),
            )
            reviewed = run(
                workflow_command(developer, "workflow_state.py", "record-review", relative, "--review-json", str(review_evidence), "--apply"),
                cwd=developer,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            acceptance_evidence = self._write_json(
                root / "acceptance-evidence.json",
                {
                    "acceptance": [{"id": "AC-001", "status": "passed", "evidence": ["developer command and reviewer report"]}],
                    "process_retrospective": {
                        "completed": True,
                        "completed_by": "coordinator-1",
                        "completed_at": "2026-07-11T00:00:00Z",
                        "questions": {"repeated_problem_found": False, "guidance_gap_found": False, "deterministic_check_candidate_found": False},
                        "summary": "No reusable workflow gap found.",
                    },
                    "rule_proposals": [],
                    "remaining_risks": [],
                },
            )
            completed = run(
                workflow_command(developer, "workflow_state.py", "complete-task", relative, "--acceptance-json", str(acceptance_evidence), "--apply"),
                cwd=developer,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            gate = run(workflow_command(developer, "workflow_check.py", "gate", relative), cwd=developer)
            self.assertEqual(gate.returncode, 0, gate.stderr)
            verified = run(workflow_command(developer, "workflow_state.py", "mark-verified", relative, "--apply"), cwd=developer)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            prepared = run(
                workflow_command(developer, "workflow_state.py", "prepare-integration", relative, "--mode", "remote_pr_ci", "--apply"),
                cwd=developer,
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)

            git(developer, "push", "origin", f"{delivery_commit}:refs/heads/{branch}")
            git(developer, "push", "origin", f"{delivery_commit}:refs/heads/main")
            git(developer, "fetch", "origin", "main")
            commit_all(developer, "record verified remote state")
            git(developer, "push", "origin", f"HEAD:refs/heads/{branch}")
            remote_evidence = self._write_json(
                root / "remote-evidence.json",
                {
                    "target_ref": "refs/remotes/origin/main",
                    "target_parent": assigned_main,
                    "pr_head_commit": delivery_commit,
                    "result_commit": delivery_commit,
                    "merge_strategy": "ff",
                    "pr_url": "https://example.invalid/pull/1",
                    "ci_checks": [{"name": "test", "status": "success"}],
                },
            )
            closeout = run(
                workflow_command(developer, "workflow_state.py", "prepare-remote-closeout", relative, "--evidence-json", str(remote_evidence), "--apply"),
                cwd=developer,
            )
            self.assertEqual(closeout.returncode, 0, closeout.stderr)
            closeout_commit = closeout.stdout.split("commit=", 1)[1].split()[0]
            git(developer, "push", "origin", f"{closeout_commit}:refs/heads/{branch}")
            git(developer, "push", "origin", f"{closeout_commit}:refs/heads/main")

            git(recovery, "fetch", "origin", "main")
            git(recovery, "merge", "--ff-only", "origin/main")
            confirmed = run(
                workflow_command(
                    recovery,
                    "workflow_state.py",
                    "confirm-closeout",
                    relative,
                    "--target-ref",
                    "refs/remotes/origin/main",
                    "--closeout-commit",
                    closeout_commit,
                    "--apply",
                ),
                cwd=recovery,
            )
            self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
            final_record = json.loads((recovery / relative).read_text(encoding="utf-8"))
            self.assertEqual(final_record["integration"]["status"], "integrated")
            self.assertEqual(final_record["integration"]["result_commit"], delivery_commit)
            final_backlog = (recovery / ".codex-workflow/state/MVP_BACKLOG.md").read_text(encoding="utf-8")
            statuses = {
                line.split("|")[1].strip(): line.split("|")[7].strip()
                for line in final_backlog.splitlines()
                if line.startswith("| MVP-") or line.startswith("| OPS-")
            }
            self.assertEqual(statuses["MVP-001"], "done")
            self.assertEqual(statuses["OPS-001"], "ready")
            closeout_gate = run(workflow_command(recovery, "workflow_check.py", "closeout-gate", relative), cwd=recovery)
            self.assertEqual(closeout_gate.returncode, 0, closeout_gate.stderr)
            self.assertEqual(git(recovery, "status", "--porcelain").stdout, "")


if __name__ == "__main__":
    unittest.main()
