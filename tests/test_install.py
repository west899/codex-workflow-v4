from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import install_project


class InstallerTests(unittest.TestCase):
    def test_clean_reinstall_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "project"
            first = install_project(target)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = install_project(target)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertFalse((target / ".codex-workflow-backup").exists())

    def test_reinstall_replaces_managed_sections_without_duplication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "project"
            first = install_project(target)
            self.assertEqual(first.returncode, 0, first.stderr)

            hooks_path = target / ".codex" / "hooks.json"
            hooks = json.loads(hooks_path.read_text())
            hooks["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 31
            hooks["hooks"]["Stop"].append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "echo user-hook",
                        }
                    ]
                }
            )
            hooks_path.write_text(json.dumps(hooks, indent=2) + "\n")

            gitignore = target / ".gitignore"
            gitignore.write_text(gitignore.read_text().replace(".env.*\n", ""))

            second = install_project(target)
            self.assertEqual(second.returncode, 0, second.stderr)

            updated_hooks = json.loads(hooks_path.read_text())
            stop_handlers = [
                handler
                for group in updated_hooks["hooks"]["Stop"]
                for handler in group["hooks"]
            ]
            managed = [
                handler
                for handler in stop_handlers
                if "codex_stop_hook.py" in handler.get("command", "")
            ]
            self.assertEqual(len(managed), 1)
            self.assertTrue(any(h.get("command") == "echo user-hook" for h in stop_handlers))
            self.assertIn("commandWindows", managed[0])
            self.assertIn(".env.*", gitignore.read_text())

            manifest = json.loads(
                (target / ".codex" / "workflow-v2-install.json").read_text()
            )
            self.assertEqual(manifest["version"], "2.1.0")

    def test_failed_install_restores_written_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "project"
            hooks = target / ".codex" / "hooks.json"
            hooks.parent.mkdir(parents=True)
            hooks.write_text('{"hooks": []}\n')

            result = install_project(target)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("file changes were restored", result.stderr)
            self.assertEqual(hooks.read_text(), '{"hooks": []}\n')
            self.assertFalse((target / "AGENTS.md").exists())


if __name__ == "__main__":
    unittest.main()
