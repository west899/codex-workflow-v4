from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from support import (
    PACKAGE_ROOT,
    basic_v3_record,
    commit_all,
    create_baseline,
    install_project,
    record_relative,
    run,
    workflow_command,
    write_record,
)

BIN_PATH = PACKAGE_ROOT / "payload/.codex-workflow/bin"
sys.path.insert(0, str(BIN_PATH))
from workflow_common import (  # noqa: E402
    PROVIDER_RECEIPT_SCHEMA,
    WorkflowDataError,
    validate_provider_receipt,
    validate_remote_closeout_evidence,
)


SCHEMA = PACKAGE_ROOT / "payload/.codex-workflow/schemas" / PROVIDER_RECEIPT_SCHEMA
EQUIVALENCE = PACKAGE_ROOT / "V4_PHASEC_EQUIVALENCE.md"
PLAN = PACKAGE_ROOT / "V4_PHASEC_PLAN.md"


def _receipt(commit: str = "abc123", snapshot_id: str = "a" * 64, **overrides) -> dict:
    payload = {
        "schema_version": 1,
        "provider": "github",
        "kind": "pull_request",
        "snapshot_id": snapshot_id,
        "commit_sha": commit,
        "additive_only": True,
        "substitutes_reviewer": False,
        "substitutes_local_proofs": False,
        "checks": [{"name": "test", "status": "success", "source": "github-actions"}],
        "reviews": [{"reviewer": "octocat", "state": "approved", "commit_sha": commit}],
        "protection": {
            "admin_bypass_allowed": False,
            "dismiss_stale_reviews": True,
            "require_last_push_approval": True,
        },
    }
    payload.update(overrides)
    return payload


def _evidence(base: str, delivery: str, **overrides) -> dict:
    payload = {
        "target_ref": "refs/heads/main",
        "target_parent": base,
        "pr_head_commit": delivery,
        "result_commit": delivery,
        "merge_strategy": "ff",
        "pr_url": "https://example.invalid/pull/1",
        "ci_checks": [{"name": "test", "status": "success"}],
    }
    payload.update(overrides)
    return payload


class PhaseCReceiptTests(unittest.TestCase):
    def test_valid_receipt_is_additive_only(self) -> None:
        payload = validate_provider_receipt(SCHEMA, _receipt())
        self.assertIs(payload["additive_only"], True)
        self.assertIs(payload["substitutes_reviewer"], False)

    def test_admin_bypass_and_stale_review_protection_fail(self) -> None:
        bypass = _receipt()
        bypass["protection"]["admin_bypass_allowed"] = True
        with self.assertRaisesRegex(WorkflowDataError, "EQ-005"):
            validate_provider_receipt(SCHEMA, bypass)
        stale = _receipt()
        stale["protection"]["dismiss_stale_reviews"] = False
        with self.assertRaisesRegex(WorkflowDataError, "EQ-002"):
            validate_provider_receipt(SCHEMA, stale)
        latest = _receipt()
        latest["protection"]["require_last_push_approval"] = False
        with self.assertRaisesRegex(WorkflowDataError, "EQ-002"):
            validate_provider_receipt(SCHEMA, latest)

    def test_receipt_must_bind_pr_head_commit(self) -> None:
        with self.assertRaisesRegex(WorkflowDataError, "EQ-002"):
            validate_remote_closeout_evidence(
                _evidence("b", "d", provider_receipt=_receipt("other")),
                schema_path=SCHEMA,
            )
        mismatched_review = _receipt("d")
        mismatched_review["reviews"] = [
            {"reviewer": "octocat", "state": "approved", "commit_sha": "other"}
        ]
        with self.assertRaisesRegex(WorkflowDataError, "EQ-002"):
            validate_remote_closeout_evidence(
                _evidence("b", "d", provider_receipt=mismatched_review),
                schema_path=SCHEMA,
            )

    def test_skipped_check_and_reviewer_substitution_fail(self) -> None:
        skipped = _receipt()
        skipped["checks"] = [{"name": "test", "status": "skipped", "source": "github-actions"}]
        with self.assertRaisesRegex(WorkflowDataError, "EQ-004"):
            validate_provider_receipt(SCHEMA, skipped)
        with self.assertRaisesRegex(WorkflowDataError, "EQ-001"):
            validate_provider_receipt(SCHEMA, _receipt(substitutes_reviewer=True))
        with self.assertRaises(WorkflowDataError):
            validate_provider_receipt(SCHEMA, _receipt(substitutes_local_proofs=True))
        untrusted = _receipt()
        untrusted["checks"] = [{"name": "test", "status": "success", "source": "admin-bypass"}]
        with self.assertRaisesRegex(WorkflowDataError, "EQ-009"):
            validate_provider_receipt(SCHEMA, untrusted)

    def test_receipt_cannot_repair_missing_ci_or_ff(self) -> None:
        receipt = _receipt("d")
        with self.assertRaisesRegex(WorkflowDataError, "success"):
            validate_remote_closeout_evidence(
                _evidence("b", "d", ci_checks=[], provider_receipt=receipt),
                schema_path=SCHEMA,
            )
        with self.assertRaisesRegex(WorkflowDataError, "EQ-006"):
            validate_remote_closeout_evidence(
                _evidence("b", "d", merge_strategy="squash", provider_receipt=receipt),
                schema_path=SCHEMA,
            )

    def test_provider_receipt_cli_validates_installed_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            receipt_path = target / "receipt.json"
            receipt_path.write_text(json.dumps(_receipt()) + "\n", encoding="utf-8")
            ok = run(
                workflow_command(target, "workflow_check.py", "provider-receipt", "receipt.json"),
                cwd=target,
            )
            self.assertEqual(ok.returncode, 0, ok.stderr)
            bad = _receipt()
            bad["checks"] = [{"name": "test", "status": "neutral", "source": "github-actions"}]
            receipt_path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
            failed = run(
                workflow_command(target, "workflow_check.py", "provider-receipt", "receipt.json"),
                cwd=target,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("EQ-004", failed.stderr)


class PhaseCRemoteCloseoutTests(unittest.TestCase):
    def _pending(self, root: Path) -> dict[str, str]:
        project = root / "project"
        self.assertEqual(install_project(project).returncode, 0)
        base = create_baseline(project)
        src = project / "src"
        src.mkdir(exist_ok=True)
        (src / "feature.txt").write_text("delivery\n", encoding="utf-8")
        delivery = commit_all(project, "delivery")
        record = basic_v3_record(base)
        record["generation"] = 4
        record["status"] = "completed"
        record["phase"] = "integration"
        record["verification"].update(
            {
                "status": "passed",
                "delivery_commit": delivery,
                "delivery_hash": "1" * 64,
                "patch_hash": "2" * 64,
                "snapshot_id": "3" * 64,
                "changed_paths": ["src/feature.txt"],
            }
        )
        record["review"]["status"] = "pass"
        record["acceptance"][0]["status"] = "passed"
        record["process_retrospective"].update(
            {"completed": True, "completed_by": "coordinator-1", "completed_at": "2026-07-11T00:00:00Z"}
        )
        record["integration"].update(
            {
                "status": "pending",
                "mode": "remote_pr_ci",
                "policy_id": "IP-001",
                "source_ref": "refs/heads/main",
                "target_ref": "refs/heads/main",
                "target_parent": base,
                "pr_head_commit": delivery,
                "result_commit": delivery,
                "merge_strategy": "ff",
                "pr_url": "https://example.invalid/pull/1",
                "ci_checks": [{"name": "test", "status": "success"}],
            }
        )
        path = write_record(project, record)
        return {
            "project": str(project),
            "relative": record_relative(path, project),
            "base": base,
            "delivery": delivery,
        }

    def _closeout(self, project: Path, relative: str, evidence: dict, *, apply: bool = False):
        evidence_path = project.parent / "remote-evidence.json"
        evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
        args = [
            "prepare-remote-closeout",
            relative,
            "--evidence-json",
            str(evidence_path),
        ]
        if apply:
            args.append("--apply")
        return run(workflow_command(project, "workflow_state.py", *args), cwd=project)

    def test_ff_success_with_additive_receipt_and_optional_topology(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._pending(Path(directory))
            project = Path(fixture["project"])
            commit_all(project, "record pending remote")
            evidence = _evidence(
                fixture["base"],
                fixture["delivery"],
                merge_group=None,
                result_tree=fixture["delivery"],
                provider_receipt=_receipt(fixture["delivery"], snapshot_id="3" * 64),
            )
            del evidence["merge_group"]
            result = self._closeout(project, fixture["relative"], evidence, apply=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("commit=", result.stdout)

    def test_head_not_result_and_non_ff_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._pending(Path(directory))
            project = Path(fixture["project"])
            extra = commit_all(project, "second commit")
            failed = self._closeout(
                project,
                fixture["relative"],
                _evidence(fixture["base"], fixture["delivery"], result_commit=extra, result_tree=extra),
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("EQ-006", failed.stderr)
            squash = self._closeout(
                project,
                fixture["relative"],
                _evidence(fixture["base"], fixture["delivery"], merge_strategy="merge"),
            )
            self.assertNotEqual(squash.returncode, 0)
            self.assertIn("EQ-006", squash.stderr)

    def test_skipped_neutral_bypass_and_fake_reviewer_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._pending(Path(directory))
            project = Path(fixture["project"])
            cases = [
                (_evidence(fixture["base"], fixture["delivery"], ci_checks=[{"name": "test", "status": "skipped"}]), "EQ-004"),
                (_evidence(fixture["base"], fixture["delivery"], ci_checks=[{"name": "test", "status": "neutral"}]), "EQ-004"),
                (_evidence(fixture["base"], fixture["delivery"], admin_bypass=True), "EQ-005"),
                (_evidence(fixture["base"], fixture["delivery"], github_review_equivalent=True), "EQ-001"),
                (_evidence(fixture["base"], fixture["delivery"], branch_protection_review_equivalent=True), "EQ-001"),
                (
                    _evidence(
                        fixture["base"],
                        fixture["delivery"],
                        expected_check_source="github-actions",
                        ci_checks=[{"name": "test", "status": "success", "source": "unknown"}],
                    ),
                    "EQ-009",
                ),
            ]
            for evidence, marker in cases:
                failed = self._closeout(project, fixture["relative"], evidence)
                self.assertNotEqual(failed.returncode, 0, marker)
                self.assertIn(marker, failed.stderr)

    def test_closeout_requires_record_review_pass_without_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._pending(Path(directory))
            project = Path(fixture["project"])
            record_path = project / fixture["relative"]
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["review"]["status"] = "pending"
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            failed = self._closeout(
                project,
                fixture["relative"],
                _evidence(fixture["base"], fixture["delivery"]),
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("EQ-001", failed.stderr)
            self.assertIn("record-review", failed.stderr)

    def test_missing_ci_with_receipt_fails_on_closeout_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._pending(Path(directory))
            project = Path(fixture["project"])
            failed = self._closeout(
                project,
                fixture["relative"],
                _evidence(
                    fixture["base"],
                    fixture["delivery"],
                    ci_checks=[],
                    provider_receipt=_receipt(fixture["delivery"], snapshot_id="3" * 64),
                ),
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("success", failed.stderr)

    def test_stale_approval_and_divergent_merge_group_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._pending(Path(directory))
            project = Path(fixture["project"])
            stale = self._closeout(
                project,
                fixture["relative"],
                _evidence(fixture["base"], fixture["delivery"], stale_approval=True),
            )
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("EQ-002", stale.stderr)
            group = self._closeout(
                project,
                fixture["relative"],
                _evidence(fixture["base"], fixture["delivery"], merge_group="not-the-result"),
            )
            self.assertNotEqual(group.returncode, 0)
            self.assertIn("EQ-006", group.stderr)

    def test_unbound_receipt_fails_on_closeout_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._pending(Path(directory))
            project = Path(fixture["project"])
            failed = self._closeout(
                project,
                fixture["relative"],
                _evidence(
                    fixture["base"],
                    fixture["delivery"],
                    provider_receipt=_receipt("deadbeef", snapshot_id="3" * 64),
                ),
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("EQ-002", failed.stderr)

    def test_cooperative_flags_are_extra_denylist_not_reviewer_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._pending(Path(directory))
            project = Path(fixture["project"])
            evidence = _evidence(
                fixture["base"],
                fixture["delivery"],
                github_review_equivalent=True,
                provider_receipt=_receipt(fixture["delivery"], snapshot_id="3" * 64),
            )
            failed = self._closeout(project, fixture["relative"], evidence)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("EQ-001", failed.stderr)


class PhaseCMatrixDocsTests(unittest.TestCase):
    def test_prefilled_matrix_dispositions_are_no(self) -> None:
        text = EQUIVALENCE.read_text(encoding="utf-8")
        for matrix_id in [f"EQ-00{i}" for i in range(1, 10)]:
            self.assertIn(matrix_id, text)
        self.assertIn("附加 receipt ≠ 替代证明", text)
        plan = PLAN.read_text(encoding="utf-8")
        self.assertIn("phase_c_authorized_complete", plan)
        self.assertNotIn("PENDING_COMPUTE", plan)
