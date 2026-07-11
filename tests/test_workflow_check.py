from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import (
    approved_requirements,
    basic_v3_record,
    commit_all,
    create_baseline,
    install_project,
    record_relative,
    requirements_fingerprint,
    run,
    workflow_command,
    write_record,
)


class WorkflowCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.target = Path(self.temporary.name) / "project"
        installed = install_project(self.target)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.baseline = create_baseline(self.target)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_manual_resolves_same_root_from_business_subdirectory(self) -> None:
        subdirectory = self.target / "src" / "nested folder" / "中文"
        subdirectory.mkdir(parents=True)
        result = run(workflow_command(self.target, "workflow_check.py", "manual"), cwd=subdirectory)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_requirements_gate_accepts_normalized_crlf_and_rejects_stale_fingerprint(self) -> None:
        brief, fingerprint = approved_requirements(self.target)
        crlf = brief.read_text(encoding="utf-8").replace("\n", "\r\n")
        brief.write_bytes(crlf.encode("utf-8"))
        passed = run(
            workflow_command(self.target, "workflow_check.py", "requirements-gate", record_relative(brief, self.target)),
            cwd=self.target,
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn(fingerprint, run(
            workflow_command(self.target, "workflow_check.py", "requirements-snapshot", record_relative(brief, self.target)),
            cwd=self.target,
        ).stdout)

        text = brief.read_text(encoding="utf-8")
        text = text.replace("The task completes", "The corrected task completes")
        brief.write_text(text, encoding="utf-8")
        stale = run(
            workflow_command(self.target, "workflow_check.py", "requirements-gate", record_relative(brief, self.target)),
            cwd=self.target,
        )
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("fingerprint is stale", stale.stderr)

    def test_mvp_preflight_binds_project_backlog_and_brief_baseline(self) -> None:
        brief, fingerprint = approved_requirements(self.target)
        baseline = {"brief_id": "REQ-001", "revision": 1, "approval_fingerprint": fingerprint}
        record = basic_v3_record(
            self.baseline,
            source_type="mvp_backlog",
            requirements_baseline=baseline,
        )
        record["lane"].update(
            {
                "lane_id": "lane-MVP-001-test",
                "mode": "single",
                "branch": "main",
                "base_ref": "main",
                "base_commit": self.baseline,
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
        record_path = write_record(self.target, record)
        passed = run(
            workflow_command(self.target, "workflow_check.py", "preflight", record_relative(record_path, self.target)),
            cwd=self.target,
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)

        record["source"]["requirements_baseline"]["revision"] = 2
        write_record(self.target, record)
        failed = run(
            workflow_command(self.target, "workflow_check.py", "preflight", record_relative(record_path, self.target)),
            cwd=self.target,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("revision is stale", failed.stderr)

    def test_preflight_rejects_wrong_branch_and_dependency_cycle(self) -> None:
        record = basic_v3_record(self.baseline)
        record["lane"].update(
            {
                "lane_id": "lane-wrong",
                "mode": "single",
                "branch": "codex/task/other",
                "base_ref": "main",
                "base_commit": self.baseline,
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
        record_path = write_record(self.target, record)
        wrong = run(
            workflow_command(self.target, "workflow_check.py", "preflight", record_relative(record_path, self.target)),
            cwd=self.target,
        )
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn("does not match lane branch", wrong.stderr)

        backlog = self.target / ".codex-workflow/state/MVP_BACKLOG.md"
        text = backlog.read_text(encoding="utf-8")
        text = text.replace(
            "| MVP-001 | Must | <用户能完成什么> | 无 | REQ-F-001 / REQ-S-001 | <风险或无> | draft | none | - | - | - |",
            "| MVP-001 | Must | First | OPS-001 | AC-1 | none | blocked | dependencies | - | - | - |\n"
            "| OPS-001 | Must | Second | MVP-001 | AC-2 | none | blocked | dependencies | - | - | - |",
        )
        backlog.write_text(text, encoding="utf-8")
        cycle = run(workflow_command(self.target, "workflow_check.py", "manual"), cwd=self.target)
        self.assertNotEqual(cycle.returncode, 0)
        self.assertIn("dependency cycle", cycle.stderr)


if __name__ == "__main__":
    unittest.main()

