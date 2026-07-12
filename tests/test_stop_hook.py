from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import create_baseline, install_project, run, workflow_command


class StopHookTests(unittest.TestCase):
    def test_coordinator_without_lane_continues_and_broken_pointer_blocks_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            create_baseline(target)
            event = json.dumps({"cwd": str(target), "stop_hook_active": False})
            clean = run(workflow_command(target, "codex_stop_hook.py"), cwd=target, input_text=event)
            self.assertEqual(json.loads(clean.stdout), {"continue": True})

            pointer = target / ".git/codex-workflow-v3/lane.json"
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text("{broken", encoding="utf-8")
            broken = run(workflow_command(target, "codex_stop_hook.py"), cwd=target, input_text=event)
            payload = json.loads(broken.stdout)
            self.assertEqual(payload["decision"], "block")
            self.assertIn("unreadable", payload["reason"])

            repeated = run(
                workflow_command(target, "codex_stop_hook.py"),
                cwd=target,
                input_text=json.dumps({"cwd": str(target), "stop_hook_active": True}),
            )
            repeated_payload = json.loads(repeated.stdout)
            self.assertTrue(repeated_payload["continue"])
            self.assertIn("Do not claim completion", repeated_payload["systemMessage"])


if __name__ == "__main__":
    unittest.main()
