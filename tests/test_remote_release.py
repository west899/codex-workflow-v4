from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from support import (
    basic_v3_record,
    closeout_fingerprint,
    commit_all,
    configure_git,
    create_baseline,
    git,
    install_project,
    run,
    workflow_command,
    write_record,
)


class RemoteReleaseTests(unittest.TestCase):
    def _assert_ok(self, result) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)

    def _remote_oid(self, root: Path, bare: Path, reference: str) -> str | None:
        result = run(
            ["git", "--git-dir", str(bare), "rev-parse", "--verify", reference],
            cwd=root,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def _prepare_integrated_claim(self, root: Path, *, publish_target: bool) -> dict[str, object]:
        seed = root / "seed"
        self._assert_ok(install_project(seed))
        layout_path = seed / ".codex-workflow/layout.json"
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        layout["remote"].update(
            {"mode": "remote_claimed", "atomic_claims": True, "remote_name": "origin"}
        )
        layout_path.write_text(
            json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        backlog_path = seed / ".codex-workflow/state/MVP_BACKLOG.md"
        backlog_path.write_text(
            backlog_path.read_text(encoding="utf-8").replace(
                "| draft | none |", "| ready | none |", 1
            ),
            encoding="utf-8",
        )
        base = create_baseline(seed)

        bare = root / "remote.git"
        self._assert_ok(run(["git", "init", "--bare", str(bare)], cwd=root))
        git(seed, "remote", "add", "origin", str(bare))
        git(seed, "push", "-u", "origin", "main")
        git(seed, "--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main")

        owner = root / "owner"
        self._assert_ok(run(["git", "clone", str(bare), str(owner)], cwd=root))
        configure_git(owner)
        branch = "codex/task/MVP-001-release"
        git(owner, "switch", "-c", branch, "main")
        owner_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        record = basic_v3_record(
            base,
            task_id="MVP-001",
            allowed_paths=["src/release/**"],
            resources=["path:src/release", "api:release"],
        )
        record["status"] = "in_progress"
        record["phase"] = "developer"
        record["lane"].update(
            {
                "lane_id": "lane-MVP-001-release",
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
                    "assigned_by": "test-coordinator",
                },
            }
        )
        record_path = write_record(owner, record)
        relative = record_path.relative_to(owner).as_posix()
        task_head = commit_all(owner, "prepare remote release task")
        claimed = run(
            workflow_command(
                owner,
                "workflow_lane.py",
                "remote-claim",
                relative,
                "--lease-seconds",
                "300",
                "--apply",
            ),
            cwd=owner,
        )
        self._assert_ok(claimed)

        claim_ref = "refs/heads/codex/claims/MVP-001"
        claim = json.loads(
            git(root, "--git-dir", str(bare), "show", f"{claim_ref}:claim.json").stdout
        )
        refs = [claim_ref, *claim["resource_refs"]]
        claim_oid = self._remote_oid(root, bare, claim_ref)
        self.assertIsNotNone(claim_oid)
        for reference in refs:
            self.assertEqual(self._remote_oid(root, bare, reference), claim_oid)

        observer = root / "observer"
        self._assert_ok(run(["git", "clone", str(bare), str(observer)], cwd=root))
        configure_git(observer)
        git(observer, "fetch", "--prune", "origin")
        self.assertTrue(
            git(
                observer,
                "for-each-ref",
                "refs/remotes/origin/codex/claims",
                "--format=%(refname)",
            ).stdout.strip()
        )

        backlog_path = owner / ".codex-workflow/state/MVP_BACKLOG.md"
        backlog = backlog_path.read_text(encoding="utf-8").replace(
            "| ready | none |", "| done | none |", 1
        )
        backlog_path.write_text(backlog, encoding="utf-8")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["status"] = "completed"
        record["phase"] = "integration"
        record["verification"].update(
            {
                "status": "passed",
                "delivery_commit": task_head,
                "delivery_hash": "1" * 64,
                "patch_hash": "2" * 64,
                "snapshot_id": "3" * 64,
            }
        )
        record["integration"].update(
            {
                "status": "integrated",
                "mode": "remote_pr_ci",
                "policy_id": "IP-001",
                "source_ref": f"refs/heads/{branch}",
                "target_ref": "refs/remotes/origin/main",
                "target_parent": base,
                "pr_head_commit": task_head,
                "result_commit": task_head,
                "merge_strategy": "ff",
                "closeout_commit": None,
                "pr_url": "https://example.invalid/pull/1",
                "ci_checks": [{"name": "test", "status": "success"}],
                "evidence": [{"kind": "remote_ff", "verified_at": "2026-07-11T00:00:00Z"}],
            }
        )
        record["integration"]["closeout_state_fingerprint"] = closeout_fingerprint(
            record, backlog
        )
        record_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        closeout_commit = commit_all(owner, "remote closeout state")
        git(owner, "push", "origin", f"HEAD:refs/heads/{branch}")
        if publish_target:
            self._publish_target(owner, observer, closeout_commit)

        return {
            "root": root,
            "bare": bare,
            "owner": owner,
            "observer": observer,
            "relative": relative,
            "branch": branch,
            "closeout_commit": closeout_commit,
            "claim_ref": claim_ref,
            "resource_refs": claim["resource_refs"],
            "refs": refs,
        }

    def _publish_target(self, owner: Path, observer: Path, closeout_commit: str) -> None:
        git(owner, "push", "origin", f"{closeout_commit}:refs/heads/main")
        git(owner, "fetch", "origin", "main")
        git(observer, "fetch", "--prune", "origin")

    def test_release_requires_remote_visible_closeout_and_prunes_refs_in_another_clone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._prepare_integrated_claim(Path(directory), publish_target=False)
            owner = fixture["owner"]
            observer = fixture["observer"]
            relative = fixture["relative"]
            bare = fixture["bare"]
            root = fixture["root"]

            invisible = run(
                workflow_command(owner, "workflow_lane.py", "remote-release", relative),
                cwd=owner,
            )
            self.assertNotEqual(invisible.returncode, 0)
            self.assertIn("remote target", invisible.stderr.lower())
            for reference in fixture["refs"]:
                self.assertIsNotNone(self._remote_oid(root, bare, reference))

            self._publish_target(owner, observer, fixture["closeout_commit"])
            dry_run = run(
                workflow_command(owner, "workflow_lane.py", "remote-release", relative),
                cwd=owner,
            )
            self._assert_ok(dry_run)
            projection = json.loads(dry_run.stdout)
            self.assertFalse(projection["apply"])
            self.assertEqual(projection["target_ref"], "refs/remotes/origin/main")
            self.assertEqual(set(projection["delete_refs"]), set(fixture["refs"]))
            expected_oid = projection["expected_oid"]

            missing_expected = run(
                workflow_command(
                    owner,
                    "workflow_lane.py",
                    "remote-release",
                    relative,
                    "--apply",
                ),
                cwd=owner,
            )
            self.assertNotEqual(missing_expected.returncode, 0)
            self.assertIn("requires --expected-claim-oid", missing_expected.stderr)
            for reference in fixture["refs"]:
                self.assertEqual(self._remote_oid(root, bare, reference), expected_oid)

            released = run(
                workflow_command(
                    owner,
                    "workflow_lane.py",
                    "remote-release",
                    relative,
                    "--expected-claim-oid",
                    expected_oid,
                    "--apply",
                ),
                cwd=owner,
            )
            self._assert_ok(released)
            self.assertIn("REMOTE_RELEASED", released.stdout)
            for reference in fixture["refs"]:
                self.assertIsNone(self._remote_oid(root, bare, reference))
            self.assertIsNotNone(
                self._remote_oid(root, bare, f"refs/heads/{fixture['branch']}")
            )

            git(observer, "fetch", "--prune", "origin")
            remaining = git(
                observer,
                "for-each-ref",
                "refs/remotes/origin/codex/claims",
                "refs/remotes/origin/codex/resources",
                "--format=%(refname)",
            ).stdout.strip()
            self.assertEqual(remaining, "")
            self.assertFalse((owner / ".git/codex-workflow-v4/lane.json").exists())
            audit = owner / ".git/codex-workflow-v4/audit/remote-release-MVP-001.json"
            self.assertTrue(audit.is_file())
            audit_payload = json.loads(audit.read_text(encoding="utf-8"))
            self.assertEqual(audit_payload["expected_claim_oid"], expected_oid)
            self.assertEqual(set(audit_payload["released_refs"]), set(fixture["refs"]))

    def test_stale_expected_oid_is_a_cas_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._prepare_integrated_claim(Path(directory), publish_target=True)
            owner = fixture["owner"]
            relative = fixture["relative"]
            root = fixture["root"]
            bare = fixture["bare"]

            historical = json.loads((owner / relative).read_text(encoding="utf-8"))
            self.assertNotIn("evidence_contract_version", historical["developer"])
            closeout_gate = run(
                workflow_command(owner, "workflow_check.py", "closeout-gate", relative),
                cwd=owner,
            )
            self._assert_ok(closeout_gate)

            dry_run = run(
                workflow_command(owner, "workflow_lane.py", "remote-release", relative),
                cwd=owner,
            )
            self._assert_ok(dry_run)
            stale_oid = json.loads(dry_run.stdout)["expected_oid"]
            heartbeat = run(
                workflow_command(
                    owner,
                    "workflow_lane.py",
                    "remote-heartbeat",
                    relative,
                    "--lease-seconds",
                    "300",
                    "--apply",
                ),
                cwd=owner,
            )
            self._assert_ok(heartbeat)
            current_oid = self._remote_oid(root, bare, fixture["claim_ref"])
            self.assertNotEqual(current_oid, stale_oid)

            stale_release = run(
                workflow_command(
                    owner,
                    "workflow_lane.py",
                    "remote-release",
                    relative,
                    "--expected-claim-oid",
                    stale_oid,
                    "--apply",
                ),
                cwd=owner,
            )
            self.assertNotEqual(stale_release.returncode, 0)
            self.assertIn("expected claim OID", stale_release.stderr)
            for reference in fixture["refs"]:
                self.assertEqual(self._remote_oid(root, bare, reference), current_oid)


if __name__ == "__main__":
    unittest.main()
