from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import (
    approved_requirements,
    basic_v3_record,
    commit_all,
    create_baseline,
    git,
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

    def doctor(self, *, env_extra: dict[str, str] | None = None):
        return run(
            workflow_command(self.target, "workflow_check.py", "doctor"),
            cwd=self.target,
            env_extra=env_extra,
        )

    def startup_observation_path(self) -> Path:
        common = Path(git(self.target, "rev-parse", "--git-common-dir").stdout.strip())
        if not common.is_absolute():
            common = self.target / common
        return common.resolve() / "codex-workflow-v3" / "audit" / "last-session-check.json"

    def write_startup_observation(
        self,
        *,
        checked_at: str = "2026-07-01T02:03:04+00:00",
        worktree: Path | None = None,
        worktree_text: str | None = None,
    ) -> None:
        path = self.startup_observation_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "checked_at": checked_at,
                    "worktree": (
                        worktree_text
                        if worktree_text is not None
                        else str((worktree or self.target).resolve())
                    ),
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    def assert_doctor_shape(self, output: str) -> None:
        lines = output.splitlines()
        for label in (
            "PACKAGE:",
            "GOVERNANCE:",
            "HOOK CONFIG:",
            "STARTUP OBSERVATION:",
        ):
            self.assertEqual(sum(line.startswith(label + " ") for line in lines), 1, output)
        self.assertEqual(lines.count("PRIMARY NEXT ACTION:"), 1, output)

    def assert_doctor_decode_failure(self, result, domain: str) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assert_doctor_shape(result.stdout)
        self.assertIn(f"{domain}: INVALID", result.stdout)
        self.assertIn("not valid UTF-8", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    @staticmethod
    def tree_snapshot(root: Path) -> dict[str, bytes | None]:
        if not root.exists():
            return {}
        return {
            path.relative_to(root).as_posix(): (path.read_bytes() if path.is_file() else None)
            for path in root.rglob("*")
        }

    @staticmethod
    def json_resource_limit_fixture(kind: str) -> bytes:
        if kind == "huge_integer":
            return b'{"value":' + (b"9" * 5000) + b"}"
        if kind == "deep_nesting":
            return (b"[" * 1500) + b"0" + (b"]" * 1500)
        raise AssertionError(f"unknown JSON resource fixture: {kind}")

    @staticmethod
    def embedded_json_fixture(marker: str, payload: bytes) -> bytes:
        return (
            f"<!-- {marker}_START -->\n".encode("ascii")
            + payload
            + f"\n<!-- {marker}_END -->\n".encode("ascii")
        )

    @staticmethod
    def load_payload_workflow_check():
        script = (
            Path(__file__).resolve().parents[1]
            / "payload"
            / ".codex-workflow"
            / "bin"
            / "workflow_check.py"
        )
        module_name = f"workflow_check_doctor_test_{id(script)}"
        spec = importlib.util.spec_from_file_location(module_name, script)
        if spec is None or spec.loader is None:
            raise AssertionError(f"cannot load {script}")
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(script.parent))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        return module

    @staticmethod
    def restore_bytes(path: Path, original: bytes | None) -> None:
        if original is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(original)

    def test_doctor_missing_startup_observation_is_readable_warning_and_success(self) -> None:
        self.assertFalse(self.startup_observation_path().exists())
        result = self.doctor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_doctor_shape(result.stdout)
        self.assertIn("PACKAGE: PASS", result.stdout)
        self.assertIn("GOVERNANCE: PASS", result.stdout)
        self.assertIn("HOOK CONFIG: PASS", result.stdout)
        self.assertIn("STARTUP OBSERVATION: WARN", result.stdout)
        self.assertIn("review and trust", result.stdout.lower())
        self.assertIn("start or resume a new Codex session", result.stdout)
        self.assertIn("run doctor again in that session", result.stdout)
        self.assertNotIn("reinstall", result.stdout.lower())
        self.assertNotIn("workflow_check.py start", result.stdout)

    def test_doctor_reports_historical_observation_without_session_trust_claim(self) -> None:
        self.write_startup_observation()
        result = self.doctor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_doctor_shape(result.stdout)
        self.assertIn("STARTUP OBSERVATION: PASS", result.stdout)
        self.assertIn("2026-07-01T02:03:04+00:00", result.stdout)
        self.assertIn(str(self.target.resolve()), result.stdout)
        self.assertIn("does not prove a current or unique session identity", result.stdout)
        self.assertNotIn("current session is trusted", result.stdout.lower())
        self.assertIn("Start new work as a V4 focus core slice", result.stdout)
        self.assertIn("workflow_check.py status", result.stdout)
        self.assertNotIn("existing V3 workflow", result.stdout)

    def test_doctor_package_drift_fails_before_lower_priority_problems(self) -> None:
        managed = self.target / ".codex-workflow/bin/workflow_state.py"
        managed.write_text(managed.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
        (self.target / ".codex-workflow/governance/PLAN.md").unlink()
        hooks = self.target / ".codex/hooks.json"
        hooks.write_text("{}\n", encoding="utf-8")
        self.startup_observation_path().parent.mkdir(parents=True, exist_ok=True)
        self.startup_observation_path().write_text("{", encoding="utf-8")

        result = self.doctor()
        self.assertNotEqual(result.returncode, 0)
        self.assert_doctor_shape(result.stdout)
        self.assertIn("PACKAGE: FAIL", result.stdout)
        self.assertIn("GOVERNANCE: FAIL", result.stdout)
        self.assertIn("HOOK CONFIG: FAIL", result.stdout)
        self.assertIn("STARTUP OBSERVATION: INVALID", result.stdout)
        primary = result.stdout.split("PRIMARY NEXT ACTION:", 1)[1]
        self.assertIn("external package verifier", primary)
        self.assertNotIn("restore the missing governance", primary)

    def test_doctor_missing_package_owned_file_is_nonzero(self) -> None:
        (self.target / ".codex-workflow/bin/workflow_state.py").unlink()
        result = self.doctor()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PACKAGE: FAIL", result.stdout)
        self.assertIn("package-owned file is missing", result.stdout)

    def test_doctor_accepts_project_owned_governance_edit_as_not_package_drift(self) -> None:
        project_rules = self.target / ".codex-workflow/governance/AGENTS.md"
        project_rules.write_text(
            project_rules.read_text(encoding="utf-8") + "\nProject-specific note.\n",
            encoding="utf-8",
        )
        result = self.doctor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PACKAGE: PASS", result.stdout)
        self.assertNotIn("package-owned content drift", result.stdout)

    def test_doctor_rejects_invalid_managed_hook_configuration(self) -> None:
        hook_path = self.target / ".codex/hooks.json"
        hooks = json.loads(hook_path.read_text(encoding="utf-8"))
        hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "python workflow_check.py manual"
        hook_path.write_text(json.dumps(hooks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        result = self.doctor()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PACKAGE: PASS", result.stdout)
        self.assertIn("HOOK CONFIG: FAIL", result.stdout)
        self.assertIn("configuration only; it does not prove that a Hook ran", result.stdout)

    def test_doctor_invalid_or_mismatched_observation_is_nonzero(self) -> None:
        cases = (
            ("not-a-time", self.target, "invalid checked_at"),
            ("2026-07-01T02:03:04", self.target, "timezone"),
            ("2026-07-01T02:03:04+00:00", self.target.parent / "other", "different worktree"),
        )
        for checked_at, worktree, expected in cases:
            with self.subTest(checked_at=checked_at, worktree=worktree):
                self.write_startup_observation(checked_at=checked_at, worktree=worktree)
                result = self.doctor()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("STARTUP OBSERVATION: INVALID", result.stdout)
                self.assertIn(expected, result.stdout)

    def test_doctor_invalid_utf8_manifest_remains_structured(self) -> None:
        manifest = self.target / ".codex-workflow/install/manifest.json"
        manifest.write_bytes(b"{\"package\": \"codex-workflow-v3\", \"bad\": \xff}")
        self.assert_doctor_decode_failure(self.doctor(), "PACKAGE")

    def test_doctor_invalid_utf8_governance_remains_structured(self) -> None:
        (self.target / "AGENTS.md").write_bytes(b"workflow discovery \xff")
        self.assert_doctor_decode_failure(self.doctor(), "GOVERNANCE")

    def test_doctor_invalid_utf8_hooks_remains_structured(self) -> None:
        (self.target / ".codex/hooks.json").write_bytes(b"{\"hooks\": \xff}")
        self.assert_doctor_decode_failure(self.doctor(), "HOOK CONFIG")

    def test_doctor_invalid_utf8_startup_observation_remains_structured(self) -> None:
        observation = self.startup_observation_path()
        observation.parent.mkdir(parents=True, exist_ok=True)
        observation.write_bytes(b"{\"checked_at\": \xff}")
        self.assert_doctor_decode_failure(self.doctor(), "STARTUP OBSERVATION")

    def test_doctor_invalid_utf8_layout_uses_structured_discovery_boundary(self) -> None:
        # Encoding matrix: discovery layout plus manifest, governance, Hooks and observation.
        (self.target / ".codex-workflow/layout.json").write_bytes(b"{\"layout_version\": \xff}")
        result = self.doctor()
        self.assertNotEqual(result.returncode, 0)
        self.assert_doctor_shape(result.stdout)
        for domain in ("PACKAGE", "GOVERNANCE", "HOOK CONFIG", "STARTUP OBSERVATION"):
            self.assertIn(f"{domain}: UNKNOWN", result.stdout)
        primary = result.stdout.split("PRIMARY NEXT ACTION:", 1)[1]
        self.assertIn("external package verifier", primary)
        self.assertNotIn("Traceback", result.stderr)

    def test_doctor_json_resource_limits_stay_inside_their_data_boundaries(self) -> None:
        layout = self.target / ".codex-workflow/layout.json"
        manifest = self.target / ".codex-workflow/install/manifest.json"
        hooks = self.target / ".codex/hooks.json"
        backlog = self.target / ".codex-workflow/state/MVP_BACKLOG.md"
        observation = self.startup_observation_path()
        paths = (layout, manifest, hooks, backlog, observation)
        originals = {path: path.read_bytes() if path.exists() else None for path in paths}
        sources = (
            ("layout", layout, lambda payload: payload, None),
            ("manifest", manifest, lambda payload: payload, "PACKAGE"),
            ("Hooks", hooks, lambda payload: payload, "HOOK CONFIG"),
            (
                "Backlog",
                backlog,
                lambda payload: self.embedded_json_fixture(
                    "CODEX_REQUIREMENTS_BASELINE", payload
                ),
                "GOVERNANCE",
            ),
            ("startup observation", observation, lambda payload: payload, "STARTUP OBSERVATION"),
        )
        try:
            for name, path, prepare, domain in sources:
                for kind in ("huge_integer", "deep_nesting"):
                    with self.subTest(source=name, resource_error=kind):
                        payload = prepare(self.json_resource_limit_fixture(kind))
                        for candidate, original in originals.items():
                            self.restore_bytes(candidate, original)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(payload)

                        environment = (
                            {"PYTHONINTMAXSTRDIGITS": "640"}
                            if kind == "huge_integer" and hasattr(sys, "get_int_max_str_digits")
                            else None
                        )
                        result = self.doctor(env_extra=environment)

                        self.assertNotEqual(result.returncode, 0)
                        self.assert_doctor_shape(result.stdout)
                        self.assertNotIn("Traceback", result.stderr)
                        if domain is None:
                            for label in (
                                "PACKAGE",
                                "GOVERNANCE",
                                "HOOK CONFIG",
                                "STARTUP OBSERVATION",
                            ):
                                self.assertIn(f"{label}: UNKNOWN", result.stdout)
                        else:
                            self.assertIn(f"{domain}: INVALID", result.stdout)
        finally:
            for path, original in originals.items():
                self.restore_bytes(path, original)

    def test_doctor_governance_internal_json_resource_limits_are_structured(self) -> None:
        brief, _ = approved_requirements(self.target)
        project = self.target / ".codex-workflow/governance/PROJECT.md"
        backlog = self.target / ".codex-workflow/state/MVP_BACKLOG.md"
        schema = self.target / ".codex-workflow/schemas/requirements-v1.schema.json"
        paths = (project, backlog, brief, schema)
        originals = {path: path.read_bytes() for path in paths}
        sources = (
            (
                "PROJECT baseline",
                project,
                lambda payload: self.embedded_json_fixture(
                    "CODEX_REQUIREMENTS_BASELINE", payload
                ),
            ),
            (
                "Requirements brief",
                brief,
                lambda payload: self.embedded_json_fixture("CODEX_REQUIREMENTS_JSON", payload),
            ),
            ("requirements schema", schema, lambda payload: payload),
            (
                "Backlog baseline",
                backlog,
                lambda payload: self.embedded_json_fixture(
                    "CODEX_REQUIREMENTS_BASELINE", payload
                ),
            ),
        )
        try:
            for name, path, prepare in sources:
                for kind in ("huge_integer", "deep_nesting"):
                    with self.subTest(source=name, resource_error=kind):
                        for candidate, original in originals.items():
                            self.restore_bytes(candidate, original)
                        path.write_bytes(prepare(self.json_resource_limit_fixture(kind)))

                        environment = (
                            {"PYTHONINTMAXSTRDIGITS": "640"}
                            if kind == "huge_integer" and hasattr(sys, "get_int_max_str_digits")
                            else None
                        )
                        result = self.doctor(env_extra=environment)

                        self.assertNotEqual(result.returncode, 0)
                        self.assert_doctor_shape(result.stdout)
                        self.assertIn("GOVERNANCE: INVALID", result.stdout)
                        self.assertNotIn("Traceback", result.stderr)
        finally:
            for path, original in originals.items():
                self.restore_bytes(path, original)

    def test_doctor_dynamic_diagnostics_are_rendered_as_safe_single_lines(self) -> None:
        workflow_check = self.load_payload_workflow_check()
        malicious = "before\r\nPRIMARY NEXT ACTION:\nPACKAGE: PASS\t\x00\x1f\x7f\x85\ud800after"
        findings = [
            (label, workflow_check.DoctorFinding("INVALID", f"{label} {malicious}"))
            for label in ("PACKAGE", "GOVERNANCE", "HOOK CONFIG", "STARTUP OBSERVATION")
        ]
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            workflow_check._print_doctor(findings, malicious)

        rendered = output.getvalue()
        self.assert_doctor_shape(rendered)
        self.assertNotIn("\r", rendered)
        self.assertNotIn("\x00", rendered)
        self.assertNotIn("\x1f", rendered)
        self.assertNotIn("\x7f", rendered)
        self.assertNotIn("\x85", rendered)
        self.assertNotIn("\ud800", rendered)
        for escaped in (r"\r", r"\n", r"\t", r"\u0000", r"\u001f", r"\u007f", r"\u0085", r"\ud800"):
            self.assertIn(escaped, rendered)

    def test_doctor_manifest_diagnostic_cannot_forge_headings_or_emit_surrogates(self) -> None:
        manifest_path = self.target / ".codex-workflow/install/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        malicious_key = "bad\r\nPRIMARY NEXT ACTION:\nGOVERNANCE: PASS\x00\ud800"
        manifest["files"][malicious_key] = {
            "ownership": "unsupported",
            "managed_sha256": "0" * 64,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

        result = self.doctor()

        self.assertNotEqual(result.returncode, 0)
        self.assert_doctor_shape(result.stdout)
        self.assertIn("PACKAGE: INVALID", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(r"\r\n", result.stdout)
        self.assertIn(r"\ud800", result.stdout)

    def test_doctor_observation_path_failures_are_local_unknown_results(self) -> None:
        workflow_check = self.load_payload_workflow_check()

        class SharedRuntimeFailure:
            def __init__(self, failure: Exception) -> None:
                self.failure = failure

            @property
            def shared_runtime(self):
                raise self.failure

        for failure in (
            workflow_check.WorkflowPathError("runtime containment escape\nPRIMARY NEXT ACTION:"),
            OSError("runtime filesystem failure\nSTARTUP OBSERVATION: PASS"),
        ):
            with self.subTest(failure=type(failure).__name__):
                finding = workflow_check._doctor_observation(SharedRuntimeFailure(failure))
                self.assertEqual(finding.status, "UNKNOWN")

        with self.assertRaises(RecursionError):
            workflow_check._doctor_observation(
                SharedRuntimeFailure(RecursionError("programming recursion is not path diagnosis"))
            )
        with self.assertRaises(RuntimeError):
            workflow_check._doctor_observation(
                SharedRuntimeFailure(RuntimeError("programming runtime failure"))
            )

        observation = self.startup_observation_path()
        self.write_startup_observation()

        class ObservationPaths:
            shared_runtime = observation.parents[1]
            root = self.target.resolve()

        class ResolveFailure:
            def __init__(self, value: str) -> None:
                self.value = value

            def expanduser(self):
                return self

            def resolve(self, *, strict: bool = False):
                raise RuntimeError("recorded worktree resolve loop\nPACKAGE: PASS")

        with mock.patch.object(workflow_check, "Path", ResolveFailure):
            finding = workflow_check._doctor_observation(ObservationPaths())
        self.assertEqual(finding.status, "UNKNOWN")

        class OSResolveFailure(ResolveFailure):
            def resolve(self, *, strict: bool = False):
                raise OSError("recorded worktree permission failure")

        with mock.patch.object(workflow_check, "Path", OSResolveFailure):
            finding = workflow_check._doctor_observation(ObservationPaths())
        self.assertEqual(finding.status, "INVALID")

    def test_doctor_observation_user_and_value_paths_stay_in_four_domains(self) -> None:
        cases = [("invalid\x00worktree", "INVALID")]
        if os.name == "posix":
            cases.append(("~codex-workflow-user-that-does-not-exist-7f0f", "UNKNOWN"))

        for worktree, status in cases:
            with self.subTest(worktree=worktree, status=status):
                self.write_startup_observation(worktree_text=worktree)
                result = self.doctor()
                self.assertNotEqual(result.returncode, 0)
                self.assert_doctor_shape(result.stdout)
                self.assertIn(f"STARTUP OBSERVATION: {status}", result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_doctor_governance_does_not_reclassify_programming_exceptions(self) -> None:
        workflow_check = self.load_payload_workflow_check()

        for failure in (
            ValueError("programming value failure"),
            RecursionError("programming recursion failure"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(
                    workflow_check,
                    "check_governance",
                    side_effect=failure,
                ):
                    with self.assertRaises(type(failure)):
                        workflow_check._doctor_governance(mock.Mock())

    def test_shared_path_boundary_classifies_runtime_only(self) -> None:
        workflow_check = self.load_payload_workflow_check()

        self.assertFalse(
            issubclass(
                workflow_check.WorkflowPathResourceError,
                workflow_check.WorkflowPathError,
            )
        )

        class ResolveFailure:
            def resolve(self, *, strict: bool = False):
                raise RuntimeError("filesystem resolve loop")

        with self.assertRaises(workflow_check.WorkflowPathRuntimeError):
            workflow_check.resolve_path(ResolveFailure(), label="test path")

        class OSFailure:
            def resolve(self, *, strict: bool = False):
                raise OSError("filesystem permission failure")

        with self.assertRaises(workflow_check.WorkflowPathOSError):
            workflow_check.resolve_path(OSFailure(), label="test path")

        class ValueFailure:
            def resolve(self, *, strict: bool = False):
                raise ValueError("invalid path value")

        with self.assertRaises(workflow_check.WorkflowPathValueError):
            workflow_check.resolve_path(ValueFailure(), label="test path")

        class ExpandRuntimeFailure:
            def expanduser(self):
                raise RuntimeError("unknown home directory")

        with self.assertRaises(workflow_check.WorkflowPathRuntimeError):
            workflow_check.resolve_path(
                ExpandRuntimeFailure(),
                label="test path",
                expand_user=True,
            )

        class RecursionFailure:
            def resolve(self, *, strict: bool = False):
                raise RecursionError("programming recursion failure")

        with self.assertRaises(RecursionError):
            workflow_check.resolve_path(RecursionFailure(), label="test path")

    def test_real_symlink_loop_has_stable_runtime_classification(self) -> None:
        workflow_check = self.load_payload_workflow_check()
        loop_a = Path(self.temporary.name) / "loop-a"
        loop_b = Path(self.temporary.name) / "loop-b"
        try:
            loop_a.symlink_to(loop_b)
            loop_b.symlink_to(loop_a)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symbolic links are unavailable: {exc}")

        with self.assertRaises(workflow_check.WorkflowPathRuntimeError):
            workflow_check.resolve_path(loop_a, label="symlink loop")

        observation = self.startup_observation_path()
        observation.parent.mkdir(parents=True, exist_ok=True)
        observation.write_text(
            json.dumps(
                {
                    "checked_at": "2026-07-01T02:03:04+00:00",
                    "worktree": str(loop_a),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = self.doctor()
        self.assertNotEqual(result.returncode, 0)
        self.assert_doctor_shape(result.stdout)
        self.assertIn("STARTUP OBSERVATION: UNKNOWN", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_requirements_candidate_symlink_loop_uses_shared_resolver(self) -> None:
        requirements = self.target / ".codex-workflow/governance/requirements"
        loop_a = requirements / "candidate-loop-a"
        loop_b = requirements / "candidate-loop-b"
        try:
            loop_a.symlink_to(loop_b)
            loop_b.symlink_to(loop_a)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symbolic links are unavailable: {exc}")

        result = run(
            workflow_command(
                self.target,
                "workflow_check.py",
                "requirements-snapshot",
                loop_a.relative_to(self.target).as_posix(),
            ),
            cwd=self.target,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unable to resolve Requirements brief", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_json_integer_resource_limit_is_exact_and_sign_independent(self) -> None:
        workflow_check = self.load_payload_workflow_check()

        for sign in ("", "-"):
            with self.subTest(sign=sign, digits=640):
                payload = workflow_check.parse_json_resource(
                    '{"value":' + sign + ("9" * 640) + "}",
                    label="boundary fixture",
                )
                self.assertIsInstance(payload["value"], int)
            with self.subTest(sign=sign, digits=641):
                with self.assertRaises(workflow_check.WorkflowJSONResourceError):
                    workflow_check.parse_json_resource(
                        '{"value":' + sign + ("9" * 641) + "}",
                        label="boundary fixture",
                    )

    def test_json_nesting_resource_limit_is_exact(self) -> None:
        workflow_check = self.load_payload_workflow_check()

        accepted = ("[" * 256) + "0" + ("]" * 256)
        self.assertIsInstance(
            workflow_check.parse_json_resource(accepted, label="boundary fixture"),
            list,
        )
        rejected = ("[" * 257) + "0" + ("]" * 257)
        with self.assertRaises(workflow_check.WorkflowJSONResourceError):
            workflow_check.parse_json_resource(rejected, label="boundary fixture")

    def test_json_syntax_and_resource_failures_have_distinct_types(self) -> None:
        workflow_check = self.load_payload_workflow_check()

        self.assertTrue(
            issubclass(workflow_check.WorkflowJSONSyntaxError, workflow_check.WorkflowDataError)
        )
        self.assertFalse(
            issubclass(workflow_check.WorkflowJSONResourceError, workflow_check.WorkflowDataError)
        )
        self.assertTrue(
            issubclass(workflow_check.WorkflowJSONSyntaxError, workflow_check.WorkflowJSONError)
        )
        self.assertTrue(
            issubclass(workflow_check.WorkflowJSONResourceError, workflow_check.WorkflowJSONError)
        )
        with self.assertRaises(workflow_check.WorkflowJSONSyntaxError):
            workflow_check.parse_json_resource("{", label="syntax fixture")
        with self.assertRaises(workflow_check.WorkflowJSONResourceError):
            workflow_check.parse_json_resource(
                '{"value":' + ("9" * 641) + "}",
                label="resource fixture",
            )

    def test_layout_read_recursion_is_not_reclassified_as_json(self) -> None:
        workflow_check = self.load_payload_workflow_check()
        workflow_paths = sys.modules[workflow_check.WorkflowPaths.__module__]

        class LayoutFile:
            def read_text(self, *, encoding: str):
                raise RecursionError("programming recursion while reading")

        class LayoutRoot:
            def __truediv__(self, other):
                return LayoutFile()

        with self.assertRaises(RecursionError):
            workflow_paths._load_layout(LayoutRoot())

    def test_non_doctor_layout_resource_error_is_not_swallowed(self) -> None:
        layout = self.target / ".codex-workflow/layout.json"
        layout.write_bytes(self.json_resource_limit_fixture("huge_integer"))

        environment = (
            {"PYTHONINTMAXSTRDIGITS": "640"}
            if hasattr(sys, "get_int_max_str_digits")
            else None
        )
        result = run(
            workflow_command(self.target, "workflow_check.py", "manual"),
            cwd=self.target,
            env_extra=environment,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("JSONResourceLimitError", result.stderr)
        self.assertIn("Traceback", result.stderr)
        self.assertNotIn("PRIMARY NEXT ACTION:", result.stdout)

    def test_non_doctor_json_syntax_errors_remain_structured(self) -> None:
        layout = self.target / ".codex-workflow/layout.json"
        layout_bytes = layout.read_bytes()
        layout.write_text("{", encoding="utf-8")
        result = run(
            workflow_command(self.target, "workflow_check.py", "manual"),
            cwd=self.target,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid workflow layout JSON", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        layout.write_bytes(layout_bytes)
        backlog = self.target / ".codex-workflow/state/MVP_BACKLOG.md"
        backlog.write_bytes(
            self.embedded_json_fixture("CODEX_REQUIREMENTS_BASELINE", b"{")
        )
        result = run(
            workflow_command(self.target, "workflow_check.py", "manual"),
            cwd=self.target,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CODEX_REQUIREMENTS_BASELINE is invalid JSON", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_non_doctor_json_resource_errors_cross_command_boundaries(self) -> None:
        payload = self.json_resource_limit_fixture("huge_integer")

        def assert_resource_traceback(result) -> None:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("JSONResourceLimitError", result.stderr)
            self.assertIn("Traceback", result.stderr)

        candidate = (
            self.target
            / ".codex-workflow/governance/requirements/REQ-RESOURCE-BOUNDARY.md"
        )
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(
            self.embedded_json_fixture("CODEX_REQUIREMENTS_JSON", payload)
        )
        result = run(
            workflow_command(
                self.target,
                "workflow_check.py",
                "requirements-snapshot",
                candidate.relative_to(self.target).as_posix(),
            ),
            cwd=self.target,
        )
        assert_resource_traceback(result)

        brief, _ = approved_requirements(self.target)
        status = self.target / ".codex-workflow/state/STATUS.md"
        schema = self.target / ".codex-workflow/schemas/task-record-v3.schema.json"
        originals = {
            brief: brief.read_bytes(),
            status: status.read_bytes(),
            schema: schema.read_bytes(),
        }
        try:
            brief.write_bytes(
                self.embedded_json_fixture("CODEX_REQUIREMENTS_JSON", payload)
            )
            result = run(
                workflow_command(self.target, "workflow_check.py", "status"),
                cwd=self.target,
            )
            assert_resource_traceback(result)

            brief.write_bytes(originals[brief])
            status.write_bytes(
                self.embedded_json_fixture("CODEX_WORKFLOW_STATUS_JSON", payload)
            )
            result = run(
                workflow_command(self.target, "workflow_check.py", "status"),
                cwd=self.target,
            )
            assert_resource_traceback(result)

            status.write_bytes(originals[status])
            record_path = write_record(self.target, basic_v3_record(self.baseline))
            schema.write_bytes(payload)
            result = run(
                workflow_command(
                    self.target,
                    "workflow_check.py",
                    "preflight",
                    record_relative(record_path, self.target),
                ),
                cwd=self.target,
            )
            assert_resource_traceback(result)
        finally:
            for path, original in originals.items():
                path.write_bytes(original)

    def test_doctor_ordinary_python_is_strictly_read_only_and_creates_no_bytecode(self) -> None:
        script = self.target / ".codex-workflow/bin/workflow_check.py"
        bin_root = script.parent
        self.assertFalse(list(bin_root.rglob("__pycache__")))
        self.assertFalse(list(bin_root.rglob("*.pyc")))
        runtime = self.startup_observation_path().parents[1]
        tracked_before = git(self.target, "status", "--porcelain=v1").stdout
        runtime_before = self.tree_snapshot(runtime)
        env = os.environ.copy()
        env.pop("PYTHONDONTWRITEBYTECODE", None)

        result = subprocess.run(
            [sys.executable, str(script), "doctor"],
            cwd=self.target,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.target, "status", "--porcelain=v1").stdout, tracked_before)
        self.assertEqual(self.tree_snapshot(runtime), runtime_before)
        self.assertFalse(list(bin_root.rglob("__pycache__")))
        self.assertFalse(list(bin_root.rglob("*.pyc")))

    def test_doctor_bootstrap_failure_stays_outside_its_self_diagnosis_claim(self) -> None:
        script = self.target / ".codex-workflow/bin/workflow_check.py"
        script_bytes = script.read_bytes()
        script.unlink()
        result = run([sys.executable, str(script), "doctor"], cwd=self.target)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PACKAGE: PASS", result.stdout)
        self.assertNotIn("PRIMARY NEXT ACTION:", result.stdout)

        script.write_bytes(script_bytes)
        dependency = self.target / ".codex-workflow/bin/workflow_common.py"
        dependency.unlink()
        result = run([sys.executable, str(script), "doctor"], cwd=self.target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ModuleNotFoundError", result.stderr)
        self.assertNotIn("PACKAGE: PASS", result.stdout)
        self.assertNotIn("PRIMARY NEXT ACTION:", result.stdout)

    def test_doctor_loaded_but_worktree_discovery_unknown_is_structured_and_nonzero(self) -> None:
        script = self.target / ".codex-workflow/bin/workflow_check.py"
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        result = run([sys.executable, str(script), "doctor"], cwd=outside)
        self.assertNotEqual(result.returncode, 0)
        self.assert_doctor_shape(result.stdout)
        self.assertIn("PACKAGE: UNKNOWN", result.stdout)
        self.assertIn("GOVERNANCE: UNKNOWN", result.stdout)
        self.assertIn("external bootstrap failure", result.stdout)

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
