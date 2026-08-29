from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

from support import (
    PACKAGE_ROOT,
    basic_v3_record,
    basic_v4_record,
    closeout_fingerprint,
    create_baseline,
    install_project,
    run,
    workflow_command,
    write_record,
)

BIN_PATH = PACKAGE_ROOT / "payload/.codex-workflow/bin"
sys.path.insert(0, str(BIN_PATH))
from workflow_common import (  # noqa: E402
    V4_CLOSEOUT_FINGERPRINT_VERSION,
    WorkflowDataError,
    closeout_fingerprint_algorithm,
    closeout_state_fingerprint,
    stamp_closeout_fingerprint_version,
    task_record_schema_name,
)


def _backlog() -> str:
    return "# Backlog\n\n| id | status |\n| MVP-001 | done |\n"


class V4CloseoutFingerprintTests(unittest.TestCase):
    def test_v3_algorithm_is_unchanged(self) -> None:
        record = basic_v3_record("a" * 40)
        record["integration"]["status"] = "integrated"
        record["integration"]["target_ref"] = "refs/heads/main"
        record["integration"]["result_commit"] = "b" * 40
        backlog = _backlog()
        self.assertEqual(
            closeout_state_fingerprint(record, backlog),
            closeout_fingerprint(record, backlog),
        )
        self.assertEqual(closeout_fingerprint_algorithm(record), 3)

    def test_v2_closeout_fails_closed(self) -> None:
        record = {"version": 2, "task_id": "LEGACY", "integration": {}}
        with self.assertRaises(WorkflowDataError):
            closeout_state_fingerprint(record, _backlog())

    def test_v4_fingerprint_detects_contract_and_decision_drift(self) -> None:
        record = basic_v4_record("a" * 40)
        record["decision_log"] = [
            {
                "id": "HD-010",
                "kind": "product_decision",
                "status": "resolved",
                "decision_fingerprint": "c" * 64,
                "decision_state_fingerprint": "d" * 64,
            }
        ]
        stamp_closeout_fingerprint_version(record)
        baseline = closeout_state_fingerprint(record, _backlog())
        drifted_contract = copy.deepcopy(record)
        drifted_contract["contract_fingerprint"] = "e" * 64
        self.assertNotEqual(
            closeout_state_fingerprint(drifted_contract, _backlog()),
            baseline,
        )
        drifted_decision = copy.deepcopy(record)
        drifted_decision["decision_log"][0]["decision_state_fingerprint"] = "f" * 64
        self.assertNotEqual(
            closeout_state_fingerprint(drifted_decision, _backlog()),
            baseline,
        )
        same_v3_fields = copy.deepcopy(record)
        same_v3_fields["integration"]["queue_id"] = same_v3_fields["integration"]["queue_id"]
        self.assertEqual(closeout_state_fingerprint(same_v3_fields, _backlog()), baseline)

    def test_pending_closeout_keeps_stored_algorithm_and_rejects_unknown(self) -> None:
        record = basic_v4_record("a" * 40)
        record["decision_log"] = []
        stamp_closeout_fingerprint_version(record)
        self.assertEqual(
            closeout_fingerprint_algorithm(record),
            V4_CLOSEOUT_FINGERPRINT_VERSION,
        )
        sealed = closeout_state_fingerprint(record, _backlog())
        record["integration"]["closeout_state_fingerprint"] = sealed
        self.assertEqual(closeout_state_fingerprint(record, _backlog()), sealed)
        record["integration"]["closeout_fingerprint_version"] = 5
        with self.assertRaises(WorkflowDataError):
            closeout_state_fingerprint(record, _backlog())
        v3 = basic_v3_record("a" * 40)
        v3["integration"]["closeout_fingerprint_version"] = 4
        with self.assertRaises(WorkflowDataError):
            closeout_state_fingerprint(v3, _backlog())

    def test_v3_fingerprint_ignores_v4_contract_fields(self) -> None:
        record = basic_v3_record("a" * 40)
        baseline = closeout_state_fingerprint(record, _backlog())
        record["contract_fingerprint"] = "a" * 64
        record["decision_log"] = [{"id": "HD-010"}]
        self.assertEqual(closeout_state_fingerprint(record, _backlog()), baseline)


class V4CloseoutGateTests(unittest.TestCase):
    def test_v4_closeout_gate_no_longer_uses_milestone_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            base = create_baseline(target)
            record = basic_v4_record(base)
            record["integration"]["status"] = "integrated"
            record["integration"]["target_ref"] = "refs/heads/main"
            record["integration"]["result_commit"] = base
            record["integration"]["closeout_state_fingerprint"] = "a" * 64
            record["integration"]["closeout_fingerprint_version"] = 4
            path = write_record(target, record)
            result = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "closeout-gate",
                    path.relative_to(target).as_posix(),
                ),
                cwd=target,
            )
            combined = result.stderr + result.stdout
            self.assertNotIn("versioned closeout milestone", combined)
            missing_version = copy.deepcopy(record)
            missing_version["integration"]["closeout_fingerprint_version"] = None
            write_record(target, missing_version)
            blocked = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "closeout-gate",
                    path.relative_to(target).as_posix(),
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("closeout_fingerprint_version=4", blocked.stderr)

    def test_target_record_schema_follows_record_version(self) -> None:
        self.assertEqual(task_record_schema_name({"version": 3}), "task-record-v3.schema.json")
        self.assertEqual(task_record_schema_name({"version": 4}), "task-record-v4.schema.json")
        with self.assertRaises(WorkflowDataError):
            task_record_schema_name({"version": 2})


if __name__ == "__main__":
    unittest.main()
