from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from support import (
    PACKAGE_ROOT,
    basic_v3_record,
    commit_all,
    configure_git,
    create_baseline,
    install_project,
    run,
    workflow_command,
    write_record,
)


class InstallTests(unittest.TestCase):
    def test_install_module_does_not_keep_unused_legacy_package_name(self) -> None:
        script = PACKAGE_ROOT / "install.py"
        spec = importlib.util.spec_from_file_location("install_under_test", script)
        if spec is None or spec.loader is None:
            raise AssertionError(f"cannot load {script}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.PACKAGE_NAME, "codex-workflow-v4")
        self.assertEqual(module.LEGACY_RUNTIME_DIRNAME, "codex-workflow-v3")
        self.assertFalse(hasattr(module, "LEGACY_PACKAGE_NAME"))

    def test_fresh_install_uses_v3_layout_and_deterministic_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            result = install_project(target, parallel_mode="local_worktree")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / ".codex-workflow/bin/workflow_lane.py").is_file())
            self.assertFalse((target / "scripts/workflow_check.py").exists())
            self.assertFalse((target / ".agent").exists())
            layout = json.loads((target / ".codex-workflow/layout.json").read_text(encoding="utf-8"))
            self.assertEqual(layout["parallel"]["mode"], "local_worktree")
            manifest_text = (target / ".codex-workflow/install/manifest.json").read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)
            self.assertEqual(manifest["package"], "codex-workflow-v4")
            self.assertTrue((target / ".git/codex-workflow-v4").exists())
            self.assertFalse((target / ".git/codex-workflow-v3").exists())
            self.assertNotIn("installed_at", manifest)
            self.assertEqual(manifest["files"][".codex-workflow/governance/PROJECT.md"]["ownership"], "project")
            self.assertEqual(manifest["files"][".codex-workflow/bin/workflow_check.py"]["ownership"], "package")
            hooks = json.loads((target / ".codex/hooks.json").read_text(encoding="utf-8"))
            serialized = json.dumps(hooks)
            self.assertIn(".codex-workflow/bin/workflow_check.py", serialized)
            self.assertNotIn("/scripts/workflow_check.py", serialized)
            status = (target / ".codex-workflow/state/STATUS.md").read_text(encoding="utf-8")
            self.assertLess(status.index("## 产品状态"), status.index("CODEX_WORKFLOW_STATUS_JSON_START"))

    def test_existing_v3_runtime_directory_is_still_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            v4 = target / ".git/codex-workflow-v4"
            v3 = target / ".git/codex-workflow-v3"
            self.assertTrue(v4.exists())
            v4.rename(v3)
            started = run(workflow_command(target, "workflow_check.py", "start"), cwd=target)
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertTrue((v3 / "audit" / "last-session-check.json").is_file())
            self.assertFalse(v4.exists())

    def test_reinstall_is_content_idempotent_and_preserves_project_governance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            first = install_project(target)
            self.assertEqual(first.returncode, 0, first.stderr)
            project = target / ".codex-workflow/governance/PROJECT.md"
            project.write_text(project.read_text(encoding="utf-8") + "\nUser-owned fact.\n", encoding="utf-8")
            manifest_before = (target / ".codex-workflow/install/manifest.json").read_bytes()
            second = install_project(target)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("User-owned fact.", project.read_text(encoding="utf-8"))
            self.assertEqual((target / ".codex-workflow/install/manifest.json").read_bytes(), manifest_before)

    def test_package_conflict_requires_narrow_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            protocol = target / ".codex-workflow/protocol/AGENTS.md"
            protocol.write_text("user changed package file\n", encoding="utf-8")
            stopped = install_project(target)
            self.assertEqual(stopped.returncode, 2)
            self.assertIn("package-owned", stopped.stdout)
            forced = install_project(target, extra=["--force-package"])
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertIn("Codex Workflow V4", protocol.read_text(encoding="utf-8"))

    def test_v2_active_pointer_is_zero_write_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            (target / ".agent").mkdir(parents=True)
            (target / ".agent/active-task").write_text(".agent/runs/MVP-001.json\n", encoding="utf-8")
            before = sorted((path.relative_to(target).as_posix(), path.read_bytes()) for path in target.rglob("*") if path.is_file())
            result = install_project(target, extra=["--adopt-v2"])
            after = sorted((path.relative_to(target).as_posix(), path.read_bytes()) for path in target.rglob("*") if path.is_file())
            self.assertEqual(result.returncode, 2)
            self.assertEqual(before, after)

    def test_failure_injection_restores_every_tracked_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            missing = target / ".codex-workflow/bin/workflow_state.py"
            missing.unlink()
            before = sorted((path.relative_to(target).as_posix(), path.read_bytes()) for path in target.rglob("*") if path.is_file() and ".git/" not in path.relative_to(target).as_posix())
            result = run(
                [
                    __import__("sys").executable,
                    "-B",
                    str(PACKAGE_ROOT / "install.py"),
                    str(target),
                    "--project-name",
                    "workflow-test",
                ],
                cwd=PACKAGE_ROOT,
                env_extra={"CODEX_WORKFLOW_TEST_FAIL_AT": "after-stage"},
            )
            after = sorted((path.relative_to(target).as_posix(), path.read_bytes()) for path in target.rglob("*") if path.is_file() and ".git/" not in path.relative_to(target).as_posix())
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(before, after)
            self.assertFalse(missing.exists())

    def test_manifest_backed_v2_migration_moves_project_state_and_removes_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            target.mkdir()
            run(["git", "init", "-b", "main"], cwd=target)
            configure_git(target)
            agents = "# V2 AGENTS\n\nManaged V2 protocol.\n"
            files = {
                "AGENTS.md": agents,
                "PROJECT.md": "# PROJECT\n\nConfirmed user fact.\n",
                "PLAN.md": "# PLAN\n\nCurrent plan.\n",
                "DECISIONS.md": "# DECISIONS\n\n| W-007 | V2 single task |\n",
                "docs/MVP_BACKLOG.md": (
                    "# MVP Backlog\n\n"
                    "| ID | 优先级 | 可观察交付结果 | 依赖 | 验收来源 | 风险 | 状态 | 任务记录 | 集成证据 |\n"
                    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                    "| MVP-001 | Must | Existing result | 无 | AC-1 | 无 | verified | .agent/runs/MVP-001.json | - |\n"
                ),
                "scripts/workflow_check.py": "print('v2')\n",
            }
            hashes = {}
            for relative, content in files.items():
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            record = target / ".agent/runs/MVP-001.json"
            record.parent.mkdir(parents=True)
            record.write_text(json.dumps({"version": 2, "task_id": "MVP-001", "status": "completed", "planning": {"exec_plan": ".agent/plans/MVP-001.md"}}), encoding="utf-8")
            hashes[".agent/runs/MVP-001.json"] = hashlib.sha256(record.read_bytes()).hexdigest()
            manifest_path = target / ".codex/workflow-v2-install.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps({"package": "codex-workflow-v2", "version": "2.1.0", "files": hashes}), encoding="utf-8")
            commit_all(target, "legacy v2")

            plan = install_project(target, extra=["--plan-upgrade"])
            self.assertEqual(plan.returncode, 0, plan.stderr)
            self.assertTrue((target / "PROJECT.md").is_file(), "read-only plan changed files")
            migrated = install_project(target)
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            self.assertFalse((target / "PROJECT.md").exists())
            self.assertFalse((target / "scripts/workflow_check.py").exists())
            self.assertFalse(manifest_path.exists())
            self.assertIn("Confirmed user fact.", (target / ".codex-workflow/governance/PROJECT.md").read_text(encoding="utf-8"))
            backlog = (target / ".codex-workflow/state/MVP_BACKLOG.md").read_text(encoding="utf-8")
            self.assertIn("legacy_warn", backlog)
            self.assertIn("| verified | none |", backlog)
            migrated_record = json.loads((target / ".codex-workflow/state/runs/MVP-001.json").read_text(encoding="utf-8"))
            self.assertEqual(migrated_record["planning"]["exec_plan"], ".codex-workflow/state/plans/MVP-001.md")

    def test_plan_upgrade_lists_inventory_without_guessing_focus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            base = create_baseline(target)
            write_record(target, basic_v3_record(base))
            planned = install_project(target, extra=["--plan-upgrade"])
            self.assertEqual(planned.returncode, 0, planned.stderr)
            payload = json.loads(planned.stdout)
            self.assertEqual(payload["version"], "4.0.0")
            self.assertEqual(payload["supported_task_record_versions"], [3, 4])
            self.assertEqual(payload["default_new_task_record_version"], 4)
            inventory = payload["v4_inventory"]
            self.assertEqual(inventory["v3_record_ids"], ["MVP-001"])
            self.assertEqual(inventory["v4_record_ids"], [])
            self.assertEqual(inventory["governance_customizations"], [])
            self.assertTrue(inventory["backlog_focus_metadata"])
            self.assertEqual(
                inventory["guardrail_registry"],
                {"present": True, "status": "unconfigured"},
            )
            self.assertEqual(inventory["pending_queued_recovery"], "abandon_only")
            self.assertFalse(inventory["architecture_baseline"]["guessed_focus"])
            self.assertFalse(inventory["architecture_baseline"]["approved"])
            notes = " ".join(inventory["notes"])
            self.assertIn("does not guess the current focus", notes)
            self.assertIn("does not generate or approve", notes)
            self.assertIn("does not overwrite them", notes)
            self.assertIn("abandon-only", notes)
            layout = json.loads((target / ".codex-workflow/layout.json").read_text(encoding="utf-8"))
            self.assertEqual(layout["default_new_task_version"], 4)
            manifest = json.loads((target / ".codex-workflow/install/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], "4.0.0")
            self.assertEqual(manifest["supported_task_record_versions"], [3, 4])
            project = target / ".codex-workflow/governance/PROJECT.md"
            customized = project.read_text(encoding="utf-8") + "\nConfirmed custom fact.\n"
            project.write_text(customized, encoding="utf-8")
            planned_custom = install_project(target, extra=["--plan-upgrade"])
            self.assertEqual(planned_custom.returncode, 0, planned_custom.stderr)
            custom_inventory = json.loads(planned_custom.stdout)["v4_inventory"]
            self.assertEqual(
                custom_inventory["governance_customizations"],
                [".codex-workflow/governance/PROJECT.md"],
            )
            self.assertEqual(project.read_text(encoding="utf-8"), customized)

    def test_uninstall_dry_run_is_zero_write_and_apply_keeps_product_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            product = target / "src/app.py"
            product.parent.mkdir()
            product.write_text("print('product')\n", encoding="utf-8")
            agents = target / "AGENTS.md"
            agents.write_text(agents.read_text(encoding="utf-8") + "\n# Product rules\nKeep me.\n", encoding="utf-8")
            before = product.read_bytes()
            dry = install_project(target, extra=["--uninstall"])
            self.assertEqual(dry.returncode, 0, dry.stderr)
            plan = json.loads(dry.stdout)
            self.assertEqual(plan["action"], "uninstall")
            self.assertFalse(plan["apply"])
            self.assertTrue(plan["product_source_untouched"])
            self.assertTrue((target / ".codex-workflow/bin/workflow_check.py").is_file())
            self.assertEqual(product.read_bytes(), before)
            applied = install_project(target, extra=["--uninstall", "--apply"])
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertFalse((target / ".codex-workflow/bin/workflow_check.py").exists())
            self.assertTrue((target / ".codex-workflow/governance/PROJECT.md").is_file())
            self.assertEqual(product.read_bytes(), before)
            self.assertIn("Keep me.", (target / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertNotIn("BEGIN CODEX WORKFLOW ENTRY", (target / "AGENTS.md").read_text(encoding="utf-8"))

    def test_uninstall_purge_state_removes_workflow_log_not_product(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            product = target / "README-product.md"
            product.write_text("product readme\n", encoding="utf-8")
            purged = install_project(target, extra=["--uninstall", "--apply", "--purge-state"])
            self.assertEqual(purged.returncode, 0, purged.stderr)
            self.assertFalse((target / ".codex-workflow").exists())
            self.assertTrue(product.is_file())

    def test_export_product_excludes_workflow_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            dest = Path(directory) / "export"
            self.assertEqual(install_project(target).returncode, 0)
            src = target / "src/app.py"
            src.parent.mkdir()
            src.write_text("print('ok')\n", encoding="utf-8")
            dry = install_project(target, extra=["--export-product", str(dest)])
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertFalse(dest.exists())
            applied = install_project(target, extra=["--export-product", str(dest), "--apply"])
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertTrue((dest / "src/app.py").is_file())
            self.assertFalse((dest / ".codex-workflow").exists())
            self.assertFalse((dest / ".agents/skills").exists())
            self.assertTrue((target / ".codex-workflow/bin/workflow_check.py").is_file())


if __name__ == "__main__":
    unittest.main()
