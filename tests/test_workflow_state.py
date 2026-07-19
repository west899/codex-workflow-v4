from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import (
    basic_v3_record,
    commit_all,
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


class WorkflowStateTests(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict) -> Path:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def _prepare_delivery(self, target: Path) -> tuple[Path, str]:
        layout_path = target / ".codex-workflow/layout.json"
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        layout["integration_policy"]["local_bootstrap"].update(
            {"enabled": True, "allowed_task_ids": ["MVP-001"], "expires_after_task": "MVP-001"}
        )
        layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        backlog = target / ".codex-workflow/state/MVP_BACKLOG.md"
        backlog.write_text(backlog.read_text(encoding="utf-8").replace("| draft | none |", "| ready | none |"), encoding="utf-8")
        base = create_baseline(target)

        record = basic_v3_record(base)
        record["status"] = "in_progress"
        record["phase"] = "developer"
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
        record_path = write_record(target, record)
        product = target / "src/feature.txt"
        product.parent.mkdir(parents=True)
        product.write_text("delivery\n", encoding="utf-8")
        delivery_commit = commit_all(target, "delivery")
        return record_path, delivery_commit

    def test_default_dry_run_does_not_mutate_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            base = create_baseline(target)
            record = basic_v3_record(base)
            record_path = write_record(target, record)
            before = record_path.read_bytes()
            result = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "invalidate-integration",
                    record_relative(record_path, target),
                    "--reason",
                    "test",
                ),
                cwd=target,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(record_path.read_bytes(), before)
            self.assertIn('"apply": false', result.stdout)

    def test_aggregate_evidence_scopes_fail_without_record_mutation(self) -> None:
        cases = (
            ("declared_path_call_graph", "repository_wide"),
            ("explicit_command_set", "all_dry_runs"),
            ("explicit_command_set", "all dry-run variants"),
        )
        for scope_kind, target_name in cases:
            with self.subTest(scope_kind=scope_kind, target=target_name):
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / "project"
                    self.assertEqual(install_project(target).returncode, 0)
                    record_path, delivery_commit = self._prepare_delivery(target)
                    relative = record_relative(record_path, target)
                    payload = developer_evidence_v1("developer-scope")
                    payload["scopes"][0]["kind"] = scope_kind
                    payload["scopes"][0]["targets"] = [target_name]
                    evidence = self._write_json(target.parent / "developer.json", payload)
                    before = record_path.read_bytes()

                    result = run(
                        workflow_command(
                            target,
                            "workflow_state.py",
                            "record-developer",
                            relative,
                            "--evidence-json",
                            str(evidence),
                            "--delivery-commit",
                            delivery_commit,
                            "--apply",
                        ),
                        cwd=target,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("aggregate", result.stderr)
                    self.assertEqual(record_path.read_bytes(), before)
                    self.assertEqual(json.loads(record_path.read_text(encoding="utf-8"))["generation"], 0)

    def test_review_scope_assessment_and_fingerprint_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            record_path, delivery_commit = self._prepare_delivery(target)
            relative = record_relative(record_path, target)
            evidence = self._write_json(
                target.parent / "developer.json",
                developer_evidence_v1("developer-review-contract"),
            )
            recorded = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    relative,
                    "--evidence-json",
                    str(evidence),
                    "--delivery-commit",
                    delivery_commit,
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))

            narrowed = review_evidence_v1("reviewer-contract", current)
            narrowed["claim_assessments"][0]["assessment"] = "narrowed"
            review_path = self._write_json(target.parent / "review.json", narrowed)
            before = record_path.read_bytes()
            rejected = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-review",
                    relative,
                    "--review-json",
                    str(review_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("requires every claim assessment to be confirmed", rejected.stderr)
            self.assertEqual(record_path.read_bytes(), before)

            drifted = review_evidence_v1("reviewer-contract", current)
            drifted["claim_assessments"][0]["evidence_fingerprint"] = "0" * 64
            self._write_json(review_path, drifted)
            rejected = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-review",
                    relative,
                    "--review-json",
                    str(review_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("fingerprint", rejected.stderr)
            self.assertEqual(record_path.read_bytes(), before)

            narrowed["status"] = "changes_requested"
            self._write_json(review_path, narrowed)
            requested = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-review",
                    relative,
                    "--review-json",
                    str(review_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(requested.returncode, 0, requested.stderr)
            narrowed_record = json.loads(record_path.read_text(encoding="utf-8"))
            original_snapshot = narrowed_record["verification"]["snapshot_id"]
            original_fingerprint = narrowed_record["developer"]["claims"][0]["evidence_fingerprint"]

            corrected = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    relative,
                    "--evidence-json",
                    str(evidence),
                    "--delivery-commit",
                    delivery_commit,
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(corrected.returncode, 0, corrected.stderr)
            corrected_record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(corrected_record["verification"]["snapshot_id"], original_snapshot)
            self.assertEqual(
                corrected_record["developer"]["claims"][0]["evidence_fingerprint"],
                original_fingerprint,
            )
            self.assertEqual(corrected_record["review"]["status"], "pending")

    def test_repository_tree_scope_requires_control_path_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            record_path, delivery_commit = self._prepare_delivery(target)
            relative = record_relative(record_path, target)
            payload = developer_evidence_v1("developer-repository-tree")
            payload["scopes"][0].update({"kind": "repository_tree", "targets": ["."]})
            evidence = self._write_json(target.parent / "developer.json", payload)
            before = record_path.read_bytes()

            rejected = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    relative,
                    "--evidence-json",
                    str(evidence),
                    "--delivery-commit",
                    delivery_commit,
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("mutable_workflow_control", rejected.stderr)
            self.assertEqual(record_path.read_bytes(), before)

            payload["scopes"][0]["excluded_targets"] = ["mutable_workflow_control"]
            self._write_json(evidence, payload)
            recorded = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    relative,
                    "--evidence-json",
                    str(evidence),
                    "--delivery-commit",
                    delivery_commit,
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(
                current["developer"]["scopes"][0]["excluded_targets"],
                ["mutable_workflow_control"],
            )

    def test_two_process_generation_cas_has_exactly_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            base = create_baseline(target)
            record_path = write_record(target, basic_v3_record(base))
            relative = record_relative(record_path, target)
            command = workflow_command(
                target,
                "workflow_state.py",
                "invalidate-integration",
                relative,
                "--reason",
                "race",
                "--expected-generation",
                "0",
                "--apply",
            )
            env = __import__("os").environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            first = subprocess.Popen(command, cwd=target, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
            second = subprocess.Popen(command, cwd=target, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
            outputs = [first.communicate(), second.communicate()]
            codes = [first.returncode, second.returncode]
            self.assertEqual(sorted(codes), [0, 1], outputs)
            self.assertEqual(json.loads(record_path.read_text(encoding="utf-8"))["generation"], 1)

    def test_local_bootstrap_verified_to_done_two_phase_closeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            record_path, delivery_commit = self._prepare_delivery(target)
            relative = record_relative(record_path, target)
            evidence = self._write_json(
                target.parent / "developer.json",
                developer_evidence_v1("developer-1"),
            )
            developer = run(
                workflow_command(target, "workflow_state.py", "record-developer", relative, "--evidence-json", str(evidence), "--delivery-commit", delivery_commit, "--apply"),
                cwd=target,
            )
            self.assertEqual(developer.returncode, 0, developer.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["developer"]["evidence_contract_version"], 1)
            self.assertRegex(record["developer"]["claims"][0]["evidence_fingerprint"], r"^[0-9a-f]{64}$")
            review = self._write_json(
                target.parent / "review.json",
                review_evidence_v1("reviewer-1", record),
            )
            reviewed = run(workflow_command(target, "workflow_state.py", "record-review", relative, "--review-json", str(review), "--apply"), cwd=target)
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            acceptance = self._write_json(
                target.parent / "acceptance.json",
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
            completed = run(workflow_command(target, "workflow_state.py", "complete-task", relative, "--acceptance-json", str(acceptance), "--apply"), cwd=target)
            self.assertEqual(completed.returncode, 0, completed.stderr)

            valid_bytes = record_path.read_bytes()
            valid_record = json.loads(valid_bytes)
            legacy_record = json.loads(valid_bytes)
            legacy_record["developer"] = {
                "agent_id": "developer-legacy",
                "snapshot_id": legacy_record["verification"]["snapshot_id"],
                "commands": [
                    {
                        "command": "python -m unittest",
                        "exit_code": 0,
                        "expected_failure": False,
                        "result": "passed",
                    }
                ],
                "handoff": "Legacy handoff.",
            }
            legacy_record["review"] = {
                "agent_id": "reviewer-legacy",
                "snapshot_id": legacy_record["verification"]["snapshot_id"],
                "status": "pass",
                "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
                "requirement_checklist": ["AC-001 is observable"],
                "accepted_findings": [],
                "summary": "Legacy review passed.",
            }
            record_path.write_text(json.dumps(legacy_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            legacy_gate = run(workflow_command(target, "workflow_check.py", "gate", relative), cwd=target)
            self.assertNotEqual(legacy_gate.returncode, 0)
            self.assertIn("requires Evidence Contract v1", legacy_gate.stderr)

            drifted_record = valid_record
            drifted_record["developer"]["claims"][0]["predicate"] += " Expanded claim."
            record_path.write_text(json.dumps(drifted_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            drift_gate = run(workflow_command(target, "workflow_check.py", "gate", relative), cwd=target)
            self.assertNotEqual(drift_gate.returncode, 0)
            self.assertIn("fingerprint", drift_gate.stderr)
            record_path.write_bytes(valid_bytes)

            gate = run(workflow_command(target, "workflow_check.py", "gate", relative), cwd=target)
            self.assertEqual(gate.returncode, 0, gate.stderr)
            marked = run(workflow_command(target, "workflow_state.py", "mark-verified", relative, "--apply"), cwd=target)
            self.assertEqual(marked.returncode, 0, marked.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            approval = self._write_json(
                target.parent / "approval.json",
                {
                    "kind": "local_bootstrap",
                    "task_id": "MVP-001",
                    "target_ref": "refs/heads/main",
                    "snapshot_id": record["verification"]["snapshot_id"],
                    "delivery_hash": record["verification"]["delivery_hash"],
                    "approved_by": "test-user",
                    "approved_at": "2026-07-11T00:00:00Z",
                    "source": "user:test",
                },
            )
            approved = run(workflow_command(target, "workflow_state.py", "record-approval", relative, "--approval-json", str(approval), "--apply"), cwd=target)
            self.assertEqual(approved.returncode, 0, approved.stderr)
            prepared = run(workflow_command(target, "workflow_state.py", "prepare-integration", relative, "--mode", "local_bootstrap", "--apply"), cwd=target)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            state_commit = commit_all(target, "record verified state")
            closeout = run(
                workflow_command(target, "workflow_state.py", "prepare-local-closeout", relative, "--target-ref", "refs/heads/main", "--result-commit", state_commit, "--apply"),
                cwd=target,
            )
            self.assertEqual(closeout.returncode, 0, closeout.stderr)
            closeout_commit = closeout.stdout.split("commit=", 1)[1].split()[0]
            dry_confirm = run(
                workflow_command(target, "workflow_state.py", "confirm-closeout", relative, "--target-ref", "refs/heads/main", "--closeout-commit", closeout_commit),
                cwd=target,
            )
            self.assertEqual(dry_confirm.returncode, 0, dry_confirm.stderr)
            confirmed = run(
                workflow_command(target, "workflow_state.py", "confirm-closeout", relative, "--target-ref", "refs/heads/main", "--closeout-commit", closeout_commit, "--apply"),
                cwd=target,
            )
            self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
            final_record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(final_record["integration"]["status"], "integrated")
            backlog = (target / ".codex-workflow/state/MVP_BACKLOG.md").read_text(encoding="utf-8")
            self.assertIn("| done | none |", backlog)
            self.assertTrue(git(target, "merge-base", "--is-ancestor", closeout_commit, "main", check=False).returncode == 0)

    def test_os_releases_advisory_lock_after_holder_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "crash.lock"
            bin_path = Path(__file__).resolve().parents[1] / "payload/.codex-workflow/bin"
            snippet = (
                "import os,sys; sys.path.insert(0, sys.argv[1]); "
                "from workflow_lock import AdvisoryLock; "
                "lock=AdvisoryLock(__import__('pathlib').Path(sys.argv[2])); lock.acquire(); os._exit(0)"
            )
            crashed = run([sys.executable, "-B", "-c", snippet, str(bin_path), str(lock_path)], cwd=Path(directory))
            self.assertEqual(crashed.returncode, 0)
            probe = (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "from workflow_lock import AdvisoryLock; "
                "from pathlib import Path; "
                "lock=AdvisoryLock(Path(sys.argv[2])); lock.acquire(); lock.release(); print('ok')"
            )
            acquired = run([sys.executable, "-B", "-c", probe, str(bin_path), str(lock_path)], cwd=Path(directory))
            self.assertEqual(acquired.returncode, 0, acquired.stderr)
            self.assertIn("ok", acquired.stdout)


if __name__ == "__main__":
    unittest.main()
