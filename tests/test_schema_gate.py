from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from support import (
    PACKAGE_ROOT,
    approved_requirements,
    basic_v3_record,
    create_baseline,
    install_project,
    record_relative,
    run,
    workflow_command,
    write_record,
)


BIN = PACKAGE_ROOT / "payload/.codex-workflow/bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

from workflow_common import WorkflowDataError, validate_json_schema  # noqa: E402
from workflow_lane import _claim_commit  # noqa: E402
from workflow_paths import WorkflowPaths  # noqa: E402
from workflow_state import mutate_record  # noqa: E402


class JsonSchemaGateTests(unittest.TestCase):
    def _assert_ok(self, result) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_task_schema_blocks_state_write_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            base = create_baseline(target)
            record = basic_v3_record(base)
            del record["planning"]
            record_path = write_record(target, record)
            before = record_path.read_bytes()
            result = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "invalidate-integration",
                    record_relative(record_path, target),
                    "--reason",
                    "schema-test",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("task-record-v3.schema.json", result.stderr)
            self.assertIn("planning", result.stderr)
            self.assertEqual(record_path.read_bytes(), before)

    def test_schema_definition_rejects_unsupported_optional_keyword(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            schema = Path(directory) / "future.schema.json"
            schema.write_text(
                json.dumps(
                    {
                        "type": "object",
                        "properties": {
                            "optional": {"type": "string", "maxLength": 3},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowDataError, r"future\.schema\.json.*maxLength"):
                validate_json_schema(schema, {}, label="Future payload")

    def test_additional_properties_false_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            schema = Path(directory) / "strict.schema.json"
            schema.write_text(
                json.dumps(
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"allowed": {"type": "string"}},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowDataError, r"unexpected.*additional property"):
                validate_json_schema(
                    schema,
                    {"allowed": "yes", "unexpected": "no"},
                    label="Strict payload",
                )

    def test_lane_schema_reference_blocks_state_write_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            base = create_baseline(target)
            record = basic_v3_record(base)
            record["lane"]["dependency_snapshot"]["dependencies"] = [123]
            record_path = write_record(target, record)
            before = record_path.read_bytes()
            result = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "invalidate-integration",
                    record_relative(record_path, target),
                    "--reason",
                    "schema-test",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("task-record-v3.schema.json", result.stderr)
            self.assertIn("dependency_snapshot.dependencies[0]", result.stderr)
            self.assertEqual(record_path.read_bytes(), before)

    def test_task_schema_rejects_an_untracked_developer_narrative_channel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            base = create_baseline(target)
            record = basic_v3_record(base)
            record["developer"]["global_summary"] = "Repository-wide coverage passed."
            record_path = write_record(target, record)
            before = record_path.read_bytes()
            result = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "invalidate-integration",
                    record_relative(record_path, target),
                    "--reason",
                    "schema-test",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("global_summary", result.stderr)
            self.assertIn("additional property", result.stderr)
            self.assertEqual(record_path.read_bytes(), before)

    def test_state_writer_revalidates_its_updated_record_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            base = create_baseline(target)
            record_path = write_record(target, basic_v3_record(base))
            before = record_path.read_bytes()
            paths = WorkflowPaths.discover(target)

            def make_invalid(updated: dict) -> None:
                del updated["planning"]

            with self.assertRaisesRegex(WorkflowDataError, r"task-record-v3\.schema\.json[\s\S]*planning"):
                mutate_record(
                    paths,
                    record_relative(record_path, target),
                    None,
                    True,
                    make_invalid,
                )
            self.assertEqual(record_path.read_bytes(), before)

    def test_requirements_schema_blocks_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            brief, _ = approved_requirements(target)
            source = brief.read_text(encoding="utf-8")
            start = "<!-- CODEX_REQUIREMENTS_JSON_START -->"
            end = "<!-- CODEX_REQUIREMENTS_JSON_END -->"
            metadata = json.loads(source.split(start, 1)[1].split(end, 1)[0])
            del metadata["approval"]
            brief.write_text(
                source.split(start, 1)[0]
                + start
                + "\n"
                + json.dumps(metadata, ensure_ascii=False, indent=2)
                + "\n"
                + end
                + source.split(end, 1)[1],
                encoding="utf-8",
            )
            result = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "requirements-snapshot",
                    str(brief.relative_to(target)),
                ),
                cwd=target,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requirements-v1.schema.json", result.stderr)
            self.assertIn("approval", result.stderr)

    def test_remote_claim_schema_rejects_incomplete_transfer(self) -> None:
        payload = {
            "schema_version": 1,
            "task_id": "MVP-001",
            "claim_id": "claim-001",
            "owner_generation": 1,
            "lease_revision": 1,
            "owner_id": "owner-001",
            "task_ref": "refs/heads/codex/task/MVP-001",
            "resource_keys": ["path:src"],
            "resource_refs": ["refs/heads/codex/resources/example"],
            "heartbeat_at": "2026-07-12T00:00:00Z",
            "expires_at": "2026-07-12T00:15:00Z",
            "state": "active",
            "transfer": {"kind": "handoff"},
        }
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            create_baseline(target)
            with self.assertRaises(WorkflowDataError) as raised:
                _claim_commit(WorkflowPaths.discover(target), payload)
        self.assertIn("remote-claim-v1.schema.json", str(raised.exception))
        self.assertIn("transfer", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
