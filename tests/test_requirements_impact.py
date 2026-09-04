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
    git,
    install_project,
    record_relative,
    requirements_fingerprint,
    run,
    workflow_command,
    write_record,
)


START = "<!-- CODEX_REQUIREMENTS_JSON_START -->"
END = "<!-- CODEX_REQUIREMENTS_JSON_END -->"


class RequirementsImpactTests(unittest.TestCase):
    def _assert_ok(self, result) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)

    def _rewrite_brief(
        self,
        brief: Path,
        *,
        brief_id: str | None = None,
        revision: int | None = None,
    ) -> str:
        source = brief.read_text(encoding="utf-8")
        metadata = json.loads(source.split(START, 1)[1].split(END, 1)[0])
        if brief_id is not None:
            metadata["brief_id"] = brief_id
        if revision is not None:
            metadata["revision"] = revision
        metadata["requirements"]["capabilities"][0]["observable_result"] = "The revised gate passes"
        metadata["requirements"]["calibration_rounds"].append(
            {
                "id": "CAL-002",
                "result": "confirmed",
                "changed_requirement_ids": ["REQ-F-001"],
                "source": "user:revised-test",
            }
        )
        markdown = source.split(END, 1)[1]
        fingerprint = requirements_fingerprint(metadata, markdown)
        metadata["approval"]["approved_fingerprint"] = fingerprint
        brief.write_text(
            START
            + "\n"
            + json.dumps(metadata, ensure_ascii=False, indent=2)
            + "\n"
            + END
            + markdown,
            encoding="utf-8",
        )
        return fingerprint

    def test_apply_requirements_impact_dry_run_does_not_create_runtime_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            brief, _original = approved_requirements(target)
            create_baseline(target)
            revised = self._rewrite_brief(brief, revision=2)
            runtime = target / ".git" / "codex-workflow-v4"
            before = (
                {path.relative_to(runtime).as_posix() for path in runtime.rglob("*")}
                if runtime.exists()
                else set()
            )
            preview = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "apply-requirements-impact",
                    str(brief.relative_to(target)),
                    "--expected-fingerprint",
                    revised,
                ),
                cwd=target,
            )
            self._assert_ok(preview)
            self.assertIn('"apply": false', preview.stdout)
            self.assertFalse(
                (target / ".codex-workflow/state/requirements-impacts/REQ-001-r2.json").exists()
            )
            after = (
                {path.relative_to(runtime).as_posix() for path in runtime.rglob("*")}
                if runtime.exists()
                else set()
            )
            self.assertEqual(after, before)

    def test_revision_impact_blocks_unstarted_work_and_requires_human_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            brief, original_fingerprint = approved_requirements(target)
            base = create_baseline(target)
            record = basic_v3_record(
                base,
                source_type="mvp_backlog",
                requirements_baseline={
                    "brief_id": "REQ-001",
                    "revision": 1,
                    "approval_fingerprint": original_fingerprint,
                },
            )
            record["status"] = "in_progress"
            record["phase"] = "developer"
            record_path = write_record(target, record)
            revised_fingerprint = self._rewrite_brief(brief, revision=2)
            brief_relative = str(brief.relative_to(target))

            manual = run(workflow_command(target, "workflow_check.py", "manual"), cwd=target)
            self.assertNotEqual(manual.returncode, 0)
            self.assertIn("differs from the PROJECT/Backlog baseline", manual.stderr)
            stale_status = run(workflow_command(target, "workflow_check.py", "status"), cwd=target)
            self.assertNotEqual(stale_status.returncode, 0)
            self.assertIn("Requirements Brief differs from the managed baseline", stale_status.stderr)

            impact = run(
                workflow_command(target, "workflow_check.py", "requirements-impact", brief_relative, "--json"),
                cwd=target,
            )
            self._assert_ok(impact)
            analysis = json.loads(impact.stdout.splitlines()[0])
            self.assertEqual(analysis["status"], "ready")
            self.assertEqual([item["task_id"] for item in analysis["active_tasks"]], ["MVP-001"])
            self.assertEqual(analysis["active_tasks"][0]["action"], "human_decision_required")

            applied = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "apply-requirements-impact",
                    brief_relative,
                    "--expected-fingerprint",
                    revised_fingerprint,
                    "--apply",
                ),
                cwd=target,
            )
            self._assert_ok(applied)
            report = target / ".codex-workflow/state/requirements-impacts/REQ-001-r2.json"
            self.assertTrue(report.is_file())
            saved_report = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(saved_report["status"], "applied")
            self.assertEqual(saved_report["analysis_id"], analysis["analysis_id"])
            backlog = (target / ".codex-workflow/state/MVP_BACKLOG.md").read_text(encoding="utf-8")
            self.assertIn("| draft | none |", backlog)

            stale = run(
                workflow_command(target, "workflow_check.py", "preflight", record_relative(record_path, target)),
                cwd=target,
            )
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("Task requirements baseline field revision is stale", stale.stderr)

            decision = target.parent / "continue.json"
            decision.write_text(
                json.dumps(
                    {
                        "analysis_id": analysis["analysis_id"],
                        "decision": "continue",
                        "approved_by": "test-user",
                        "approved_at": "2026-07-12T00:00:00Z",
                        "source": "user:test",
                        "rationale": "The task contract remains valid after reviewing REQ-F-001.",
                    }
                ),
                encoding="utf-8",
            )
            resolved = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "resolve-requirements-impact",
                    record_relative(record_path, target),
                    "--analysis-id",
                    analysis["analysis_id"],
                    "--decision-json",
                    str(decision),
                    "--apply",
                ),
                cwd=target,
            )
            self._assert_ok(resolved)
            updated = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["source"]["requirements_baseline"]["revision"], 2)
            self.assertEqual(updated["requirements_impact"]["decision"], "continue")
            self.assertEqual(updated["verification"]["status"], "pending")
            status = run(workflow_command(target, "workflow_check.py", "status"), cwd=target)
            self._assert_ok(status)

    def test_superseding_brief_uses_the_previous_brief_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            original, _ = approved_requirements(target)
            create_baseline(target)
            replacement = original.with_name("REQ-002.md")
            replacement.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
            self._rewrite_brief(replacement, brief_id="REQ-002", revision=1)

            impact = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "requirements-impact",
                    str(replacement.relative_to(target)),
                    "--json",
                ),
                cwd=target,
            )
            self._assert_ok(impact)
            analysis = json.loads(impact.stdout.splitlines()[0])
            self.assertEqual(analysis["status"], "ready")
            self.assertEqual(analysis["previous_baseline"]["brief_id"], "REQ-001")
            self.assertEqual(analysis["current_baseline"]["brief_id"], "REQ-002")
            self.assertIn("$brief_id", [item["id"] for item in analysis["changes"]])

    def test_impact_reads_the_live_local_lane_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            lane = Path(directory) / "lane"
            self._assert_ok(install_project(target, parallel_mode="local_worktree"))
            brief, original_fingerprint = approved_requirements(target)
            baseline = create_baseline(target)
            record = basic_v3_record(
                baseline,
                source_type="mvp_backlog",
                requirements_baseline={
                    "brief_id": "REQ-001",
                    "revision": 1,
                    "approval_fingerprint": original_fingerprint,
                },
            )
            record_path = write_record(target, record)
            main_with_record = commit_all(target, "authorized task")
            git(target, "worktree", "add", "-b", "codex/task/MVP-001", str(lane), main_with_record)
            try:
                lane_record = lane / record_path.relative_to(target)
                active = json.loads(lane_record.read_text(encoding="utf-8"))
                active["status"] = "in_progress"
                active["phase"] = "developer"
                lane_record.write_text(json.dumps(active, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                common = git(target, "rev-parse", "--git-common-dir").stdout.strip()
                common_path = (target / common).resolve() if not Path(common).is_absolute() else Path(common)
                registry_path = common_path / "codex-workflow-v4/registry/lanes/lane-MVP-001.json"
                registry_path.parent.mkdir(parents=True, exist_ok=True)
                registry_path.write_text(
                    json.dumps(
                        {
                            "task_id": "MVP-001",
                            "lane_id": "lane-MVP-001",
                            "worktree": str(lane),
                            "record": record_relative(record_path, target),
                        }
                    ),
                    encoding="utf-8",
                )
                self._rewrite_brief(brief, revision=2)
                result = run(
                    workflow_command(
                        target,
                        "workflow_check.py",
                        "requirements-impact",
                        str(brief.relative_to(target)),
                        "--json",
                    ),
                    cwd=target,
                )
                self._assert_ok(result)
                analysis = json.loads(result.stdout.splitlines()[0])
                self.assertEqual(analysis["active_tasks"][0]["task_id"], "MVP-001")
                self.assertEqual(analysis["active_tasks"][0]["runtime_lane_id"], "lane-MVP-001")
                synced = run(
                    workflow_command(target, "workflow_state.py", "sync-status", "--apply"),
                    cwd=target,
                )
                self._assert_ok(synced)
                status_source = (target / ".codex-workflow/state/STATUS.md").read_text(encoding="utf-8")
                snapshot = json.loads(status_source.split("<!-- CODEX_WORKFLOW_STATUS_JSON_START -->", 1)[1].split("<!-- CODEX_WORKFLOW_STATUS_JSON_END -->", 1)[0])
                self.assertEqual(snapshot["task_records"][0]["runtime_lane_id"], "lane-MVP-001")
                self.assertEqual(snapshot["task_records"][0]["status"], "in_progress")
            finally:
                git(target, "worktree", "remove", "--force", str(lane), check=False)

    def test_status_snapshot_is_current_after_sync_and_state_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self._assert_ok(install_project(target))
            base = create_baseline(target)
            initial = run(workflow_command(target, "workflow_check.py", "status"), cwd=target)
            self._assert_ok(initial)

            backlog_path = target / ".codex-workflow/state/MVP_BACKLOG.md"
            backlog_path.write_text(
                backlog_path.read_text(encoding="utf-8").replace("| draft | none |", "| ready | none |"),
                encoding="utf-8",
            )
            stale = run(workflow_command(target, "workflow_check.py", "status"), cwd=target)
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("Workflow status document is stale", stale.stderr)
            synced = run(
                workflow_command(target, "workflow_state.py", "sync-status", "--apply"),
                cwd=target,
            )
            self._assert_ok(synced)

            record_path = write_record(target, basic_v3_record(base))
            mutated = run(
                workflow_command(
                    target,
                    "workflow_state.py",
                    "invalidate-integration",
                    record_relative(record_path, target),
                    "--reason",
                    "status-test",
                    "--apply",
                ),
                cwd=target,
            )
            self._assert_ok(mutated)
            current = run(workflow_command(target, "workflow_check.py", "status"), cwd=target)
            self._assert_ok(current)


if __name__ == "__main__":
    unittest.main()
