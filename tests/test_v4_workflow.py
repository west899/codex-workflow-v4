from __future__ import annotations

import concurrent.futures
import copy
import json
import tempfile
import unittest
import uuid
from contextlib import ExitStack, nullcontext
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock

from support import (
    approved_requirements,
    PACKAGE_ROOT,
    basic_v4_record,
    commit_all,
    configure_v4_architecture_baseline,
    create_baseline,
    developer_evidence_v1,
    git,
    install_project,
    record_relative,
    requirements_fingerprint,
    review_evidence_v1,
    run,
    v4_checkpoint_request,
    v4_observation_receipt,
    workflow_command,
    write_record,
)

BIN_PATH = PACKAGE_ROOT / "payload/.codex-workflow/bin"
sys.path.insert(0, str(BIN_PATH))
from workflow_common import (  # noqa: E402
    WorkflowDataError,
    canonical_delivery,
    contract_fingerprint,
    contract_fingerprint_material,
    decision_fingerprint,
    decision_state_fingerprint,
    observation_fingerprint,
    observation_receipt_fingerprint,
    read_embedded_json,
    replace_embedded_json,
    snapshot_id,
    validate_v4_observation_receipt,
    v4_continuation_path_class,
)
from workflow_paths import WorkflowPaths  # noqa: E402
from workflow_lane import _reset_after_base_refresh  # noqa: E402
import workflow_lane as workflow_lane_module  # noqa: E402
import workflow_state as workflow_state_module  # noqa: E402


class V4WorkflowM2Tests(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict) -> Path:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def _revise_requirements(self, brief: Path) -> str:
        start = "<!-- CODEX_REQUIREMENTS_JSON_START -->"
        end = "<!-- CODEX_REQUIREMENTS_JSON_END -->"
        source = brief.read_text(encoding="utf-8")
        metadata = json.loads(source.split(start, 1)[1].split(end, 1)[0])
        metadata["revision"] = 2
        metadata["requirements"]["capabilities"][0][
            "observable_result"
        ] = "The revised V4 gate passes"
        metadata["requirements"]["calibration_rounds"].append(
            {
                "id": "CAL-002",
                "result": "confirmed",
                "changed_requirement_ids": ["REQ-F-001"],
                "source": "user:v4-revision",
            }
        )
        markdown = source.split(end, 1)[1]
        fingerprint = requirements_fingerprint(metadata, markdown)
        metadata["approval"]["approved_fingerprint"] = fingerprint
        brief.write_text(
            start
            + "\n"
            + json.dumps(metadata, ensure_ascii=False, indent=2)
            + "\n"
            + end
            + markdown,
            encoding="utf-8",
        )
        return fingerprint

    def _prepare_delivery(
        self,
        root: Path,
        *,
        checkpoint_mode: str = "required",
        execution_mode: str = "formal",
        task_id: str = "MVP-001",
    ) -> tuple[Path, Path, str]:
        target = root / "project"
        installed = install_project(target)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        _, requirements_fingerprint = approved_requirements(target)
        architecture_fingerprint = configure_v4_architecture_baseline(target)
        base = create_baseline(target)
        record = basic_v4_record(
            base,
            task_id=task_id,
            requirements_baseline={
                "brief_id": "REQ-001",
                "revision": 1,
                "approval_fingerprint": requirements_fingerprint,
            },
            architecture_fingerprint=architecture_fingerprint,
            checkpoint_mode=checkpoint_mode,
            execution_mode=execution_mode,
        )
        record_path = write_record(target, record)
        product = target / "src/feature.txt"
        product.parent.mkdir(parents=True, exist_ok=True)
        product.write_text("observable v4 delivery\n", encoding="utf-8")
        delivery_commit = commit_all(target, "v4 delivery")
        evidence = self._write_json(
            root / "developer.json", developer_evidence_v1("v4-developer")
        )
        recorded = run(
            workflow_command(
                target,
                "workflow_state.py",
                "record-developer",
                record_relative(record_path, target),
                "--evidence-json",
                str(evidence),
                "--delivery-commit",
                delivery_commit,
                "--apply",
            ),
            cwd=target,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        return target, record_path, delivery_commit

    def _generic_request(self, decision_id: str = "HD-010") -> dict:
        return {
            "id": decision_id,
            "kind": "product_decision",
            "affected_scope": ["current_slice", "user_flow"],
            "latest_decision_point": "before_review",
            "current_delivery_independent": False,
            "question": "Should confirmation happen before the core action?",
            "options": [
                {
                    "id": "A",
                    "label": "Confirm first",
                    "impact": "Adds a reversible confirmation step.",
                    "reversibility": "reversible",
                },
                {
                    "id": "B",
                    "label": "Commit directly",
                    "impact": "Removes one step and raises recovery cost.",
                    "reversibility": "costly",
                },
            ],
            "recommendation": {
                "option_id": "A",
                "reason": "The current outcome prioritizes recovery from mistakes.",
            },
            "product_context": {
                "user_behavior": "changes",
                "data_contract": "unchanged",
                "public_interface": "unchanged",
                "dependencies": "unchanged",
            },
        }

    def _checkpoint_resolution(self, outcome: str) -> dict:
        return {
            "outcome": outcome,
            "decided_by": "test-owner",
            "decided_at": "2026-07-20T01:00:00Z",
            "source": f"external-receipt:{outcome}",
            "rationale": f"The checkpoint outcome is {outcome}.",
        }

    def _acceptance(self, record: dict) -> dict:
        return {
            "acceptance": [
                {
                    "id": item["id"],
                    "status": "passed",
                    "evidence": [f"evidence:{item['id']}:passed"],
                }
                for item in record["acceptance"]
            ],
            "process_retrospective": {
                "completed": True,
                "completed_by": "v4-developer",
                "completed_at": "2026-07-20T02:00:00Z",
                "questions": {
                    "repeated_problem_found": False,
                    "guidance_gap_found": False,
                    "deterministic_check_candidate_found": False,
                },
                "summary": "M2 regression completed without a process proposal.",
            },
            "rule_proposals": [],
            "remaining_risks": [],
        }

    def _approval(self, record: dict) -> dict:
        return {
            "kind": "local_bootstrap",
            "task_id": record["task_id"],
            "target_ref": "refs/heads/main",
            "snapshot_id": record["verification"]["snapshot_id"],
            "delivery_hash": record["verification"]["delivery_hash"],
            "approved_by": "test-owner",
            "approved_at": "2026-07-20T02:30:00Z",
            "source": "user:v4-approval-test",
        }

    def _request_checkpoint(self, target: Path, record_path: Path, root: Path) -> dict:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        request_path = self._write_json(root / "checkpoint.json", v4_checkpoint_request(record))
        requested = run(
            workflow_command(
                target,
                "workflow_state.py",
                "request-decision",
                record_relative(record_path, target),
                "--decision-json",
                str(request_path),
                "--apply",
            ),
            cwd=target,
        )
        self.assertEqual(requested.returncode, 0, requested.stderr)
        return json.loads(record_path.read_text(encoding="utf-8"))["decision_log"][-1]

    def _record_checkpoint(
        self,
        target: Path,
        record_path: Path,
        root: Path,
        decision: dict,
        outcome: str,
    ) -> None:
        resolution = self._checkpoint_resolution(outcome)
        resolution_path = self._write_json(root / f"{outcome}.json", resolution)
        result = run(
            workflow_command(
                target,
                "workflow_state.py",
                "record-decision",
                record_relative(record_path, target),
                "--decision-id",
                decision["id"],
                "--expected-fingerprint",
                decision["decision_fingerprint"],
                "--resolution-json",
                str(resolution_path),
                "--apply",
            ),
            cwd=target,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_decision_dry_run_does_not_create_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "project"
            installed = install_project(target)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            _, requirements_fingerprint = approved_requirements(target)
            architecture_fingerprint = configure_v4_architecture_baseline(target)
            base = create_baseline(target)
            record = basic_v4_record(
                base,
                requirements_baseline={
                    "brief_id": "REQ-001",
                    "revision": 1,
                    "approval_fingerprint": requirements_fingerprint,
                },
                architecture_fingerprint=architecture_fingerprint,
                checkpoint_mode="not_required",
            )
            record_path = write_record(target, record)
            relative = record_relative(record_path, target)
            request_path = self._write_json(
                root / "dry-run-request.json", self._generic_request("HD-DRY-RUN")
            )
            runtime = WorkflowPaths.discover(target).shared_runtime
            runtime_before = {
                path.relative_to(runtime).as_posix(): (
                    "directory" if path.is_dir() else path.read_bytes()
                )
                for path in runtime.rglob("*")
            }
            before = record_path.read_bytes()

            dry = run(
                workflow_command(
                    target, "workflow_state.py", "request-decision", relative,
                    "--decision-json", str(request_path),
                ),
                cwd=target,
            )
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(record_path.read_bytes(), before)
            runtime_after = {
                path.relative_to(runtime).as_posix(): (
                    "directory" if path.is_dir() else path.read_bytes()
                )
                for path in runtime.rglob("*")
            }
            self.assertEqual(runtime_after, runtime_before)

    def test_decision_request_is_dry_run_derived_idempotent_and_conflict_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            relative = record_relative(record_path, target)
            request = self._generic_request()
            request_path = self._write_json(root / "request.json", request)
            before = record_path.read_bytes()

            dry = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    relative,
                    "--decision-json",
                    str(request_path),
                ),
                cwd=target,
            )
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(record_path.read_bytes(), before)
            self.assertIn('"apply": false', dry.stdout)

            attempted_override = {**request, "blocking": False}
            self._write_json(request_path, attempted_override)
            override = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    relative,
                    "--decision-json",
                    str(request_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(override.returncode, 0)
            self.assertIn("unknown fields: blocking", override.stderr)
            self.assertEqual(record_path.read_bytes(), before)
            self._write_json(request_path, request)

            applied = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    relative,
                    "--decision-json",
                    str(request_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            decision = current["decision_log"][0]
            self.assertTrue(decision["blocking"])
            self.assertNotIn("blocking", request)
            generation = current["generation"]
            contract_fingerprint = current["contract_fingerprint"]

            retry = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    relative,
                    "--decision-json",
                    str(request_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertIn("STATE_NOOP", retry.stdout)
            self.assertEqual(
                json.loads(record_path.read_text(encoding="utf-8"))["generation"], generation
            )

            conflicting = copy.deepcopy(request)
            conflicting["question"] += " Changed."
            self._write_json(request_path, conflicting)
            conflict = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    relative,
                    "--decision-json",
                    str(request_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("conflicting request material", conflict.stderr)

            sealed = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    relative,
                    "--evidence-json",
                    str(root / "developer.json"),
                    "--delivery-commit",
                    "HEAD",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(sealed.returncode, 0)
            self.assertIn("open decision", sealed.stderr)
            self.assertEqual(
                json.loads(record_path.read_text(encoding="utf-8"))["contract_fingerprint"],
                contract_fingerprint,
            )
            current = json.loads(record_path.read_text(encoding="utf-8"))
            review_path = self._write_json(
                root / "blocked-review.json", review_evidence_v1("v4-reviewer", current)
            )
            acceptance_path = self._write_json(
                root / "blocked-acceptance.json", self._acceptance(current)
            )
            approval_path = self._write_json(
                root / "blocked-approval.json", self._approval(current)
            )
            bypasses = (
                workflow_command(target, "workflow_check.py", "preflight", relative),
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
                workflow_command(
                    target, "workflow_state.py", "complete-task", relative,
                    "--acceptance-json", str(acceptance_path), "--apply",
                ),
                workflow_command(target, "workflow_check.py", "gate", relative),
                workflow_command(
                    target, "workflow_state.py", "record-approval", relative,
                    "--approval-json", str(approval_path), "--apply",
                ),
                workflow_command(
                    target, "workflow_state.py", "prepare-integration", relative,
                    "--mode", "remote_pr_ci", "--apply",
                ),
            )
            for command in bypasses:
                bypass = run(command, cwd=target)
                self.assertNotEqual(bypass.returncode, 0)
                self.assertIn("open decision", bypass.stderr)

    def test_decision_state_identity_blocks_direct_resolution_and_later_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            request_path = self._write_json(
                root / "decision.json", self._generic_request("HD-STATE-IDENTITY")
            )
            requested = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    relative,
                    "--decision-json",
                    str(request_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(requested.returncode, 0, requested.stderr)
            open_state = json.loads(record_path.read_text(encoding="utf-8"))
            decision = open_state["decision_log"][0]
            resolution = {
                "selected_option_id": "A",
                "decided_by": "test-owner",
                "decided_at": "2026-07-20T01:00:00Z",
                "source": "external-receipt:state-identity",
                "rationale": "The reversible option is approved.",
            }

            directly_resolved = copy.deepcopy(open_state)
            directly_resolved["decision_log"][0].update(
                {"status": "resolved", "resolution": copy.deepcopy(resolution)}
            )
            self._write_json(record_path, directly_resolved)
            stale_review_path = self._write_json(
                root / "stale-review.json",
                review_evidence_v1("v4-reviewer", directly_resolved),
            )
            before_direct = record_path.read_bytes()
            bypasses = (
                workflow_command(target, "workflow_check.py", "preflight", relative),
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-review",
                    relative,
                    "--review-json",
                    str(stale_review_path),
                    "--apply",
                ),
            )
            for command in bypasses:
                bypass = run(command, cwd=target)
                self.assertNotEqual(bypass.returncode, 0)
                self.assertIn("state fingerprint", bypass.stderr)
                self.assertEqual(record_path.read_bytes(), before_direct)

            semantic_cases = {
                "option": (
                    {**resolution, "selected_option_id": "DOES-NOT-EXIST"},
                    "select one declared option ID",
                ),
                "time": (
                    {**resolution, "decided_at": "not-a-time"},
                    "must be an ISO-8601 timestamp",
                ),
                "source": (
                    {**resolution, "source": "agent:not-external"},
                    "must use user:, provider:, or external-receipt:",
                ),
            }
            for name, (invalid_resolution, expected_message) in semantic_cases.items():
                with self.subTest(lifecycle_semantics=name):
                    semantically_invalid = copy.deepcopy(open_state)
                    invalid_decision = semantically_invalid["decision_log"][0]
                    invalid_decision.update(
                        {
                            "status": "resolved",
                            "resolution": invalid_resolution,
                        }
                    )
                    invalid_decision["decision_state_fingerprint"] = (
                        decision_state_fingerprint(invalid_decision)
                    )
                    self._write_json(record_path, semantically_invalid)
                    rejected = run(
                        workflow_command(
                            target, "workflow_check.py", "preflight", relative
                        ),
                        cwd=target,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn(expected_message, rejected.stderr)

            self._write_json(record_path, open_state)
            resolution_path = self._write_json(root / "resolution.json", resolution)
            resolved = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    relative,
                    "--decision-id",
                    decision["id"],
                    "--expected-fingerprint",
                    decision["decision_fingerprint"],
                    "--resolution-json",
                    str(resolution_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            resolved_state = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(
                resolved_state["decision_log"][0]["decision_state_fingerprint"],
                decision_state_fingerprint(resolved_state["decision_log"][0]),
            )

            review_path = self._write_json(
                root / "review.json", review_evidence_v1("v4-reviewer", resolved_state)
            )
            reviewed = run(
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            reviewed_state = json.loads(record_path.read_text(encoding="utf-8"))
            acceptance_path = self._write_json(
                root / "acceptance.json", self._acceptance(reviewed_state)
            )

            tampered_review = copy.deepcopy(reviewed_state)
            tampered_review["decision_log"][0]["resolution"][
                "decided_by"
            ] = "forged-owner"
            self._write_json(record_path, tampered_review)
            before_completion = record_path.read_bytes()
            rejected_completion = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "complete-task",
                    relative,
                    "--acceptance-json",
                    str(acceptance_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(rejected_completion.returncode, 0)
            self.assertIn("state fingerprint", rejected_completion.stderr)
            self.assertEqual(record_path.read_bytes(), before_completion)

            self._write_json(record_path, reviewed_state)
            completed = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "complete-task",
                    relative,
                    "--acceptance-json",
                    str(acceptance_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            completed_state = json.loads(record_path.read_text(encoding="utf-8"))
            completed_state["decision_log"][0]["resolution"][
                "decided_by"
            ] = "forged-owner"
            self._write_json(record_path, completed_state)
            before_gate = record_path.read_bytes()
            for command in (
                workflow_command(target, "workflow_check.py", "gate", relative),
                workflow_command(
                    target, "workflow_state.py", "mark-verified", relative, "--apply"
                ),
            ):
                bypass = run(command, cwd=target)
                self.assertNotEqual(bypass.returncode, 0)
                self.assertIn("state fingerprint", bypass.stderr)
                self.assertEqual(record_path.read_bytes(), before_gate)

    def test_concurrent_decision_answers_have_one_cas_winner_and_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            relative = record_relative(record_path, target)
            request_path = self._write_json(root / "request.json", self._generic_request())
            requested = run(
                workflow_command(
                    target, "workflow_state.py", "request-decision", relative,
                    "--decision-json", str(request_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(requested.returncode, 0, requested.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            decision = current["decision_log"][0]
            generation = current["generation"]
            resolutions = []
            for option in ("A", "B"):
                resolutions.append(
                    self._write_json(
                        root / f"resolution-{option}.json",
                        {
                            "selected_option_id": option,
                            "decided_by": "test-owner",
                            "decided_at": "2026-07-20T01:00:00Z",
                            "source": f"external-receipt:choice-{option}",
                            "rationale": f"Choose {option}.",
                        },
                    )
                )

            def answer(path: Path):
                return run(
                    workflow_command(
                        target,
                        "workflow_state.py",
                        "record-decision",
                        relative,
                        "--decision-id",
                        decision["id"],
                        "--expected-fingerprint",
                        decision["decision_fingerprint"],
                        "--expected-generation",
                        str(generation),
                        "--resolution-json",
                        str(path),
                        "--apply",
                    ),
                    cwd=target,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(answer, resolutions))
            self.assertEqual(sorted(result.returncode for result in results), [0, 1])
            self.assertTrue(any("Generation conflict" in result.stderr for result in results))
            resolved = json.loads(record_path.read_text(encoding="utf-8"))["decision_log"][0]
            self.assertEqual(resolved["status"], "resolved")
            self.assertIn(resolved["resolution"]["selected_option_id"], {"A", "B"})

            winner_path = resolutions[0 if resolved["resolution"]["selected_option_id"] == "A" else 1]
            retry = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    relative,
                    "--decision-id",
                    decision["id"],
                    "--expected-fingerprint",
                    decision["decision_fingerprint"],
                    "--resolution-json",
                    str(winner_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertIn("STATE_NOOP", retry.stdout)

            loser_path = resolutions[1 if winner_path == resolutions[0] else 0]
            conflict = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    relative,
                    "--decision-id",
                    decision["id"],
                    "--expected-fingerprint",
                    decision["decision_fingerprint"],
                    "--resolution-json",
                    str(loser_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("Conflicting decision answer", conflict.stderr)

    def test_checkpoint_receipt_rejects_stale_wrong_sensitive_and_expired_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            relative = record_relative(record_path, target)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            baseline = v4_checkpoint_request(record)
            cases = {
                "snapshot": lambda item: item["observation_receipt"].update(
                    {"snapshot_id": "0" * 64}
                ),
                "contract": lambda item: item["observation_receipt"].update(
                    {"contract_fingerprint": "0" * 64}
                ),
                "artifact": lambda item: item["observation_receipt"]["artifact"].update(
                    {"digest": "0" * 64}
                ),
                "artifact-reference": lambda item: item["observation_receipt"][
                    "artifact"
                ].update({"reference": "git:" + "0" * 40}),
                "requirements": lambda item: item["observation_receipt"].update(
                    {"requirement_ids": ["REQ-WRONG"]}
                ),
                "acceptance": lambda item: item["observation_receipt"].update(
                    {"acceptance_ids": ["AC-WRONG"]}
                ),
                "fixture": lambda item: item["observation_receipt"]["fixture"].update(
                    {"reference": "fixture:wrong"}
                ),
                "real-unredacted": lambda item: item["observation_receipt"][
                    "fixture"
                ].update({"data_class": "real"}),
                "entrypoint": lambda item: item["observation_receipt"]["entrypoint"].update(
                    {"reference": "project-script:wrong-entrypoint"}
                ),
                "resolved-entrypoint": lambda item: item["observation_receipt"][
                    "entrypoint"
                ].update({"resolved_reference": "https://attacker.invalid/not-the-recipe"}),
                "healthcheck": lambda item: item["observation_receipt"]["healthcheck"].update(
                    {"status": "failed"}
                ),
                "sensitive": lambda item: item["observation_receipt"]["evidence_refs"].append(
                    "https://preview.invalid/result?token=secret"
                ),
                "sensitive-name": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["stable_output"].append(
                    {
                        "name": "access_token",
                        "value": "opaque-live-credential-123456789",
                    }
                ),
                "signed-query": lambda item: item["observation_receipt"]["evidence_refs"].append(
                    "https://preview.invalid/result?sv=2026-07-20&sig=FAKE_SIGNED_QUERY_VALUE"
                ),
                "token-prefix": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["stable_output"].append(
                    {"name": "sessionId", "value": "ghp_FAKE_TOKEN_VALUE_123456789"}
                ),
                "phone": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["assertions"][0].update(
                    {"actual": "The operator contact is +1 (415) 555-2671."}
                ),
                "command-mismatch": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["method_evidence"].update(
                    {"command_ref": "command:unrelated-program"}
                ),
                "password-prefix": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["assertions"][0].update({"actual": "password: hunter12345"}),
                "secret-prefix": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["assertions"][0].update({"actual": "secret: topsecretvalue"}),
                "cookie-prefix": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["assertions"][0].update({"actual": "cookie: sessionid=abcdef123456"}),
                "set-cookie-prefix": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["assertions"][0].update({"actual": "set-cookie: sid=abcdef123456"}),
                "pii-prefix": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["assertions"][0].update({"actual": "pii: customer-name"}),
                "authorization-prefix": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["assertions"][0].update(
                    {"actual": "Authorization : Bearer abcdefgh12345678"}
                ),
                "api-key-assignment": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["assertions"][0].update({"actual": "API_KEY = abcdefgh12345678"}),
                "access-token-assignment": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["assertions"][0].update({"actual": "accessToken: abcdefgh12345678"}),
                "private-key-json": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["assertions"][0].update(
                    {"actual": '{"privateKey":"abcdefgh12345678"}'}
                ),
                "session-assignment": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["assertions"][0].update({"actual": "session_id=abcdefgh12345678"}),
                "credential-assignment": lambda item: item["observation_receipt"][
                    "normalized_result"
                ]["assertions"][0].update({"actual": "credential: abcdefgh12345678"}),
                "empty-user-source": lambda item: item["observation_receipt"].update(
                    {"observer_source": "user:"}
                ),
                "blank-user-source": lambda item: item["observation_receipt"].update(
                    {"observer_source": "user:   "}
                ),
                "empty-provider-source": lambda item: item["observation_receipt"].update(
                    {"observer_source": "provider:"}
                ),
                "empty-receipt-source": lambda item: item["observation_receipt"].update(
                    {"observer_source": "external-receipt:"}
                ),
                "future-times": lambda item: (
                    item["observation_receipt"].update(
                        {"observed_at": "2099-01-01T00:10:00Z"}
                    ),
                    item["observation_receipt"]["entrypoint"].update(
                        {"checked_at": "2099-01-01T00:00:00Z"}
                    ),
                    item["observation_receipt"]["healthcheck"].update(
                        {"checked_at": "2099-01-01T00:00:00Z"}
                    ),
                ),
                "expired": lambda item: item["observation_receipt"]["environment"].update(
                    {
                        "kind": "isolated_preview",
                        "reference": "https://preview.invalid/result",
                        "expires_at": "2000-01-01T00:00:00Z",
                    }
                ),
                "recipe": lambda item: item["observation_receipt"].update(
                    {"recipe_replayed": False}
                ),
            }
            for name, mutate in cases.items():
                with self.subTest(name=name):
                    request = copy.deepcopy(baseline)
                    request["id"] = f"HD-{name.upper()}"
                    mutate(request)
                    request_path = self._write_json(root / f"{name}.json", request)
                    before = record_path.read_bytes()
                    result = run(
                        workflow_command(
                            target,
                            "workflow_state.py",
                            "request-decision",
                            relative,
                            "--decision-json",
                            str(request_path),
                            "--apply",
                        ),
                        cwd=target,
                    )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(record_path.read_bytes(), before)

    def test_decision_writes_reject_stale_contract_and_sibling_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            relative = record_relative(record_path, target)

            stale_contract = json.loads(record_path.read_text(encoding="utf-8"))
            stale_contract["contract_fingerprint"] = "0" * 64
            self._write_json(record_path, stale_contract)
            stale_before = record_path.read_bytes()
            stale_request_path = self._write_json(
                root / "stale-contract-request.json",
                self._generic_request("HD-STALE-CONTRACT"),
            )
            stale_request = run(
                workflow_command(
                    target, "workflow_state.py", "request-decision", relative,
                    "--decision-json", str(stale_request_path), "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(stale_request.returncode, 0)
            self.assertIn("contract_fingerprint is stale", stale_request.stderr)
            self.assertEqual(record_path.read_bytes(), stale_before)

            stale_contract["contract_fingerprint"] = contract_fingerprint(stale_contract)
            self._write_json(record_path, stale_contract)
            for decision_id in ("HD-TARGET", "HD-SIBLING"):
                request_path = self._write_json(
                    root / f"{decision_id}.json", self._generic_request(decision_id)
                )
                requested = run(
                    workflow_command(
                        target, "workflow_state.py", "request-decision", relative,
                        "--decision-json", str(request_path), "--apply",
                    ),
                    cwd=target,
                )
                self.assertEqual(requested.returncode, 0, requested.stderr)

            current = json.loads(record_path.read_text(encoding="utf-8"))
            target_decision = next(
                item for item in current["decision_log"] if item["id"] == "HD-TARGET"
            )
            sibling = next(
                item for item in current["decision_log"] if item["id"] == "HD-SIBLING"
            )
            sibling["question"] += " Drifted without rebinding."
            self._write_json(record_path, current)
            sibling_before = record_path.read_bytes()
            resolution_path = self._write_json(
                root / "target-resolution.json",
                {
                    "selected_option_id": "A",
                    "decided_by": "test-owner",
                    "decided_at": "2026-07-20T01:45:00Z",
                    "source": "external-receipt:target-choice",
                    "rationale": "Resolve the exact target decision.",
                },
            )
            stale_sibling = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    relative,
                    "--decision-id",
                    target_decision["id"],
                    "--expected-fingerprint",
                    target_decision["decision_fingerprint"],
                    "--resolution-json",
                    str(resolution_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(stale_sibling.returncode, 0)
            self.assertIn("HD-SIBLING fingerprint is stale", stale_sibling.stderr)
            self.assertEqual(record_path.read_bytes(), sibling_before)

    def test_live_requirements_drift_blocks_all_v4_sources_and_direct_writes(self) -> None:
        for source_type in (
            "user_directive",
            "incident",
            "maintenance",
            "mvp_backlog",
        ):
            with self.subTest(source_type=source_type):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    target, record_path, _ = self._prepare_delivery(
                        root, checkpoint_mode="not_required"
                    )
                    relative = record_relative(record_path, target)
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    record["source"]["type"] = source_type
                    record["source"]["reference"] = (
                        record["task_id"]
                        if source_type == "mvp_backlog"
                        else f"{source_type}:test"
                    )
                    record["contract_fingerprint"] = contract_fingerprint(record)
                    self._write_json(record_path, record)
                    resealed = run(
                        workflow_command(
                            target,
                            "workflow_state.py",
                            "record-developer",
                            relative,
                            "--evidence-json",
                            str(root / "developer.json"),
                            "--delivery-commit",
                            "HEAD",
                            "--apply",
                        ),
                        cwd=target,
                    )
                    self.assertEqual(resealed.returncode, 0, resealed.stderr)

                    decision_id = "HD-LIVE-" + source_type.upper().replace("_", "-")
                    request_path = self._write_json(
                        root / "live-request.json", self._generic_request(decision_id)
                    )
                    requested = run(
                        workflow_command(
                            target, "workflow_state.py", "request-decision", relative,
                            "--decision-json", str(request_path), "--apply",
                        ),
                        cwd=target,
                    )
                    self.assertEqual(requested.returncode, 0, requested.stderr)
                    current = json.loads(record_path.read_text(encoding="utf-8"))
                    decision = next(
                        item for item in current["decision_log"]
                        if item["id"] == decision_id
                    )
                    resolution_path = self._write_json(
                        root / "live-resolution.json",
                        {
                            "selected_option_id": "A",
                            "decided_by": "test-owner",
                            "decided_at": "2026-07-20T01:50:00Z",
                            "source": f"external-receipt:{source_type}",
                            "rationale": "Resolve only against the current live baseline.",
                        },
                    )
                    review_path = self._write_json(
                        root / "live-review.json",
                        review_evidence_v1("live-reviewer", current),
                    )
                    acceptance_path = self._write_json(
                        root / "live-acceptance.json", self._acceptance(current)
                    )
                    approval_path = self._write_json(
                        root / "live-approval.json",
                        {
                            "kind": "local_bootstrap",
                            "task_id": current["task_id"],
                            "target_ref": "refs/heads/main",
                            "snapshot_id": current["verification"]["snapshot_id"],
                            "delivery_hash": current["verification"]["delivery_hash"],
                            "approved_by": "test-owner",
                            "approved_at": "2026-07-20T01:55:00Z",
                            "source": "user:live-baseline-test",
                        },
                    )
                    brief = target / ".codex-workflow/governance/requirements/REQ-001.md"
                    self._revise_requirements(brief)
                    before = record_path.read_bytes()
                    blocked_commands = {
                        "preflight": workflow_command(
                            target, "workflow_check.py", "preflight", relative
                        ),
                        "gate": workflow_command(
                            target, "workflow_check.py", "gate", relative
                        ),
                        "request-decision": workflow_command(
                            target, "workflow_state.py", "request-decision", relative,
                            "--decision-json", str(request_path), "--apply",
                        ),
                        "record-decision": workflow_command(
                            target,
                            "workflow_state.py",
                            "record-decision",
                            relative,
                            "--decision-id",
                            decision["id"],
                            "--expected-fingerprint",
                            decision["decision_fingerprint"],
                            "--resolution-json",
                            str(resolution_path),
                            "--apply",
                        ),
                        "record-developer": workflow_command(
                            target,
                            "workflow_state.py",
                            "record-developer",
                            relative,
                            "--evidence-json",
                            str(root / "developer.json"),
                            "--delivery-commit",
                            "HEAD",
                            "--apply",
                        ),
                        "record-review": workflow_command(
                            target, "workflow_state.py", "record-review", relative,
                            "--review-json", str(review_path), "--apply",
                        ),
                        "complete-task": workflow_command(
                            target, "workflow_state.py", "complete-task", relative,
                            "--acceptance-json", str(acceptance_path), "--apply",
                        ),
                        "record-approval": workflow_command(
                            target, "workflow_state.py", "record-approval", relative,
                            "--approval-json", str(approval_path), "--apply",
                        ),
                    }
                    for action, command in blocked_commands.items():
                        with self.subTest(source_type=source_type, action=action):
                            blocked = run(command, cwd=target)
                            self.assertNotEqual(blocked.returncode, 0)
                            self.assertIn("live Requirements Brief differs", blocked.stderr)
                            self.assertEqual(record_path.read_bytes(), before)

    def test_all_v4_sources_require_a_semantically_valid_live_brief(self) -> None:
        marker = "CODEX_REQUIREMENTS_BASELINE"
        for source_type in (
            "user_directive",
            "incident",
            "maintenance",
            "mvp_backlog",
        ):
            with self.subTest(source_type=source_type):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    target = root / "project"
                    installed = install_project(target)
                    self.assertEqual(installed.returncode, 0, installed.stderr)
                    brief, _ = approved_requirements(target)
                    start = "<!-- CODEX_REQUIREMENTS_JSON_START -->"
                    end = "<!-- CODEX_REQUIREMENTS_JSON_END -->"
                    source = brief.read_text(encoding="utf-8")
                    metadata = json.loads(source.split(start, 1)[1].split(end, 1)[0])
                    metadata["requirements"]["capabilities"][0]["status"] = "unconfirmed"
                    markdown = source.split(end, 1)[1]
                    fingerprint = requirements_fingerprint(metadata, markdown)
                    metadata["approval"]["approved_fingerprint"] = fingerprint
                    brief.write_text(
                        start
                        + "\n"
                        + json.dumps(metadata, ensure_ascii=False, indent=2)
                        + "\n"
                        + end
                        + markdown,
                        encoding="utf-8",
                    )
                    baseline = {
                        "brief_id": metadata["brief_id"],
                        "revision": metadata["revision"],
                        "approval_fingerprint": fingerprint,
                    }
                    for path in (
                        target / ".codex-workflow/governance/PROJECT.md",
                        target / ".codex-workflow/state/MVP_BACKLOG.md",
                    ):
                        text = path.read_text(encoding="utf-8")
                        managed = read_embedded_json(text, marker)
                        managed.update(baseline)
                        path.write_text(
                            replace_embedded_json(text, marker, managed),
                            encoding="utf-8",
                        )
                    architecture_fingerprint = configure_v4_architecture_baseline(target)
                    base = create_baseline(target)
                    record = basic_v4_record(
                        base,
                        requirements_baseline=baseline,
                        architecture_fingerprint=architecture_fingerprint,
                        checkpoint_mode="not_required",
                    )
                    record["source"].update(
                        {
                            "type": source_type,
                            "reference": (
                                record["task_id"]
                                if source_type == "mvp_backlog"
                                else f"{source_type}:invalid-live-brief"
                            ),
                        }
                    )
                    record["contract_fingerprint"] = contract_fingerprint(record)
                    record_path = write_record(target, record)
                    preflight = run(
                        workflow_command(
                            target,
                            "workflow_check.py",
                            "preflight",
                            record_relative(record_path, target),
                        ),
                        cwd=target,
                    )
                    self.assertNotEqual(preflight.returncode, 0)
                    self.assertIn("Must capability REQ-F-001 must be confirmed", preflight.stderr)

    def test_live_architecture_drift_blocks_direct_v4_state_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            request_path = self._write_json(
                root / "architecture-bound-request.json",
                self._generic_request("HD-ARCH-BOUND"),
            )
            requested = run(
                workflow_command(
                    target, "workflow_state.py", "request-decision", relative,
                    "--decision-json", str(request_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(requested.returncode, 0, requested.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            decision = current["decision_log"][0]
            resolution_path = self._write_json(
                root / "architecture-bound-resolution.json",
                {
                    "selected_option_id": "A",
                    "decided_by": "test-owner",
                    "decided_at": "2026-07-20T01:58:00Z",
                    "source": "external-receipt:architecture-bound",
                    "rationale": "Resolve only while the architecture baseline is current.",
                },
            )
            review_path = self._write_json(
                root / "architecture-bound-review.json",
                review_evidence_v1("v4-reviewer", current),
            )
            late_request_path = self._write_json(
                root / "architecture-late-request.json",
                self._generic_request("HD-ARCH-LATE"),
            )
            decisions_path = target / ".codex-workflow/governance/DECISIONS.md"
            start = "<!-- CODEX_ARCHITECTURE_BASELINE_START -->"
            end = "<!-- CODEX_ARCHITECTURE_BASELINE_END -->"
            source = decisions_path.read_text(encoding="utf-8")
            baseline = json.loads(source.split(start, 1)[1].split(end, 1)[0])
            baseline["guardrails"][0]["statement"] += " Drifted before state write."
            decisions_path.write_text(
                source.split(start, 1)[0]
                + start
                + "\n"
                + json.dumps(baseline, ensure_ascii=False, indent=2)
                + "\n"
                + end
                + source.split(end, 1)[1],
                encoding="utf-8",
            )
            before = record_path.read_bytes()
            commands = (
                workflow_command(
                    target, "workflow_state.py", "request-decision", relative,
                    "--decision-json", str(late_request_path), "--apply",
                ),
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    relative,
                    "--decision-id",
                    decision["id"],
                    "--expected-fingerprint",
                    decision["decision_fingerprint"],
                    "--resolution-json",
                    str(resolution_path),
                    "--apply",
                ),
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
            )
            for command in commands:
                with self.subTest(command=command[3] if len(command) > 3 else command):
                    blocked = run(command, cwd=target)
                    self.assertNotEqual(blocked.returncode, 0)
                    self.assertIn(
                        "architecture baseline approval fingerprint is stale",
                        blocked.stderr,
                    )
                    self.assertEqual(record_path.read_bytes(), before)

    def test_supporting_task_requires_an_existing_reciprocal_core_slice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "project"
            installed = install_project(target)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            _, requirements_fingerprint = approved_requirements(target)
            architecture_fingerprint = configure_v4_architecture_baseline(target)
            base = create_baseline(target)
            record = basic_v4_record(
                base,
                task_id="MVP-SUP-ORPHAN",
                requirements_baseline={
                    "brief_id": "REQ-001",
                    "revision": 1,
                    "approval_fingerprint": requirements_fingerprint,
                },
                architecture_fingerprint=architecture_fingerprint,
                checkpoint_mode="not_required",
            )
            contract = record["delivery_contract"]
            contract.update(
                {
                    "kind": "supporting",
                    "focus_slice_id": "MVP-NONEXISTENT",
                    "supports_task_id": "MVP-NONEXISTENT",
                    "supporting": {
                        "necessity": "The core slice needs this narrow prerequisite.",
                        "minimal_boundary": "Only the required callable contract is included.",
                        "blocked_observation_step": "Perform the core action.",
                    },
                }
            )
            record["contract_fingerprint"] = contract_fingerprint(record)
            record_path = write_record(target, record)
            blocked = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "preflight",
                    record_relative(record_path, target),
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("supporting focus task record is missing", blocked.stderr)

            for kind in ("hardening", "governance"):
                with self.subTest(kind=kind):
                    orphan = basic_v4_record(
                        base,
                        task_id=f"MVP-{kind.upper()}-ORPHAN",
                        requirements_baseline={
                            "brief_id": "REQ-001",
                            "revision": 1,
                            "approval_fingerprint": requirements_fingerprint,
                        },
                        architecture_fingerprint=architecture_fingerprint,
                        checkpoint_mode="not_required",
                    )
                    orphan["delivery_contract"].update(
                        {
                            "kind": kind,
                            "focus_slice_id": "MVP-NONEXISTENT",
                            "supports_task_id": None,
                            "supporting": None,
                        }
                    )
                    orphan["contract_fingerprint"] = contract_fingerprint(orphan)
                    orphan_path = write_record(target, orphan)
                    rejected = run(
                        workflow_command(
                            target,
                            "workflow_check.py",
                            "preflight",
                            record_relative(orphan_path, target),
                        ),
                        cwd=target,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn(
                        f"{kind} focus task record is missing", rejected.stderr
                    )

    def test_inline_architecture_guardrail_must_match_live_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["delivery_contract"]["architecture"]["guardrails"][0][
                "statement"
            ] = "No architecture restriction applies."
            record["contract_fingerprint"] = contract_fingerprint(record)
            self._write_json(record_path, record)
            blocked = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "preflight",
                    record_relative(record_path, target),
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("statement differs from the live baseline", blocked.stderr)

    def test_open_decision_cannot_weaken_derived_blocking_classification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            request_path = self._write_json(
                root / "blocking-request.json", self._generic_request("HD-TAMPER-BLOCK")
            )
            requested = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    relative,
                    "--decision-json",
                    str(request_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(requested.returncode, 0, requested.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            decision = record["decision_log"][0]
            decision["blocking"] = False
            decision["decision_fingerprint"] = decision_fingerprint(decision)
            self._write_json(record_path, record)
            before = record_path.read_bytes()
            blocked = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    relative,
                    "--evidence-json",
                    str(root / "developer.json"),
                    "--delivery-commit",
                    "HEAD",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("stale blocking classification", blocked.stderr)
            self.assertEqual(record_path.read_bytes(), before)

    def test_resolved_decision_uses_derived_blocking_for_stale_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            request_path = self._write_json(
                root / "resolved-blocking-request.json",
                self._generic_request("HD-RESOLVED-TAMPER"),
            )
            requested = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    relative,
                    "--decision-json",
                    str(request_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(requested.returncode, 0, requested.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            decision = record["decision_log"][0]
            resolution_path = self._write_json(
                root / "resolved-blocking-resolution.json",
                {
                    "selected_option_id": "A",
                    "decided_by": "test-owner",
                    "decided_at": "2026-07-20T02:00:00Z",
                    "source": "external-receipt:resolved-blocking",
                    "rationale": "Resolve the current product choice.",
                },
            )
            resolved = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    relative,
                    "--decision-id",
                    decision["id"],
                    "--expected-fingerprint",
                    decision["decision_fingerprint"],
                    "--resolution-json",
                    str(resolution_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            decision = record["decision_log"][0]
            decision["requirements_baseline"]["revision"] += 1
            decision["blocking"] = False
            decision["decision_fingerprint"] = decision_fingerprint(decision)
            self._write_json(record_path, record)
            before = record_path.read_bytes()
            blocked = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    relative,
                    "--evidence-json",
                    str(root / "developer.json"),
                    "--delivery-commit",
                    "HEAD",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("stale Requirements baseline", blocked.stderr)
            self.assertEqual(record_path.read_bytes(), before)

    def test_decision_resolution_rejects_future_witness_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            request_path = self._write_json(
                root / "future-time-request.json",
                self._generic_request("HD-FUTURE-TIME"),
            )
            requested = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    relative,
                    "--decision-json",
                    str(request_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(requested.returncode, 0, requested.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            decision = record["decision_log"][0]
            resolution_path = self._write_json(
                root / "future-time-resolution.json",
                {
                    "selected_option_id": "A",
                    "decided_by": "test-owner",
                    "decided_at": "2099-01-01T00:00:00Z",
                    "source": "external-receipt:future-time",
                    "rationale": "This future witness must be rejected.",
                },
            )
            before = record_path.read_bytes()
            blocked = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    relative,
                    "--decision-id",
                    decision["id"],
                    "--expected-fingerprint",
                    decision["decision_fingerprint"],
                    "--resolution-json",
                    str(resolution_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("must not be in the future", blocked.stderr)
            self.assertEqual(record_path.read_bytes(), before)

    def test_checkpoint_answer_cannot_precede_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            decision = self._request_checkpoint(target, record_path, root)
            resolution_path = self._write_json(
                root / "pre-observation-answer.json",
                {
                    "outcome": "accepted",
                    "decided_by": "test-owner",
                    "decided_at": "2000-01-01T00:00:00Z",
                    "source": "external-receipt:pre-observation-answer",
                    "rationale": "This answer predates the observation and is invalid.",
                },
            )
            before = record_path.read_bytes()
            blocked = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    record_relative(record_path, target),
                    "--decision-id",
                    decision["id"],
                    "--expected-fingerprint",
                    decision["decision_fingerprint"],
                    "--resolution-json",
                    str(resolution_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("cannot precede its observation", blocked.stderr)
            self.assertEqual(record_path.read_bytes(), before)

    def test_linked_lane_uses_coordinator_live_requirements_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "project"
            lane = root / "lane"
            installed = install_project(target, parallel_mode="local_worktree")
            self.assertEqual(installed.returncode, 0, installed.stderr)
            brief, requirements_fingerprint = approved_requirements(target)
            architecture_fingerprint = configure_v4_architecture_baseline(target)
            base = create_baseline(target)
            record = basic_v4_record(
                base,
                requirements_baseline={
                    "brief_id": "REQ-001",
                    "revision": 1,
                    "approval_fingerprint": requirements_fingerprint,
                },
                architecture_fingerprint=architecture_fingerprint,
                checkpoint_mode="not_required",
            )
            record["source"].update({"type": "mvp_backlog", "reference": record["task_id"]})
            record["lane"].update(
                {
                    "lane_id": "lane-MVP-001",
                    "mode": "local_worktree",
                    "branch": "codex/task/MVP-001",
                }
            )
            record["contract_fingerprint"] = contract_fingerprint(record)
            record_path = write_record(target, record)
            task_base = commit_all(target, "authorized V4 lane task")
            git(
                target,
                "worktree",
                "add",
                "-b",
                "codex/task/MVP-001",
                str(lane),
                task_base,
            )
            try:
                relative = record_relative(record_path, target)
                lane_record = lane / relative
                paths = WorkflowPaths.discover(target)
                registry_path = (
                    paths.shared_runtime / "registry/lanes/lane-MVP-001.json"
                )
                registry_path.parent.mkdir(parents=True, exist_ok=True)
                self._write_json(
                    registry_path,
                    {
                        "task_id": record["task_id"],
                        "lane_id": record["lane"]["lane_id"],
                        "worktree": str(lane),
                        "record": relative,
                    },
                )

                revised_fingerprint = self._revise_requirements(brief)
                impact = run(
                    workflow_command(
                        target,
                        "workflow_check.py",
                        "requirements-impact",
                        str(brief.relative_to(target)),
                        "--json",
                    ),
                    cwd=target,
                )
                self.assertEqual(impact.returncode, 0, impact.stderr)
                analysis = json.loads(impact.stdout.splitlines()[0])
                applied = run(
                    workflow_command(
                        target,
                        "workflow_state.py",
                        "apply-requirements-impact",
                        str(brief.relative_to(target)),
                        "--expected-fingerprint",
                        revised_fingerprint,
                        "--apply",
                    ),
                    cwd=target,
                )
                self.assertEqual(applied.returncode, 0, applied.stderr)
                self.assertEqual(analysis["active_tasks"][0]["task_id"], record["task_id"])

                request_path = self._write_json(
                    root / "linked-request.json", self._generic_request("HD-LINKED-STALE")
                )
                before = lane_record.read_bytes()
                blocked = run(
                    workflow_command(
                        lane,
                        "workflow_state.py",
                        "request-decision",
                        relative,
                        "--decision-json",
                        str(request_path),
                        "--apply",
                    ),
                    cwd=lane,
                )
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("Coordinator PROJECT/Backlog", blocked.stderr)
                self.assertEqual(lane_record.read_bytes(), before)
            finally:
                git(target, "worktree", "remove", "--force", str(lane), check=False)

    def test_contract_references_must_exist_in_live_requirements_and_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            original = json.loads(record_path.read_text(encoding="utf-8"))
            cases = (
                (
                    "requirement_ids",
                    ["REQ-DOES-NOT-EXIST"],
                    "Requirements IDs absent from the live approved Brief",
                ),
                (
                    "acceptance_ids",
                    ["AC-DOES-NOT-EXIST"],
                    "acceptance IDs absent from task acceptance",
                ),
            )
            for field, value, expected_message in cases:
                with self.subTest(field=field):
                    record = copy.deepcopy(original)
                    record["delivery_contract"][field] = value
                    record["contract_fingerprint"] = contract_fingerprint(record)
                    self._write_json(record_path, record)
                    before = record_path.read_bytes()
                    preflight = run(
                        workflow_command(
                            target, "workflow_check.py", "preflight", relative
                        ),
                        cwd=target,
                    )
                    self.assertNotEqual(preflight.returncode, 0)
                    self.assertIn(expected_message, preflight.stderr)
                    request_path = self._write_json(
                        root / f"invalid-{field}.json", self._generic_request(f"HD-BAD-{field}")
                    )
                    requested = run(
                        workflow_command(
                            target,
                            "workflow_state.py",
                            "request-decision",
                            relative,
                            "--decision-json",
                            str(request_path),
                            "--apply",
                        ),
                        cwd=target,
                    )
                    self.assertNotEqual(requested.returncode, 0)
                    self.assertIn(expected_message, requested.stderr)
                    self.assertEqual(record_path.read_bytes(), before)

    def test_missing_decision_ref_blocks_delivery_review_and_completion_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["delivery_contract"]["decision_refs"] = [
                {"decision_id": "HD-MISSING", "selected_option_id": "A"}
            ]
            record["contract_fingerprint"] = contract_fingerprint(record)
            self._write_json(record_path, record)
            review_path = self._write_json(
                root / "review.json", review_evidence_v1("v4-reviewer", record)
            )
            acceptance_path = self._write_json(
                root / "acceptance.json", self._acceptance(record)
            )
            before = record_path.read_bytes()
            blocked_commands = (
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    relative,
                    "--evidence-json",
                    str(root / "developer.json"),
                    "--delivery-commit",
                    "HEAD",
                    "--apply",
                ),
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-review",
                    relative,
                    "--review-json",
                    str(review_path),
                    "--apply",
                ),
                workflow_command(
                    target,
                    "workflow_state.py",
                    "complete-task",
                    relative,
                    "--acceptance-json",
                    str(acceptance_path),
                    "--apply",
                ),
            )
            for command in blocked_commands:
                blocked = run(command, cwd=target)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("references missing decision HD-MISSING", blocked.stderr)
                self.assertEqual(record_path.read_bytes(), before)

            request_path = self._write_json(
                root / "missing-decision.json", self._generic_request("HD-MISSING")
            )
            requested = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    relative,
                    "--decision-json",
                    str(request_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(requested.returncode, 0, requested.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            decision = current["decision_log"][0]
            resolution_path = self._write_json(
                root / "missing-resolution.json",
                {
                    "selected_option_id": "A",
                    "decided_by": "test-owner",
                    "decided_at": "2026-07-20T01:00:00Z",
                    "source": "user:decision-ref-owner",
                    "rationale": "Resolve the predeclared contract decision.",
                },
            )
            resolved = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    relative,
                    "--decision-id",
                    decision["id"],
                    "--expected-fingerprint",
                    decision["decision_fingerprint"],
                    "--resolution-json",
                    str(resolution_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            resealed = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    relative,
                    "--evidence-json",
                    str(root / "developer.json"),
                    "--delivery-commit",
                    "HEAD",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(resealed.returncode, 0, resealed.stderr)

    def test_review_and_completion_reject_non_independent_or_unpassed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            early_approval_path = self._write_json(
                root / "early-approval.json", self._approval(current)
            )
            before_approval = record_path.read_bytes()
            early_approval = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-approval",
                    relative,
                    "--approval-json",
                    str(early_approval_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(early_approval.returncode, 0)
            self.assertIn("Final gate requires task.status=completed", early_approval.stderr)
            self.assertEqual(record_path.read_bytes(), before_approval)
            same_agent_review_path = self._write_json(
                root / "same-agent-review.json",
                review_evidence_v1(current["developer"]["agent_id"], current),
            )
            before_review = record_path.read_bytes()
            same_agent_review = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-review",
                    relative,
                    "--review-json",
                    str(same_agent_review_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(same_agent_review.returncode, 0)
            self.assertIn("agent IDs must differ", same_agent_review.stderr)
            self.assertEqual(record_path.read_bytes(), before_review)

            invalid_reviews = (
                ("p1", "P0/P1 findings"),
                ("p2", "P2 finding"),
                ("p3", "P3 finding"),
            )
            for severity, expected_message in invalid_reviews:
                with self.subTest(review_severity=severity):
                    invalid_review = review_evidence_v1("v4-reviewer", current)
                    invalid_review["findings"][severity] = 1
                    invalid_review_path = self._write_json(
                        root / f"invalid-{severity}-review.json", invalid_review
                    )
                    before_review = record_path.read_bytes()
                    rejected_review = run(
                        workflow_command(
                            target,
                            "workflow_state.py",
                            "record-review",
                            relative,
                            "--review-json",
                            str(invalid_review_path),
                            "--apply",
                        ),
                        cwd=target,
                    )
                    self.assertNotEqual(rejected_review.returncode, 0)
                    self.assertIn(expected_message, rejected_review.stderr)
                    self.assertEqual(record_path.read_bytes(), before_review)

            review_path = self._write_json(
                root / "review.json", review_evidence_v1("v4-reviewer", current)
            )
            reviewed = run(
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            reviewed_record = json.loads(record_path.read_text(encoding="utf-8"))
            valid_acceptance = self._acceptance(reviewed_record)
            pending_payload = copy.deepcopy(valid_acceptance)
            for item in pending_payload["acceptance"]:
                item.update({"status": "pending", "evidence": []})
            empty_evidence_payload = copy.deepcopy(valid_acceptance)
            for item in empty_evidence_payload["acceptance"]:
                item["evidence"] = []
            same_agent_record = copy.deepcopy(reviewed_record)
            same_agent_record["review"]["agent_id"] = same_agent_record["developer"][
                "agent_id"
            ]
            p1_record = copy.deepcopy(reviewed_record)
            p1_record["review"]["findings"]["p1"] = 1
            p3_record = copy.deepcopy(reviewed_record)
            p3_record["review"]["findings"]["p3"] = 1
            incomplete_retrospective = copy.deepcopy(valid_acceptance)
            incomplete_retrospective["process_retrospective"]["completed"] = False
            proposed_rule = copy.deepcopy(valid_acceptance)
            proposed_rule["rule_proposals"] = [
                {
                    "id": "RP-001",
                    "reported_by": "developer",
                    "problem": "A repeated completion gap was observed.",
                    "evidence": ["test:completion-readiness"],
                    "recurrence": "repeated",
                    "target": "workflow checker",
                    "proposed_change": "Add a deterministic completion check.",
                    "status": "proposed",
                    "decision_by": None,
                    "decision_role": None,
                    "decision_at": None,
                    "decision_source": None,
                    "implemented_in": [],
                }
            ]
            completion_cases = (
                (
                    "pending",
                    reviewed_record,
                    pending_payload,
                    "must report status=passed",
                ),
                (
                    "empty-evidence",
                    reviewed_record,
                    empty_evidence_payload,
                    "must include non-empty evidence",
                ),
                (
                    "same-agent",
                    same_agent_record,
                    valid_acceptance,
                    "agent IDs must differ",
                ),
                (
                    "p1",
                    p1_record,
                    valid_acceptance,
                    "P0/P1 findings",
                ),
                (
                    "p3",
                    p3_record,
                    valid_acceptance,
                    "P3 finding",
                ),
                (
                    "retrospective",
                    reviewed_record,
                    incomplete_retrospective,
                    "retrospective must be completed",
                ),
                (
                    "rule-proposal",
                    reviewed_record,
                    proposed_rule,
                    "final disposition",
                ),
            )
            for name, record_source, payload_source, expected_message in completion_cases:
                with self.subTest(name=name):
                    record = copy.deepcopy(record_source)
                    payload = copy.deepcopy(payload_source)
                    self._write_json(record_path, record)
                    acceptance_path = self._write_json(
                        root / f"{name}-acceptance.json", payload
                    )
                    before = record_path.read_bytes()
                    completed = run(
                        workflow_command(
                            target,
                            "workflow_state.py",
                            "complete-task",
                            relative,
                            "--acceptance-json",
                            str(acceptance_path),
                            "--apply",
                        ),
                        cwd=target,
                    )
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(expected_message, completed.stderr)
                    self.assertEqual(record_path.read_bytes(), before)

            self._write_json(record_path, reviewed_record)
            acceptance_path = self._write_json(
                root / "valid-acceptance.json", valid_acceptance
            )
            completed = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "complete-task",
                    relative,
                    "--acceptance-json",
                    str(acceptance_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            completed_record = json.loads(record_path.read_text(encoding="utf-8"))
            completed_record["review"]["findings"]["p3"] = 1
            self._write_json(record_path, completed_record)
            gated = run(
                workflow_command(target, "workflow_check.py", "gate", relative),
                cwd=target,
            )
            self.assertNotEqual(gated.returncode, 0)
            self.assertIn("P3 finding", gated.stderr)

    def test_v4_snapshot_rejects_contract_mutation_with_old_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            developer_state = json.loads(record_path.read_text(encoding="utf-8"))

            def mutate_contract(record: dict) -> dict:
                changed = copy.deepcopy(record)
                changed["request"] += " The observable contract has changed."
                changed["contract_fingerprint"] = contract_fingerprint(changed)
                self.assertNotEqual(
                    snapshot_id(
                        changed, changed["verification"]["delivery_hash"]
                    ),
                    record["verification"]["snapshot_id"],
                )
                return changed

            stale_developer = mutate_contract(developer_state)
            self._write_json(record_path, stale_developer)
            stale_review_path = self._write_json(
                root / "stale-review.json",
                review_evidence_v1("v4-reviewer", stale_developer),
            )
            before_review = record_path.read_bytes()
            rejected_review = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-review",
                    relative,
                    "--review-json",
                    str(stale_review_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(rejected_review.returncode, 0)
            self.assertIn("canonical delivery", rejected_review.stderr)
            self.assertEqual(record_path.read_bytes(), before_review)

            self._write_json(record_path, developer_state)
            review_path = self._write_json(
                root / "review.json", review_evidence_v1("v4-reviewer", developer_state)
            )
            reviewed = run(
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            reviewed_state = json.loads(record_path.read_text(encoding="utf-8"))
            acceptance_path = self._write_json(
                root / "acceptance.json", self._acceptance(reviewed_state)
            )

            stale_review = mutate_contract(reviewed_state)
            self._write_json(record_path, stale_review)
            before_completion = record_path.read_bytes()
            rejected_completion = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "complete-task",
                    relative,
                    "--acceptance-json",
                    str(acceptance_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(rejected_completion.returncode, 0)
            self.assertIn("snapshot", rejected_completion.stderr)
            self.assertEqual(record_path.read_bytes(), before_completion)

            self._write_json(record_path, reviewed_state)
            completed = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "complete-task",
                    relative,
                    "--acceptance-json",
                    str(acceptance_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            completed_state = json.loads(record_path.read_text(encoding="utf-8"))
            stale_completed = mutate_contract(completed_state)
            self._write_json(record_path, stale_completed)
            before_verification = record_path.read_bytes()

            gated = run(
                workflow_command(target, "workflow_check.py", "gate", relative),
                cwd=target,
            )
            self.assertNotEqual(gated.returncode, 0)
            self.assertIn("snapshot", gated.stderr)
            self.assertEqual(record_path.read_bytes(), before_verification)

            marked = run(
                workflow_command(
                    target, "workflow_state.py", "mark-verified", relative, "--apply"
                ),
                cwd=target,
            )
            self.assertNotEqual(marked.returncode, 0)
            self.assertIn("snapshot", marked.stderr)
            self.assertEqual(record_path.read_bytes(), before_verification)

    def test_observation_methods_require_kind_specific_evidence_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, record_path, _ = self._prepare_delivery(root)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            cases = {
                "browser": (
                    "ui",
                    {
                        "kind": "ui",
                        "entrypoint_ref": "command:observe-core-slice",
                        "interaction_trace": ["Open the slice.", "Run the core action."],
                        "dom_snapshot_ref": "evidence:dom-snapshot",
                        "accessibility_snapshot_ref": "evidence:accessibility-snapshot",
                        "visual_evidence_refs": ["evidence:visual-snapshot"],
                    },
                ),
                "api": (
                    "api",
                    {
                        "kind": "api",
                        "entrypoint_ref": "command:observe-core-slice",
                        "request_ref": "evidence:api-request",
                        "response_ref": "evidence:api-response",
                        "status_code": 200,
                        "public_api_ref": "cli:observe-core-slice",
                        "public_api_digest": "f" * 64,
                    },
                ),
                "cli": (
                    "cli",
                    {
                        "kind": "cli",
                        "command_ref": "command:observe-core-slice",
                        "exit_code": 0,
                        "stdout_ref": "evidence:v4-m2-stdout",
                        "stderr_ref": None,
                    },
                ),
                "data": (
                    "data",
                    {
                        "kind": "data",
                        "entrypoint_ref": "command:observe-core-slice",
                        "query_ref": "evidence:data-query",
                        "result_ref": "evidence:data-result",
                        "schema_ref": "schema:core-result",
                        "schema_digest": "6" * 64,
                        "row_count": 1,
                    },
                ),
                "background": (
                    "background",
                    {
                        "kind": "background",
                        "entrypoint_ref": "command:observe-core-slice",
                        "trigger_ref": "evidence:background-trigger",
                        "completion_ref": "evidence:background-completion",
                        "state_ref": "evidence:background-state",
                    },
                ),
                "capability": (
                    "capability",
                    {
                        "kind": "capability",
                        "entrypoint_ref": "command:observe-core-slice",
                        "invocation_ref": "evidence:capability-invocation",
                        "result_ref": "evidence:capability-result",
                        "contract_ref": "cli:observe-core-slice",
                        "contract_digest": "f" * 64,
                    },
                ),
            }
            for method, (surface, method_evidence) in cases.items():
                with self.subTest(method=method):
                    record = copy.deepcopy(current)
                    record["delivery_contract"]["observation"]["method"] = method
                    record["contract_fingerprint"] = contract_fingerprint(record)
                    receipt = v4_observation_receipt(record)
                    receipt["normalized_result"]["surface"] = surface
                    receipt["normalized_result"]["method_evidence"] = method_evidence
                    if method == "data":
                        receipt["normalized_result"]["contracts"]["schemas"] = [
                            {"ref": "schema:core-result", "digest": "6" * 64}
                        ]
                    identity = validate_v4_observation_receipt(record, receipt)
                    self.assertEqual(len(identity), 64)

                    missing = copy.deepcopy(receipt)
                    del missing["normalized_result"]["method_evidence"]
                    with self.assertRaisesRegex(WorkflowDataError, "method_evidence"):
                        validate_v4_observation_receipt(record, missing)

    def test_architecture_and_risk_decisions_use_kind_specific_runtime_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            relative = record_relative(record_path, target)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            baseline = record["delivery_contract"]["architecture"]["baseline"]
            requests = [
                {
                    "id": "HD-ARCH",
                    "kind": "architecture_decision",
                    "affected_scope": ["current_slice", "module_boundary"],
                    "latest_decision_point": "before_review",
                    "current_delivery_independent": False,
                    "question": "Where should the new capability be owned?",
                    "options": [
                        {
                            "id": "A",
                            "label": "Existing module",
                            "impact": "Preserves the current dependency direction.",
                            "reversibility": "reversible",
                        },
                        {
                            "id": "B",
                            "label": "Shared module",
                            "impact": "Creates a new cross-module boundary.",
                            "reversibility": "costly",
                        },
                    ],
                    "recommendation": {
                        "option_id": "A",
                        "reason": "The existing module owns the relevant behavior.",
                    },
                    "architecture_context": {
                        "baseline": copy.deepcopy(baseline),
                        "guardrail_ids": ["ARCH-G-001"],
                        "declared_impact": "changes_guardrail",
                    },
                },
                {
                    "id": "HD-RISK",
                    "kind": "risk_acceptance",
                    "affected_scope": ["current_slice", "data_retention"],
                    "latest_decision_point": "before_review",
                    "current_delivery_independent": False,
                    "question": "May test records be retained for seven days?",
                    "options": [
                        {
                            "id": "A",
                            "label": "Seven days",
                            "impact": "Retains synthetic data for bounded debugging.",
                            "reversibility": "reversible",
                        },
                        {
                            "id": "B",
                            "label": "Delete immediately",
                            "impact": "Minimizes retention and debugging evidence.",
                            "reversibility": "reversible",
                        },
                    ],
                    "recommendation": {
                        "option_id": "B",
                        "reason": "The current acceptance does not need retained records.",
                    },
                    "risk_context": {
                        "risk": "Synthetic records remain after the test session.",
                        "consequence": "Retention increases unnecessary exposure.",
                        "mitigations": ["Use synthetic data.", "Delete the store at expiry."],
                        "reversibility": "reversible",
                        "expires_at": "2999-01-01T00:00:00Z",
                    },
                },
            ]
            original_contract = record["contract_fingerprint"]
            for request in requests:
                request_path = self._write_json(root / f"{request['id']}.json", request)
                requested = run(
                    workflow_command(
                        target, "workflow_state.py", "request-decision", relative,
                        "--decision-json", str(request_path), "--apply",
                    ),
                    cwd=target,
                )
                self.assertEqual(requested.returncode, 0, requested.stderr)
                current = json.loads(record_path.read_text(encoding="utf-8"))
                decision = next(
                    item for item in current["decision_log"] if item["id"] == request["id"]
                )
                self.assertTrue(decision["blocking"])
                self.assertEqual(
                    decision["requirements_baseline"], current["source"]["requirements_baseline"]
                )
                resolution_path = self._write_json(
                    root / f"{request['id']}-resolution.json",
                    {
                        "selected_option_id": "A",
                        "decided_by": "test-owner",
                        "decided_at": "2026-07-20T01:30:00Z",
                        "source": f"external-receipt:{request['id']}",
                        "rationale": "Record the exact bounded choice.",
                    },
                )
                resolved = run(
                    workflow_command(
                        target,
                        "workflow_state.py",
                        "record-decision",
                        relative,
                        "--decision-id",
                        decision["id"],
                        "--expected-fingerprint",
                        decision["decision_fingerprint"],
                        "--resolution-json",
                        str(resolution_path),
                        "--apply",
                    ),
                    cwd=target,
                )
                self.assertEqual(resolved.returncode, 0, resolved.stderr)
            final = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(final["contract_fingerprint"], original_contract)
            self.assertEqual(final["delivery_contract"]["decision_refs"], [])

            expired = copy.deepcopy(requests[1])
            expired["id"] = "HD-RISK-EXPIRED"
            expired["risk_context"]["expires_at"] = "2000-01-01T00:00:00Z"
            expired_path = self._write_json(root / "expired-risk.json", expired)
            rejected = run(
                workflow_command(
                    target, "workflow_state.py", "request-decision", relative,
                    "--decision-json", str(expired_path), "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("already expired", rejected.stderr)

    def test_architecture_sensitive_delivery_is_rejected_before_sealing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            sensitive_files = {
                "src/api/public.proto": "message PublicResult {}\n",
                "requirements.txt": "example-package==1.0\n",
                "tests/schema/public.proto": "message TestPublicResult {}\n",
                "tests/package-lock.json": "{}\n",
                "package.json": "{}\n",
                "go.mod": "module example.test/project\n",
                "Cargo.toml": "[package]\nname = \"example\"\nversion = \"0.1.0\"\n",
                "openapi.json": "{}\n",
                "Dockerfile": "FROM python:3.9\n",
                "Dockerfile.prod": "FROM python:3.9\n",
                ".github/workflows/ci.yml": "name: ci\n",
                "terraform/main.tf": "resource \"null_resource\" \"example\" {}\n",
                "helm/values.yaml": "replicaCount: 1\n",
                "Makefile": "all:\n\t@true\n",
                "docker-compose.yml": "services: {}\n",
                "docker-compose.override.yml": "services: {}\n",
                "Jenkinsfile": "pipeline {}\n",
                "azure-pipelines.yml": "trigger: []\n",
                "buildspec.yml": "version: 0.2\n",
                "cloudbuild.yaml": "steps: []\n",
                "Tiltfile": "\n",
                "WORKSPACE": "\n",
                "BUILD.bazel": "\n",
                ".dockerignore": ".git\n",
                "CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\n",
                "infra/network.tf": "resource \"null_resource\" \"network\" {}\n",
            }
            for relative, content in sensitive_files.items():
                candidate = target / relative
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text(content, encoding="utf-8")
            commit = commit_all(target, "unapproved public contract")
            before = record_path.read_bytes()
            rejected = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    record_relative(record_path, target),
                    "--evidence-json",
                    str(root / "developer.json"),
                    "--delivery-commit",
                    commit,
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("architecture-sensitive paths", rejected.stderr)
            for relative in sensitive_files:
                self.assertIn(relative, rejected.stderr)
            self.assertEqual(record_path.read_bytes(), before)

            for relative in sensitive_files:
                with self.subTest(continuation_path=relative):
                    with self.assertRaisesRegex(
                        WorkflowDataError, "cannot classify product-contract path"
                    ):
                        v4_continuation_path_class(relative)

    def test_mark_verified_binds_gate_to_the_observed_generation(self) -> None:
        for explicit_generation in (False, True):
            with self.subTest(explicit_generation=explicit_generation):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    target, record_path, _ = self._prepare_delivery(
                        root, checkpoint_mode="not_required"
                    )
                    relative = record_relative(record_path, target)
                    initial = json.loads(record_path.read_text(encoding="utf-8"))
                    request_path = self._write_json(
                        root / "concurrent-decision.json",
                        self._generic_request("HD-CONCURRENT-GATE"),
                    )
                    concurrent_state: list[bytes] = []

                    def gate_with_concurrent_decision(
                        paths: WorkflowPaths, record: str
                    ) -> int:
                        current = json.loads(record_path.read_text(encoding="utf-8"))
                        observed_generation = current["generation"]
                        requested = run(
                            workflow_command(
                                target, "workflow_state.py", "request-decision", record,
                                "--decision-json", str(request_path), "--apply",
                            ),
                            cwd=target,
                        )
                        self.assertEqual(requested.returncode, 0, requested.stderr)
                        concurrent_state.append(record_path.read_bytes())
                        return observed_generation

                    args = SimpleNamespace(
                        record=relative,
                        expected_generation=(
                            initial["generation"] + 1 if explicit_generation else None
                        ),
                        apply=True,
                    )
                    expected_error = (
                        "validated by gate" if explicit_generation else "Generation conflict"
                    )
                    with mock.patch.object(
                        workflow_state_module,
                        "_require_gate",
                        side_effect=gate_with_concurrent_decision,
                    ):
                        with self.assertRaisesRegex(
                            workflow_state_module.StateError, expected_error
                        ):
                            workflow_state_module.mark_verified(
                                WorkflowPaths.discover(target), args
                            )
                    self.assertEqual(record_path.read_bytes(), concurrent_state[0])
                    current = json.loads(record_path.read_text(encoding="utf-8"))
                    self.assertEqual(current["verification"]["status"], "pending")
                    self.assertEqual(
                        current["decision_log"][-1]["id"], "HD-CONCURRENT-GATE"
                    )

    def test_prepare_integration_binds_gate_to_the_observed_generation(self) -> None:
        for explicit_generation in (False, True):
            with self.subTest(explicit_generation=explicit_generation):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    target, record_path, _ = self._prepare_delivery(
                        root, checkpoint_mode="not_required"
                    )
                    relative = record_relative(record_path, target)
                    initial = json.loads(record_path.read_text(encoding="utf-8"))
                    initial["status"] = "completed"
                    initial["phase"] = "integration"
                    initial["verification"]["status"] = "passed"
                    self._write_json(record_path, initial)
                    concurrent_state: list[bytes] = []

                    def gate_with_concurrent_generation(
                        paths: WorkflowPaths, record: str
                    ) -> int:
                        current = json.loads(record_path.read_text(encoding="utf-8"))
                        observed_generation = current["generation"]
                        current["generation"] = observed_generation + 1
                        self._write_json(record_path, current)
                        concurrent_state.append(record_path.read_bytes())
                        return observed_generation

                    args = SimpleNamespace(
                        record=relative,
                        expected_generation=(
                            initial["generation"] + 1 if explicit_generation else None
                        ),
                        mode="remote_pr_ci",
                        apply=True,
                    )
                    expected_error = (
                        "validated by gate" if explicit_generation else "Generation conflict"
                    )
                    with mock.patch.object(
                        workflow_state_module,
                        "_require_gate",
                        side_effect=gate_with_concurrent_generation,
                    ):
                        with self.assertRaisesRegex(
                            workflow_state_module.StateError, expected_error
                        ):
                            workflow_state_module.prepare_integration(
                                WorkflowPaths.discover(target), args
                            )
                    self.assertEqual(record_path.read_bytes(), concurrent_state[0])
                    current = json.loads(record_path.read_text(encoding="utf-8"))
                    self.assertEqual(current["integration"]["status"], "not_ready")

    def test_required_checkpoint_deferred_stays_open_and_accepted_allows_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            decision = self._request_checkpoint(target, record_path, root)
            relative = record_relative(record_path, target)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            review_path = self._write_json(
                root / "review.json", review_evidence_v1("v4-reviewer", record)
            )
            approval_path = self._write_json(
                root / "approval.json", self._approval(record)
            )
            approval_before = record_path.read_bytes()
            approval_blocked = run(
                workflow_command(
                    target, "workflow_state.py", "record-approval", relative,
                    "--approval-json", str(approval_path), "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(approval_blocked.returncode, 0)
            self.assertIn("required product checkpoint", approval_blocked.stderr)
            self.assertEqual(record_path.read_bytes(), approval_before)
            blocked = run(
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("required product checkpoint", blocked.stderr)

            deferral = {
                "outcome": "deferred",
                "decided_by": "test-owner",
                "decided_at": "2026-07-20T01:00:00Z",
                "source": "external-receipt:deferral",
                "rationale": "Observe one more scenario.",
                "latest_observation_point": "Before independent Review.",
            }
            deferral_path = self._write_json(root / "deferred.json", deferral)
            deferred = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    relative,
                    "--decision-id",
                    decision["id"],
                    "--expected-fingerprint",
                    decision["decision_fingerprint"],
                    "--resolution-json",
                    str(deferral_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(deferred.returncode, 0, deferred.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(current["decision_log"][0]["status"], "open")
            self.assertIsNone(current["decision_log"][0]["resolution"])
            self.assertEqual(len(current["decision_log"][0]["deferrals"]), 1)

            still_blocked = run(
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(still_blocked.returncode, 0)
            self._record_checkpoint(target, record_path, root, decision, "accepted")
            accepted_state = json.loads(record_path.read_text(encoding="utf-8"))
            second_request_path = self._write_json(
                root / "second-checkpoint.json",
                v4_checkpoint_request(accepted_state, decision_id="HD-SECOND"),
            )
            before_second = record_path.read_bytes()
            second = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    relative,
                    "--decision-json",
                    str(second_request_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already has a product checkpoint", second.stderr)
            self.assertEqual(record_path.read_bytes(), before_second)

            for lifecycle in ("open", "deferred"):
                with self.subTest(second_checkpoint=lifecycle):
                    combined = copy.deepcopy(accepted_state)
                    second_decision = copy.deepcopy(combined["decision_log"][0])
                    second_decision.update(
                        {
                            "id": "HD-SECOND",
                            "question": "Does a second unresolved concern remain?",
                            "status": "open",
                            "resolution": None,
                            "deferrals": [],
                            "continuations": [],
                        }
                    )
                    if lifecycle == "deferred":
                        second_decision["deferrals"] = [
                            {
                                "deferred_by": "test-owner",
                                "deferred_at": "2026-07-20T01:05:00Z",
                                "source": "user:checkpoint-owner",
                                "reason": "One more scenario must be observed.",
                                "latest_observation_point": "Before independent Review.",
                            }
                        ]
                    second_decision["decision_fingerprint"] = decision_fingerprint(
                        second_decision
                    )
                    second_decision["decision_state_fingerprint"] = (
                        decision_state_fingerprint(second_decision)
                    )
                    combined["decision_log"].append(second_decision)
                    self._write_json(record_path, combined)
                    combined_review = self._write_json(
                        root / f"combined-{lifecycle}.json",
                        review_evidence_v1("v4-reviewer", combined),
                    )
                    before_combined = record_path.read_bytes()
                    rejected = run(
                        workflow_command(
                            target,
                            "workflow_state.py",
                            "record-review",
                            relative,
                            "--review-json",
                            str(combined_review),
                            "--apply",
                        ),
                        cwd=target,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn("open product checkpoint HD-SECOND", rejected.stderr)
                    self.assertEqual(record_path.read_bytes(), before_combined)

            self._write_json(record_path, accepted_state)
            reviewed = run(
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            self.assertEqual(
                json.loads(record_path.read_text(encoding="utf-8"))["review"]["status"],
                "pass",
            )

    def test_changes_requested_uses_atomic_full_reset_and_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            decision = self._request_checkpoint(target, record_path, root)
            relative = record_relative(record_path, target)
            resolution_path = self._write_json(
                root / "changes.json", self._checkpoint_resolution("changes_requested")
            )
            command = workflow_command(
                target,
                "workflow_state.py",
                "record-decision",
                relative,
                "--decision-id",
                decision["id"],
                "--expected-fingerprint",
                decision["decision_fingerprint"],
                "--resolution-json",
                str(resolution_path),
                "--apply",
            )
            before = record_path.read_bytes()
            injected = run(
                command,
                cwd=target,
                env_extra={"CODEX_WORKFLOW_TEST_FAIL_AT": "v4-reset-after-decision"},
            )
            self.assertNotEqual(injected.returncode, 0)
            self.assertEqual(record_path.read_bytes(), before)

            applied = run(command, cwd=target)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "in_progress")
            self.assertEqual(record["phase"], "developer")
            self.assertIsNone(record["verification"]["snapshot_id"])
            self.assertIsNone(record["developer"]["agent_id"])
            self.assertEqual(record["review"]["status"], "pending")
            self.assertIsNone(record["review"]["observation_equivalence"])
            self.assertEqual(record["human_approvals"], [])
            self.assertEqual(record["integration"]["status"], "not_ready")
            self.assertTrue(all(item["status"] == "pending" for item in record["acceptance"]))
            self.assertEqual(len(record["decision_log"]), 1)
            self.assertEqual(
                record["decision_log"][0]["resolution"]["outcome"], "changes_requested"
            )

    def test_show_before_dependency_allows_review_but_blocks_dependency_and_integration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="show_before_dependency"
            )
            decision = self._request_checkpoint(target, record_path, root)
            self.assertFalse(decision["blocking"])
            relative = record_relative(record_path, target)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            review_path = self._write_json(
                root / "review.json", review_evidence_v1("v4-reviewer", current)
            )
            reviewed = run(
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            acceptance_path = self._write_json(root / "acceptance.json", self._acceptance(current))
            completed = run(
                workflow_command(
                    target, "workflow_state.py", "complete-task", relative,
                    "--acceptance-json", str(acceptance_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            gate = run(
                workflow_command(target, "workflow_check.py", "gate", relative), cwd=target
            )
            self.assertEqual(gate.returncode, 0, gate.stderr)
            verified = run(
                workflow_command(
                    target, "workflow_state.py", "mark-verified", relative, "--apply"
                ),
                cwd=target,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            approval_path = self._write_json(
                root / "approval.json", self._approval(current)
            )
            approval_command = workflow_command(
                target, "workflow_state.py", "record-approval", relative,
                "--approval-json", str(approval_path), "--apply",
            )
            approval_before = record_path.read_bytes()
            approval_blocked = run(approval_command, cwd=target)
            self.assertNotEqual(approval_blocked.returncode, 0)
            self.assertIn("show_before_dependency", approval_blocked.stderr)
            self.assertEqual(record_path.read_bytes(), approval_before)
            blocked = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "prepare-integration",
                    relative,
                    "--mode",
                    "remote_pr_ci",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("show_before_dependency", blocked.stderr)

            self._record_checkpoint(target, record_path, root, decision, "accepted")
            approved = run(approval_command, cwd=target)
            self.assertEqual(approved.returncode, 0, approved.stderr)
            prepared = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "prepare-integration",
                    relative,
                    "--mode",
                    "remote_pr_ci",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            pending = record_path.read_bytes()
            new_request = self._write_json(root / "late.json", self._generic_request("HD-LATE"))
            late = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    relative,
                    "--decision-json",
                    str(new_request),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(late.returncode, 0)
            self.assertIn("leaves not_ready", late.stderr)
            self.assertEqual(record_path.read_bytes(), pending)

    def test_future_nonblocking_decision_upgrades_at_its_integration_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            request = self._generic_request("HD-FUTURE")
            request["affected_scope"] = ["future:next_slice"]
            request["latest_decision_point"] = "before_integration"
            request["current_delivery_independent"] = True
            request_path = self._write_json(root / "future.json", request)
            requested = run(
                workflow_command(
                    target, "workflow_state.py", "request-decision", relative,
                    "--decision-json", str(request_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(requested.returncode, 0, requested.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            decision = current["decision_log"][0]
            self.assertFalse(decision["blocking"])
            review_path = self._write_json(
                root / "review.json", review_evidence_v1("v4-reviewer", current)
            )
            reviewed = run(
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            acceptance_path = self._write_json(root / "acceptance.json", self._acceptance(current))
            completed = run(
                workflow_command(
                    target, "workflow_state.py", "complete-task", relative,
                    "--acceptance-json", str(acceptance_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            verified = run(
                workflow_command(
                    target, "workflow_state.py", "mark-verified", relative, "--apply"
                ),
                cwd=target,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            blocked = run(
                workflow_command(
                    target, "workflow_state.py", "prepare-integration", relative,
                    "--mode", "remote_pr_ci", "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("HD-FUTURE", blocked.stderr)
            resolution_path = self._write_json(
                root / "future-resolution.json",
                {
                    "selected_option_id": "A",
                    "decided_by": "test-owner",
                    "decided_at": "2026-07-20T03:00:00Z",
                    "source": "external-receipt:future-choice",
                    "rationale": "Resolve the future choice at its latest safe boundary.",
                },
            )
            resolved = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    relative,
                    "--decision-id",
                    decision["id"],
                    "--expected-fingerprint",
                    decision["decision_fingerprint"],
                    "--resolution-json",
                    str(resolution_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            prepared = run(
                workflow_command(
                    target, "workflow_state.py", "prepare-integration", relative,
                    "--mode", "remote_pr_ci", "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            pending = record_path.read_bytes()
            retry = run(
                workflow_command(
                    target, "workflow_state.py", "request-decision", relative,
                    "--decision-json", str(request_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertIn("STATE_NOOP", retry.stdout)
            self.assertEqual(record_path.read_bytes(), pending)

    def test_verified_integration_cannot_be_reopened_or_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            review_path = self._write_json(
                root / "review.json", review_evidence_v1("v4-reviewer", current)
            )
            reviewed = run(
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            acceptance_path = self._write_json(
                root / "acceptance.json", self._acceptance(current)
            )
            completed = run(
                workflow_command(
                    target, "workflow_state.py", "complete-task", relative,
                    "--acceptance-json", str(acceptance_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            verified = run(
                workflow_command(
                    target, "workflow_state.py", "mark-verified", relative, "--apply"
                ),
                cwd=target,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            prepare_remote = workflow_command(
                target,
                "workflow_state.py",
                "prepare-integration",
                relative,
                "--mode",
                "remote_pr_ci",
                "--apply",
            )
            prepared = run(prepare_remote, cwd=target)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            pending = record_path.read_bytes()

            retry = run(prepare_remote, cwd=target)
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertIn("STATE_NOOP", retry.stdout)
            self.assertEqual(record_path.read_bytes(), pending)

            conflict = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "prepare-integration",
                    relative,
                    "--mode",
                    "local_bootstrap",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("cannot change mode", conflict.stderr)
            self.assertEqual(record_path.read_bytes(), pending)

            pending_record = json.loads(pending)
            for integration_status in ("pending", "queued"):
                with self.subTest(integration_status=integration_status):
                    record = copy.deepcopy(pending_record)
                    record["integration"]["status"] = integration_status
                    if integration_status == "queued":
                        record["integration"].update(
                            {
                                "queue_id": str(uuid.uuid4()),
                                "queued_at": "2026-07-20T06:00:00Z",
                                "queue_priority": 100,
                            }
                        )
                    self._write_json(record_path, record)
                    before = record_path.read_bytes()
                    blocked = run(
                        workflow_command(
                            target,
                            "workflow_state.py",
                            "mark-verified",
                            relative,
                            "--apply",
                        ),
                        cwd=target,
                    )
                    self.assertNotEqual(blocked.returncode, 0)
                    self.assertIn("after integration leaves not_ready", blocked.stderr)
                    self.assertEqual(record_path.read_bytes(), before)

    def test_dependency_preflight_and_architecture_baseline_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, source_path, _ = self._prepare_delivery(
                root, checkpoint_mode="show_before_dependency"
            )
            source_decision = self._request_checkpoint(target, source_path, root)
            source = json.loads(source_path.read_text(encoding="utf-8"))
            dependent = basic_v4_record(
                source["base_commit"],
                task_id="MVP-DEPENDENT",
                requirements_baseline=source["source"]["requirements_baseline"],
                architecture_fingerprint=source["delivery_contract"]["architecture"][
                    "baseline"
                ]["fingerprint"],
                checkpoint_mode="not_required",
            )
            dependent["delivery_contract"]["dependency_refs"] = [
                {
                    "task_id": source["task_id"],
                    "closeout_ref": f"task:{source['task_id']}",
                    "closeout_fingerprint": "3" * 64,
                }
            ]
            dependent["lane"]["dependency_snapshot"]["dependencies"] = [source["task_id"]]
            dependent["contract_fingerprint"] = contract_fingerprint(dependent)
            dependent_path = write_record(target, dependent)
            dependent_relative = record_relative(dependent_path, target)
            blocked = run(
                workflow_command(
                    target, "workflow_check.py", "preflight", dependent_relative
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("show_before_dependency", blocked.stderr)

            self._record_checkpoint(target, source_path, root, source_decision, "accepted")
            ready = run(
                workflow_command(
                    target, "workflow_check.py", "preflight", dependent_relative
                ),
                cwd=target,
            )
            self.assertEqual(ready.returncode, 0, ready.stderr)

            upstream_request_path = self._write_json(
                root / "upstream-drift.json",
                self._generic_request("HD-UPSTREAM-DRIFT"),
            )
            opened = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    record_relative(source_path, target),
                    "--decision-json",
                    str(upstream_request_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(opened.returncode, 0, opened.stderr)
            dependent_before = dependent_path.read_bytes()
            direct = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    dependent_relative,
                    "--evidence-json",
                    str(root / "developer.json"),
                    "--delivery-commit",
                    "HEAD",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(direct.returncode, 0)
            self.assertIn("V4 dependency MVP-001 blocked", direct.stderr)
            self.assertIn("HD-UPSTREAM-DRIFT", direct.stderr)
            self.assertEqual(dependent_path.read_bytes(), dependent_before)

            decisions_path = target / ".codex-workflow/governance/DECISIONS.md"
            decisions_path.write_text(
                decisions_path.read_text(encoding="utf-8").replace(
                    "The slice must preserve the approved module dependency direction.",
                    "The architecture boundary drifted without new approval.",
                    1,
                ),
                encoding="utf-8",
            )
            drifted = run(
                workflow_command(
                    target, "workflow_check.py", "preflight", dependent_relative
                ),
                cwd=target,
            )
            self.assertNotEqual(drifted.returncode, 0)
            self.assertIn("architecture baseline approval fingerprint is stale", drifted.stderr)

    def test_stopped_and_pending_checkpoint_answers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            decision = self._request_checkpoint(target, record_path, root)
            self._record_checkpoint(target, record_path, root, decision, "stopped")
            stopped = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    record_relative(record_path, target),
                    "--evidence-json",
                    str(root / "developer.json"),
                    "--delivery-commit",
                    "HEAD",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(stopped.returncode, 0)
            self.assertIn("stopped this task", stopped.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertNotEqual(record["status"], "completed")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            decision = self._request_checkpoint(target, record_path, root)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["integration"].update(
                {
                    "status": "pending",
                    "mode": "remote_pr_ci",
                    "policy_id": "IP-001",
                    "source_ref": "refs/heads/main",
                    "target_ref": "refs/remotes/origin/main",
                }
            )
            self._write_json(record_path, record)
            before = record_path.read_bytes()
            resolution_path = self._write_json(
                root / "changes-pending.json",
                self._checkpoint_resolution("changes_requested"),
            )
            rejected = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    record_relative(record_path, target),
                    "--decision-id",
                    decision["id"],
                    "--expected-fingerprint",
                    decision["decision_fingerprint"],
                    "--resolution-json",
                    str(resolution_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("leaves not_ready", rejected.stderr)
            self.assertEqual(record_path.read_bytes(), before)
            queued_record = json.loads(record_path.read_text(encoding="utf-8"))
            queued_record["integration"].update(
                {
                    "status": "queued",
                    "queue_id": str(uuid.uuid4()),
                    "queued_at": "2026-07-20T05:30:00Z",
                    "queue_priority": 100,
                }
            )
            self._write_json(record_path, queued_record)
            queued_before = record_path.read_bytes()
            late_request = self._write_json(
                root / "queued-request.json", self._generic_request("HD-QUEUED")
            )
            queued = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "request-decision",
                    record_relative(record_path, target),
                    "--decision-json",
                    str(late_request),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(queued.returncode, 0)
            self.assertIn("leaves not_ready", queued.stderr)
            self.assertEqual(record_path.read_bytes(), queued_before)

    def test_requirements_continuation_uses_v4_reset_and_invalidates_old_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            checkpoint = self._request_checkpoint(target, record_path, root)
            self._record_checkpoint(target, record_path, root, checkpoint, "accepted")
            relative = record_relative(record_path, target)
            baseline_request_path = self._write_json(
                root / "baseline-decision.json", self._generic_request("HD-BASELINE")
            )
            requested = run(
                workflow_command(
                    target, "workflow_state.py", "request-decision", relative,
                    "--decision-json", str(baseline_request_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(requested.returncode, 0, requested.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            baseline_decision = next(
                item for item in record["decision_log"] if item["id"] == "HD-BASELINE"
            )
            baseline_resolution_path = self._write_json(
                root / "baseline-resolution.json",
                {
                    "selected_option_id": "A",
                    "decided_by": "test-owner",
                    "decided_at": "2026-07-20T03:30:00Z",
                    "source": "external-receipt:baseline-choice",
                    "rationale": "Bind the choice to the approved Requirements baseline.",
                },
            )
            baseline_resolved = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-decision",
                    relative,
                    "--decision-id",
                    baseline_decision["id"],
                    "--expected-fingerprint",
                    baseline_decision["decision_fingerprint"],
                    "--resolution-json",
                    str(baseline_resolution_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(baseline_resolved.returncode, 0, baseline_resolved.stderr)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            original_resolution = copy.deepcopy(record["decision_log"][0]["resolution"])
            record["source"]["type"] = "mvp_backlog"
            record["source"]["reference"] = record["task_id"]
            record["contract_fingerprint"] = contract_fingerprint(record)
            self._write_json(record_path, record)

            brief = target / ".codex-workflow/governance/requirements/REQ-001.md"
            revised_fingerprint = self._revise_requirements(brief)
            impact = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "requirements-impact",
                    str(brief.relative_to(target)),
                    "--json",
                ),
                cwd=target,
            )
            self.assertEqual(impact.returncode, 0, impact.stderr)
            analysis = json.loads(impact.stdout.splitlines()[0])
            applied = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "apply-requirements-impact",
                    str(brief.relative_to(target)),
                    "--expected-fingerprint",
                    revised_fingerprint,
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            stale_record = json.loads(record_path.read_text(encoding="utf-8"))
            stale_before = record_path.read_bytes()
            stale_review_path = self._write_json(
                root / "stale-baseline-review.json",
                review_evidence_v1("stale-baseline-reviewer", stale_record),
            )
            stale_request_path = self._write_json(
                root / "stale-baseline-request.json",
                self._generic_request("HD-STALE-MANAGED-BASELINE"),
            )
            for command in (
                workflow_command(
                    target, "workflow_state.py", "request-decision",
                    record_relative(record_path, target),
                    "--decision-json", str(stale_request_path), "--apply",
                ),
                workflow_command(
                    target, "workflow_state.py", "record-review",
                    record_relative(record_path, target),
                    "--review-json", str(stale_review_path), "--apply",
                ),
            ):
                blocked = run(command, cwd=target)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("task Requirements baseline is stale", blocked.stderr)
                self.assertEqual(record_path.read_bytes(), stale_before)
            decision_path = self._write_json(
                root / "requirements-continuation.json",
                {
                    "analysis_id": analysis["analysis_id"],
                    "decision": "continue",
                    "approved_by": "test-owner",
                    "approved_at": "2026-07-20T04:00:00Z",
                    "source": "user:v4-revision",
                    "rationale": "Continue after reviewing the exact revised baseline.",
                },
            )
            resolve_command = workflow_command(
                target,
                "workflow_state.py",
                "resolve-requirements-impact",
                record_relative(record_path, target),
                "--analysis-id",
                analysis["analysis_id"],
                "--decision-json",
                str(decision_path),
                "--apply",
            )

            invalidated_record = copy.deepcopy(stale_record)
            invalidated_record["verification"]["status"] = "invalidated"
            invalidated_record["integration"]["status"] = "invalidated"
            self._write_json(record_path, invalidated_record)
            invalidated = run(resolve_command, cwd=target)
            self.assertEqual(invalidated.returncode, 0, invalidated.stderr)
            recovered = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(recovered["integration"]["status"], "not_ready")
            self.assertEqual(recovered["verification"]["status"], "pending")
            self.assertIsNone(recovered["verification"]["snapshot_id"])

            for integration_status in ("pending", "queued"):
                with self.subTest(integration_status=integration_status):
                    blocked_record = copy.deepcopy(stale_record)
                    blocked_record["integration"]["status"] = integration_status
                    self._write_json(record_path, blocked_record)
                    blocked_before = record_path.read_bytes()
                    blocked = run(resolve_command, cwd=target)
                    self.assertNotEqual(blocked.returncode, 0)
                    self.assertIn("cannot resume in place", blocked.stderr)
                    self.assertEqual(record_path.read_bytes(), blocked_before)

            self._write_json(record_path, stale_record)
            resolved = run(resolve_command, cwd=target)
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            updated = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["source"]["requirements_baseline"]["revision"], 2)
            self.assertEqual(
                updated["source"]["requirements_baseline"]["approval_fingerprint"],
                revised_fingerprint,
            )
            self.assertEqual(updated["contract_fingerprint"], contract_fingerprint(updated))
            self.assertIsNone(updated["verification"]["snapshot_id"])
            self.assertEqual(updated["phase"], "developer")
            self.assertEqual(updated["decision_log"][0]["resolution"], original_resolution)
            gate = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "gate",
                    record_relative(record_path, target),
                ),
                cwd=target,
            )
            self.assertNotEqual(gate.returncode, 0)
            self.assertIn("required product checkpoint", gate.stderr)
            self.assertIn(
                "resolved decision HD-BASELINE has a stale Requirements baseline",
                gate.stderr,
            )

    def test_refresh_base_reset_reuses_v4_helper_and_preserves_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, delivery_commit = self._prepare_delivery(root)
            self._request_checkpoint(target, record_path, root)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            decisions = copy.deepcopy(record["decision_log"])
            record["human_approvals"] = [
                {
                    "kind": "local_bootstrap",
                    "task_id": record["task_id"],
                    "target_ref": "refs/heads/main",
                    "snapshot_id": record["verification"]["snapshot_id"],
                    "delivery_hash": record["verification"]["delivery_hash"],
                    "approved_by": "test-owner",
                    "approved_at": "2026-07-20T05:00:00Z",
                    "source": "user:test",
                }
            ]
            queued = copy.deepcopy(record)
            queued["integration"].update(
                {
                    "status": "queued",
                    "mode": "local_bootstrap",
                    "policy_id": "IP-001",
                    "source_ref": "refs/heads/main",
                    "target_ref": "refs/heads/main",
                    "queue_id": str(uuid.uuid4()),
                    "queued_at": "2026-07-20T05:00:00Z",
                    "queue_priority": 100,
                }
            )
            queued_before = copy.deepcopy(queued)
            with self.assertRaisesRegex(ValueError, "explicit abandon"):
                _reset_after_base_refresh(queued, "main", delivery_commit)
            self.assertEqual(queued, queued_before)

            _reset_after_base_refresh(record, "main", delivery_commit)
            self.assertEqual(record["base_commit"], delivery_commit)
            self.assertEqual(record["lane"]["base_commit"], delivery_commit)
            self.assertEqual(record["decision_log"], decisions)
            self.assertEqual(record["integration"]["status"], "not_ready")
            self.assertIsNone(record["verification"]["snapshot_id"])
            self.assertEqual(record["human_approvals"], [])
            self.assertEqual(record["review"]["status"], "pending")

    def test_refresh_base_command_rejects_v4_pending_and_queued(self) -> None:
        for integration_status in ("pending", "queued"):
            with self.subTest(integration_status=integration_status):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    target, record_path, _ = self._prepare_delivery(root)
                    relative = record_relative(record_path, target)
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    record["integration"].update(
                        {
                            "status": integration_status,
                            "mode": "local_bootstrap",
                            "policy_id": "IP-001",
                            "source_ref": "refs/heads/main",
                            "target_ref": "refs/heads/main",
                            "queue_id": (
                                str(uuid.uuid4())
                                if integration_status == "queued"
                                else None
                            ),
                            "queued_at": (
                                "2026-07-20T05:30:00Z"
                                if integration_status == "queued"
                                else None
                            ),
                            "queue_priority": (
                                100 if integration_status == "queued" else None
                            ),
                        }
                    )
                    self._write_json(record_path, record)
                    before = record_path.read_bytes()
                    lane = record["lane"]
                    payload = {
                        "mode": "local_worktree",
                        "worktree": str(target),
                        "record": relative,
                        **{
                            field: lane[field]
                            for field in (
                                "lane_id", "claim_id", "owner_generation", "branch",
                                "base_commit",
                            )
                        },
                    }
                    paths = WorkflowPaths.discover(target)
                    with ExitStack() as stack:
                        stack.enter_context(mock.patch.object(
                            workflow_lane_module,
                            "_coordinator_lock",
                            return_value=nullcontext(),
                        ))
                        stack.enter_context(mock.patch.object(
                            workflow_lane_module,
                            "_registry",
                            return_value=(root / "registry.json", payload),
                        ))
                        stack.enter_context(mock.patch.object(
                            workflow_lane_module, "_effective_status", return_value="active"
                        ))
                        stack.enter_context(mock.patch.object(
                            workflow_lane_module,
                            "_dirty_digest",
                            return_value=("clean", False),
                        ))
                        for apply in (False, True):
                            with self.subTest(
                                integration_status=integration_status, apply=apply
                            ):
                                args = SimpleNamespace(
                                    lane_id=lane["lane_id"],
                                    base="main",
                                    expected_generation=None,
                                    apply=apply,
                                )
                                with self.assertRaisesRegex(
                                    ValueError, "explicit abandon"
                                ):
                                    workflow_lane_module.refresh_base(paths, args)
                                self.assertEqual(record_path.read_bytes(), before)

    def test_queue_rejects_live_requirements_drift_before_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["status"] = "completed"
            record["phase"] = "integration"
            record["verification"]["status"] = "passed"
            record["integration"].update(
                {
                    "status": "pending",
                    "mode": "remote_pr_ci",
                    "policy_id": "IP-001",
                    "source_ref": "refs/heads/main",
                    "target_ref": "refs/remotes/origin/main",
                }
            )
            self._write_json(record_path, record)
            brief = target / ".codex-workflow/governance/requirements/REQ-001.md"
            self._revise_requirements(brief)
            before = record_path.read_bytes()
            lane = record["lane"]
            payload = {
                "worktree": str(target),
                "record": relative,
                "lane_id": lane["lane_id"],
                "task_id": record["task_id"],
                "claim_id": lane["claim_id"],
                "owner_generation": lane["owner_generation"],
            }
            paths = WorkflowPaths.discover(target)
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    workflow_lane_module,
                    "_coordinator_lock",
                    return_value=nullcontext(),
                ))
                stack.enter_context(mock.patch.object(
                    workflow_lane_module,
                    "_registry",
                    return_value=(root / "registry.json", payload),
                ))
                stack.enter_context(mock.patch.object(
                    workflow_lane_module, "_effective_status", return_value="verified"
                ))
                for apply in (False, True):
                    with self.subTest(apply=apply):
                        args = SimpleNamespace(
                            lane_id=lane["lane_id"],
                            priority=100,
                            apply=apply,
                        )
                        with self.assertRaisesRegex(
                            ValueError, "live Requirements Brief differs"
                        ):
                            workflow_lane_module.queue_lane(paths, args)
                        self.assertEqual(record_path.read_bytes(), before)
                        self.assertEqual(
                            list((paths.shared_runtime / "queue").glob("*.json")), []
                        )

    def test_queue_rechecks_live_architecture_before_projection_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["status"] = "completed"
            record["phase"] = "integration"
            record["verification"]["status"] = "passed"
            record["integration"].update(
                {
                    "status": "pending",
                    "mode": "remote_pr_ci",
                    "policy_id": "IP-001",
                    "source_ref": "refs/heads/main",
                    "target_ref": "refs/remotes/origin/main",
                }
            )
            self._write_json(record_path, record)
            decisions_path = target / ".codex-workflow/governance/DECISIONS.md"
            start = "<!-- CODEX_ARCHITECTURE_BASELINE_START -->"
            end = "<!-- CODEX_ARCHITECTURE_BASELINE_END -->"
            source = decisions_path.read_text(encoding="utf-8")
            baseline = json.loads(source.split(start, 1)[1].split(end, 1)[0])
            baseline["guardrails"][0]["statement"] += " Drifted after prepare."
            decisions_path.write_text(
                source.split(start, 1)[0]
                + start
                + "\n"
                + json.dumps(baseline, ensure_ascii=False, indent=2)
                + "\n"
                + end
                + source.split(end, 1)[1],
                encoding="utf-8",
            )
            before = record_path.read_bytes()
            lane = record["lane"]
            payload = {
                "worktree": str(target),
                "record": relative,
                "lane_id": lane["lane_id"],
                "task_id": record["task_id"],
                "claim_id": lane["claim_id"],
                "owner_generation": lane["owner_generation"],
            }
            paths = WorkflowPaths.discover(target)
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    workflow_lane_module,
                    "_coordinator_lock",
                    return_value=nullcontext(),
                ))
                stack.enter_context(mock.patch.object(
                    workflow_lane_module,
                    "_registry",
                    return_value=(root / "registry.json", payload),
                ))
                stack.enter_context(mock.patch.object(
                    workflow_lane_module, "_effective_status", return_value="verified"
                ))
                for apply in (False, True):
                    with self.subTest(apply=apply):
                        args = SimpleNamespace(
                            lane_id=lane["lane_id"], priority=100, apply=apply
                        )
                        with self.assertRaisesRegex(
                            ValueError, "architecture baseline approval fingerprint is stale"
                        ):
                            workflow_lane_module.queue_lane(paths, args)
                        self.assertEqual(record_path.read_bytes(), before)
                        self.assertEqual(
                            list((paths.shared_runtime / "queue").glob("*.json")), []
                        )

    def test_v4_queue_round_trip_and_consumption_rechecks_live_gates(self) -> None:
        def queue_project(root: Path, *, with_risk: bool = False):
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            relative = record_relative(record_path, target)
            if with_risk:
                risk_request = {
                    "id": "HD-QUEUE-RISK",
                    "kind": "risk_acceptance",
                    "affected_scope": ["current_slice", "operator_recovery"],
                    "latest_decision_point": "before_integration",
                    "current_delivery_independent": False,
                    "question": "May the bounded queue risk be accepted?",
                    "options": [
                        {
                            "id": "A",
                            "label": "Accept bounded risk",
                            "impact": "Proceed with the declared mitigations.",
                            "reversibility": "reversible",
                        },
                        {
                            "id": "B",
                            "label": "Stop",
                            "impact": "Do not enter the integration queue.",
                            "reversibility": "reversible",
                        },
                    ],
                    "recommendation": {
                        "option_id": "A",
                        "reason": "The risk is bounded and reversible.",
                    },
                    "risk_context": {
                        "risk": "A queued task may wait before integration.",
                        "consequence": "The evidence may need revalidation.",
                        "mitigations": ["Re-run integration preflight at consumption."],
                        "reversibility": "reversible",
                        "expires_at": "2099-12-31T00:00:00Z",
                    },
                }
                request_path = self._write_json(root / "risk-request.json", risk_request)
                requested = run(
                    workflow_command(
                        target,
                        "workflow_state.py",
                        "request-decision",
                        relative,
                        "--decision-json",
                        str(request_path),
                        "--apply",
                    ),
                    cwd=target,
                )
                self.assertEqual(requested.returncode, 0, requested.stderr)
                current = json.loads(record_path.read_text(encoding="utf-8"))
                risk = current["decision_log"][0]
                resolution_path = self._write_json(
                    root / "risk-resolution.json",
                    {
                        "selected_option_id": "A",
                        "decided_by": "test-owner",
                        "decided_at": "2026-07-20T01:00:00Z",
                        "source": "user:queue-risk-owner",
                        "rationale": "Accept only until the declared expiry.",
                    },
                )
                resolved = run(
                    workflow_command(
                        target,
                        "workflow_state.py",
                        "record-decision",
                        relative,
                        "--decision-id",
                        risk["id"],
                        "--expected-fingerprint",
                        risk["decision_fingerprint"],
                        "--resolution-json",
                        str(resolution_path),
                        "--apply",
                    ),
                    cwd=target,
                )
                self.assertEqual(resolved.returncode, 0, resolved.stderr)

            current = json.loads(record_path.read_text(encoding="utf-8"))
            review_path = self._write_json(
                root / "queue-review.json", review_evidence_v1("queue-reviewer", current)
            )
            reviewed = run(
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            acceptance_path = self._write_json(
                root / "queue-acceptance.json", self._acceptance(current)
            )
            completed = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "complete-task",
                    relative,
                    "--acceptance-json",
                    str(acceptance_path),
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            verified = run(
                workflow_command(
                    target, "workflow_state.py", "mark-verified", relative, "--apply"
                ),
                cwd=target,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            prepared = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "prepare-integration",
                    relative,
                    "--mode",
                    "remote_pr_ci",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)

            pending = json.loads(record_path.read_text(encoding="utf-8"))
            paths = WorkflowPaths.discover(target)
            paths.ensure_runtime()
            lane = pending["lane"]
            registry_path = paths.shared_runtime / "registry" / "lanes" / (
                lane["lane_id"] + ".json"
            )
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json(
                registry_path,
                {
                    "worktree": str(target),
                    "record": relative,
                    "lane_id": lane["lane_id"],
                    "task_id": pending["task_id"],
                    "claim_id": lane["claim_id"],
                    "owner_generation": lane["owner_generation"],
                    "state": "verified",
                    "expires_at": "2099-12-31T00:00:00Z",
                },
            )
            args = SimpleNamespace(lane_id=lane["lane_id"], priority=100, apply=True)
            with mock.patch.object(
                workflow_lane_module,
                "_coordinator_lock",
                return_value=nullcontext(),
            ):
                workflow_lane_module.queue_lane(paths, args)
            queued = json.loads(record_path.read_text(encoding="utf-8"))
            queue_path = paths.shared_runtime / "queue" / (
                queued["integration"]["queue_id"] + ".json"
            )
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            return target, record_path, paths, queue_path, queue

        with tempfile.TemporaryDirectory() as directory:
            target, _, paths, queue_path, queue = queue_project(Path(directory))
            registry, queued_snapshot = workflow_lane_module._queued_lane_snapshot(
                paths, queue_path, queue
            )
            self.assertEqual(registry["task_id"], queue["task_id"])
            self.assertEqual(
                set(queued_snapshot),
                {"snapshot_id", "delivery_commit", "delivery_hash", "changed_paths"},
            )
            with self.assertRaisesRegex(
                ValueError, "Integration queue actual changed paths overlap"
            ):
                workflow_lane_module._assert_queue_paths_available(
                    paths, queued_snapshot
                )
            self.assertTrue(target.is_dir())

        for drift in ("requirements", "architecture", "risk-expiry"):
            with self.subTest(queued_drift=drift):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    target, record_path, paths, queue_path, queue = queue_project(
                        root, with_risk=drift == "risk-expiry"
                    )
                    if drift == "requirements":
                        self._revise_requirements(
                            target / ".codex-workflow/governance/requirements/REQ-001.md"
                        )
                        expected = "live Requirements Brief differs"
                    elif drift == "architecture":
                        decisions_path = target / ".codex-workflow/governance/DECISIONS.md"
                        start = "<!-- CODEX_ARCHITECTURE_BASELINE_START -->"
                        end = "<!-- CODEX_ARCHITECTURE_BASELINE_END -->"
                        source = decisions_path.read_text(encoding="utf-8")
                        baseline = json.loads(source.split(start, 1)[1].split(end, 1)[0])
                        baseline["guardrails"][0]["statement"] += " Queue-time drift."
                        decisions_path.write_text(
                            source.split(start, 1)[0]
                            + start
                            + "\n"
                            + json.dumps(baseline, ensure_ascii=False, indent=2)
                            + "\n"
                            + end
                            + source.split(end, 1)[1],
                            encoding="utf-8",
                        )
                        expected = "architecture baseline approval fingerprint is stale"
                    else:
                        record = json.loads(record_path.read_text(encoding="utf-8"))
                        risk = record["decision_log"][0]
                        risk["risk_context"]["expires_at"] = "2000-01-01T00:00:00Z"
                        risk["decision_fingerprint"] = decision_fingerprint(risk)
                        risk["decision_state_fingerprint"] = decision_state_fingerprint(risk)
                        self._write_json(record_path, record)
                        expected = "risk acceptance HD-QUEUE-RISK is expired"
                    with self.assertRaisesRegex(ValueError, expected):
                        workflow_lane_module._queued_lane_snapshot(
                            paths, queue_path, queue
                        )

    def test_queue_binds_and_rechecks_dependency_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, source_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required"
            )
            source = json.loads(source_path.read_text(encoding="utf-8"))
            dependent = copy.deepcopy(source)
            dependent["task_id"] = "MVP-QUEUED-DEPENDENT"
            dependent["delivery_contract"]["focus_slice_id"] = dependent["task_id"]
            dependent["delivery_contract"]["dependency_refs"] = [
                {
                    "task_id": source["task_id"],
                    "closeout_ref": f"task:{source['task_id']}",
                    "closeout_fingerprint": "3" * 64,
                }
            ]
            dependent["contract_fingerprint"] = contract_fingerprint(dependent)
            dependent["status"] = "completed"
            dependent["phase"] = "integration"
            dependent["verification"]["status"] = "passed"
            dependent["lane"].update(
                {
                    "lane_id": "lane-MVP-QUEUED-DEPENDENT-single",
                    "claim_id": "00000000-0000-4000-8000-000000000010",
                }
            )
            dependent["integration"].update(
                {
                    "status": "pending",
                    "mode": "remote_pr_ci",
                    "policy_id": "IP-001",
                    "source_ref": "refs/heads/main",
                    "target_ref": "refs/remotes/origin/main",
                }
            )
            dependent_path = write_record(target, dependent)
            relative = record_relative(dependent_path, target)
            lane = dependent["lane"]
            payload = {
                "worktree": str(target),
                "record": relative,
                "lane_id": lane["lane_id"],
                "task_id": dependent["task_id"],
                "claim_id": lane["claim_id"],
                "owner_generation": lane["owner_generation"],
                "state": "verified",
            }
            paths = WorkflowPaths.discover(target)
            registry_path = paths.shared_runtime / "registry" / "lanes" / "queue-test.json"
            args = SimpleNamespace(
                lane_id=lane["lane_id"],
                priority=100,
                apply=True,
            )
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    workflow_lane_module,
                    "_coordinator_lock",
                    return_value=nullcontext(),
                ))
                stack.enter_context(mock.patch.object(
                    workflow_lane_module,
                    "_registry",
                    return_value=(registry_path, payload),
                ))
                stack.enter_context(mock.patch.object(
                    workflow_lane_module, "_effective_status", return_value="verified"
                ))
                stack.enter_context(mock.patch.object(
                    workflow_lane_module,
                    "_require_v4_integration_preflight",
                    return_value=None,
                ))
                workflow_lane_module.queue_lane(paths, args)

            queued = json.loads(dependent_path.read_text(encoding="utf-8"))
            queue_path = paths.shared_runtime / "queue" / (
                queued["integration"]["queue_id"] + ".json"
            )
            queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(
                queue_payload["dependency_snapshot"][0]["task_id"], source["task_id"]
            )
            source["generation"] += 1
            self._write_json(source_path, source)
            with self.assertRaisesRegex(ValueError, "dependency snapshot is stale"):
                workflow_state_module._queue_entry_for_record(paths, queued)

    def test_strict_observation_continuation_is_atomic_and_preserves_human_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, source_commit = self._prepare_delivery(root)
            decision = self._request_checkpoint(target, record_path, root)
            self._record_checkpoint(target, record_path, root, decision, "accepted")
            relative = record_relative(record_path, target)
            first = json.loads(record_path.read_text(encoding="utf-8"))
            source_resolution = copy.deepcopy(first["decision_log"][0]["resolution"])
            changes_review = review_evidence_v1("v4-reviewer-one", first)
            changes_review["status"] = "changes_requested"
            changes_review["claim_assessments"][0]["assessment"] = "narrowed"
            changes_review["claim_assessments"][0]["notes"] = "A technical test adjustment is required."
            changes_path = self._write_json(root / "review-changes.json", changes_review)
            changed = run(
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(changes_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(changed.returncode, 0, changed.stderr)

            revised = json.loads(record_path.read_text(encoding="utf-8"))
            revised["planning"]["steps"].append("Repeat one technical-only regression check.")
            revised["contract_fingerprint"] = contract_fingerprint(revised)
            self._write_json(record_path, revised)
            technical = target / "tests/technical.txt"
            technical.parent.mkdir(parents=True, exist_ok=True)
            technical.write_text("technical-only correction\n", encoding="utf-8")
            target_commit = commit_all(target, "technical correction")
            developer = self._write_json(
                root / "developer-two.json", developer_evidence_v1("v4-developer-two")
            )
            sealed = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "record-developer",
                    relative,
                    "--evidence-json",
                    str(developer),
                    "--delivery-commit",
                    target_commit,
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(sealed.returncode, 0, sealed.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))

            target_receipt = v4_observation_receipt(current)
            target_receipt["evidence_refs"] = ["evidence:second-run-transcript"]
            target_receipt["normalized_result"]["method_evidence"][
                "stdout_ref"
            ] = "evidence:second-run-stdout"
            target_observation = observation_fingerprint(current, target_receipt)
            target_receipt_identity = observation_receipt_fingerprint(target_receipt)
            source_decision = current["decision_log"][0]
            self.assertEqual(
                target_observation, source_decision["observation_fingerprint"]
            )
            source_material = source_decision["contract_material"]
            target_material = contract_fingerprint_material(current)
            changed_categories = sorted(
                key for key in target_material if source_material.get(key) != target_material.get(key)
            )
            changed_paths = canonical_delivery(WorkflowPaths.discover(target), source_commit, target_commit)[
                "changed_paths"
            ]
            review = review_evidence_v1("v4-reviewer-two", current)
            review["observation_equivalence"] = {
                "continuation_version": 1,
                "source_decision_id": source_decision["id"],
                "source_decision_fingerprint": source_decision["decision_fingerprint"],
                "target_receipt": target_receipt,
                "target_receipt_fingerprint": target_receipt_identity,
                "target_observation_fingerprint": target_observation,
                "contract_diff": {
                    "source_fingerprint": source_decision["binding"]["contract_fingerprint"],
                    "target_fingerprint": current["contract_fingerprint"],
                    "changed_categories": changed_categories,
                },
                "changed_paths": changed_paths,
                "path_classes": [
                    {"path": path, "classification": "test"} for path in changed_paths
                ],
                "replay": {"recipe_replayed": True, "normalized_result_matches": True},
                "reviewer_receipt": {
                    "reviewer_id": "v4-reviewer-two",
                    "snapshot_id": current["verification"]["snapshot_id"],
                    "assessment": "equivalent",
                    "user_behavior": "unchanged",
                    "data_contract": "unchanged",
                    "public_interface": "unchanged",
                    "dependencies": "unchanged",
                    "architecture": "unchanged",
                    "source": "external-receipt:review-equivalence",
                },
            }
            review_path = self._write_json(root / "review-equivalent.json", review)
            command = workflow_command(
                target, "workflow_state.py", "record-review", relative,
                "--review-json", str(review_path), "--apply",
            )
            before = record_path.read_bytes()
            injected = run(
                command,
                cwd=target,
                env_extra={"CODEX_WORKFLOW_TEST_FAIL_AT": "v4-review-before-continuation"},
            )
            self.assertNotEqual(injected.returncode, 0)
            self.assertEqual(record_path.read_bytes(), before)

            applied = run(command, cwd=target)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            final = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(final["review"]["status"], "pass")
            self.assertEqual(final["review"]["observation_equivalence"]["assessment"], "equivalent")
            self.assertEqual(len(final["decision_log"][0]["continuations"]), 1)
            self.assertEqual(final["decision_log"][0]["resolution"], source_resolution)
            self.assertEqual(
                final["decision_log"][0]["decision_state_fingerprint"],
                decision_state_fingerprint(final["decision_log"][0]),
            )
            acceptance_path = self._write_json(
                root / "continuation-acceptance.json", self._acceptance(final)
            )
            completed = run(
                workflow_command(
                    target, "workflow_state.py", "complete-task", relative,
                    "--acceptance-json", str(acceptance_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            gated = run(
                workflow_command(target, "workflow_check.py", "gate", relative),
                cwd=target,
            )
            self.assertEqual(gated.returncode, 0, gated.stderr)
            valid_final = json.loads(record_path.read_text(encoding="utf-8"))
            tamper_cases = {
                "receipt-ref": lambda item: item["decision_log"][0]["continuations"][0][
                    "target_receipt"
                ]["normalized_result"]["method_evidence"].update(
                    {"stdout_ref": "evidence:forged-after-review"}
                ),
                "receipt-fingerprint": lambda item: item["decision_log"][0][
                    "continuations"
                ][0].update({"target_receipt_fingerprint": "0" * 64}),
                "review-binding": lambda item: item["review"][
                    "observation_equivalence"
                ].update({"target_receipt_fingerprint": "0" * 64}),
                "changed-paths": lambda item: (
                    item["decision_log"][0]["continuations"][0].update(
                        {"changed_paths": [], "path_classes": []}
                    )
                ),
            }
            for name, mutate in tamper_cases.items():
                with self.subTest(continuation_tamper=name):
                    tampered_record = copy.deepcopy(valid_final)
                    mutate(tampered_record)
                    decision = tampered_record["decision_log"][0]
                    decision["decision_state_fingerprint"] = (
                        decision_state_fingerprint(decision)
                    )
                    self._write_json(record_path, tampered_record)
                    tampered = run(
                        workflow_command(
                            target, "workflow_check.py", "preflight", relative
                        ),
                        cwd=target,
                    )
                    self.assertNotEqual(tampered.returncode, 0)

    def test_exploratory_prepare_integration_is_always_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(
                root, checkpoint_mode="not_required", execution_mode="exploratory"
            )
            relative = record_relative(record_path, target)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            review_path = self._write_json(
                root / "review.json", review_evidence_v1("v4-reviewer", current)
            )
            reviewed = run(
                workflow_command(
                    target, "workflow_state.py", "record-review", relative,
                    "--review-json", str(review_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            acceptance_path = self._write_json(root / "acceptance.json", self._acceptance(current))
            completed = run(
                workflow_command(
                    target, "workflow_state.py", "complete-task", relative,
                    "--acceptance-json", str(acceptance_path), "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            verified = run(
                workflow_command(
                    target, "workflow_state.py", "mark-verified", relative, "--apply"
                ),
                cwd=target,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            current = json.loads(record_path.read_text(encoding="utf-8"))
            approval_path = self._write_json(
                root / "exploratory-approval.json", self._approval(current)
            )
            approval_before = record_path.read_bytes()
            approval_blocked = run(
                workflow_command(
                    target, "workflow_state.py", "record-approval", relative,
                    "--approval-json", str(approval_path), "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(approval_blocked.returncode, 0)
            self.assertIn("exploratory", approval_blocked.stderr)
            self.assertEqual(record_path.read_bytes(), approval_before)
            blocked = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "prepare-integration",
                    relative,
                    "--mode",
                    "remote_pr_ci",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("exploratory", blocked.stderr)

    def test_abandon_cleans_queue_and_claim_without_forging_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, record_path, _ = self._prepare_delivery(root)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            lane_id = record["lane"]["lane_id"]
            claim_id = record["lane"]["claim_id"]
            owner_generation = record["lane"]["owner_generation"]
            queue_id = str(uuid.uuid4())
            record["integration"].update(
                {
                    "status": "queued",
                    "mode": "remote_pr_ci",
                    "policy_id": "IP-001",
                    "source_ref": "refs/heads/main",
                    "target_ref": "refs/remotes/origin/main",
                    "queue_id": queue_id,
                    "queued_at": "2026-07-20T06:00:00Z",
                    "queue_priority": 100,
                }
            )
            self._write_json(record_path, record)
            runtime = target / ".git/codex-workflow-v3"
            registry_path = runtime / "registry/lanes" / f"{lane_id}.json"
            claim_path = runtime / "claims" / f"{record['task_id']}.json"
            queue_path = runtime / "queue" / f"{queue_id}.json"
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            claim_path.parent.mkdir(parents=True, exist_ok=True)
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            identity = {
                "lane_id": lane_id,
                "task_id": record["task_id"],
                "claim_id": claim_id,
                "owner_generation": owner_generation,
                "worktree": str(target),
                "branch": "main",
                "resource_keys": [],
            }
            self._write_json(registry_path, identity)
            self._write_json(claim_path, identity)
            self._write_json(queue_path, {**identity, "queue_id": queue_id})
            released = run(
                workflow_command(
                    target,
                    "workflow_lane.py",
                    "release",
                    lane_id,
                    "--abandon",
                    "--apply",
                ),
                cwd=target,
            )
            self.assertEqual(released.returncode, 0, released.stderr)
            self.assertFalse(registry_path.exists())
            self.assertFalse(claim_path.exists())
            self.assertFalse(queue_path.exists())
            preserved = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertNotEqual(preserved["status"], "completed")
            self.assertEqual(preserved["integration"]["status"], "queued")
            self.assertEqual(preserved["integration"]["queue_id"], queue_id)
if __name__ == "__main__":
    unittest.main()
