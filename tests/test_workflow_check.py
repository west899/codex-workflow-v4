from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import basic_record, create_baseline, install_project, run, write_task


class WorkflowCheckTests(unittest.TestCase):
    def install(self, temporary: str) -> Path:
        target = Path(temporary) / "project"
        result = install_project(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        return target

    def check(self, target: Path, *arguments: str):
        return run(
            ["python3", "scripts/workflow_check.py", *arguments],
            cwd=target,
        )

    def test_unknown_mode_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = self.install(temporary)
            result = self.check(target, "gtea")
            self.assertEqual(result.returncode, 2)
            self.assertIn("unknown mode", result.stderr)

    def test_secret_scan_respects_gitignore_but_scans_tracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = self.install(temporary)
            env_file = target / ".env"
            env_file.write_text("OPENAI_API_KEY=sk-proj-AAAAAAAAAAAAAAAAAAAAAAAA\n")

            ignored = self.check(target, "manual")
            self.assertEqual(ignored.returncode, 0, ignored.stderr)

            staged = run(["git", "add", "-f", ".env"], cwd=target)
            self.assertEqual(staged.returncode, 0, staged.stderr)
            tracked = self.check(target, "manual")
            self.assertEqual(tracked.returncode, 1)
            self.assertIn("Possible OpenAI API key in .env", tracked.stderr)

    def test_malformed_task_record_fails_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = self.install(temporary)
            base = create_baseline(target)
            record = basic_record(base)
            record["acceptance"] = [None]
            task = write_task(target, record)

            result = self.check(target, "preflight", str(task.relative_to(target)))
            self.assertEqual(result.returncode, 1)
            self.assertIn("acceptance[0] must be an object", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_empty_source_and_invalid_hooks_fail_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = self.install(temporary)
            base = create_baseline(target)
            record = basic_record(base)
            record["source"] = {}
            task = write_task(target, record)
            (target / ".codex" / "hooks.json").write_text('{"hooks": []}\n')

            result = self.check(target, "preflight", str(task.relative_to(target)))
            self.assertEqual(result.returncode, 1)
            self.assertIn("source.type", result.stderr)
            self.assertIn("hooks must be an object", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_backlog_uses_status_column_and_custom_skill_is_non_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = self.install(temporary)
            base = create_baseline(target)
            backlog = target / "docs" / "MVP_BACKLOG.md"
            backlog.write_text(
                "# MVP Backlog\n\n"
                "> 状态：approved\n\n"
                "| ID | 优先级 | 可观察交付结果 | 依赖 | 验收来源 | 风险 | 状态 | 任务记录 | 集成证据 |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| MVP-001 | Must | selected | 无 | AC-001 | 无 | active | - | - |\n"
                "| MVP-002 | Must | active | 无 | AC-002 | 无 | ready | - | - |\n"
            )
            custom_skill = target / ".agents" / "skills" / "custom-skill"
            custom_skill.mkdir(parents=True)
            (custom_skill / "SKILL.md").write_text(
                "---\nname: custom-skill\ndescription: short\n---\n\nCustom instructions.\n"
            )

            record = basic_record(base)
            record["source"] = {
                "type": "mvp_backlog",
                "reference": "MVP-001",
                "priority_reason": "First ready item",
            }
            task = write_task(target, record)
            result = self.check(target, "preflight", str(task.relative_to(target)))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("WARN", result.stderr)

    def test_start_writes_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = self.install(temporary)
            result = self.check(target, "start")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(
                (target / ".codex-log" / "last-session-check.json").is_file()
            )


if __name__ == "__main__":
    unittest.main()
