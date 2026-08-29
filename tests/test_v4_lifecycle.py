from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import (
    basic_v4_record,
    configure_v4_architecture_baseline,
    create_baseline,
    install_project,
    run,
    workflow_command,
    write_record,
)


class V4PhaseALifecycleTests(unittest.TestCase):
    def test_status_and_closeout_gate_use_v4_without_milestone_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            self.assertEqual(install_project(target).returncode, 0)
            fingerprint = configure_v4_architecture_baseline(target)
            base = create_baseline(target)
            record = basic_v4_record(base, architecture_fingerprint=fingerprint)
            record["integration"]["status"] = "integrated"
            record["integration"]["target_ref"] = "refs/heads/main"
            record["integration"]["result_commit"] = base
            record["integration"]["closeout_state_fingerprint"] = "a" * 64
            record["integration"]["closeout_fingerprint_version"] = 4
            write_record(target, record)
            synced = run(
                workflow_command(target, "workflow_state.py", "sync-status", "--apply"),
                cwd=target,
            )
            self.assertEqual(synced.returncode, 0, synced.stderr)
            status = (target / ".codex-workflow/state/STATUS.md").read_text(encoding="utf-8")
            self.assertIn("## 产品状态", status)
            self.assertLess(status.index("## 产品状态"), status.index("## 技术交付"))
            self.assertIn("当前焦点", status)
            self.assertNotIn("checks referenced", status)
            gated = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "closeout-gate",
                    ".codex-workflow/state/runs/MVP-001.json",
                ),
                cwd=target,
            )
            combined = gated.stderr + gated.stdout
            self.assertNotIn("versioned closeout milestone", combined)
            layout = json.loads((target / ".codex-workflow/layout.json").read_text(encoding="utf-8"))
            self.assertEqual(layout["default_new_task_version"], 4)


if __name__ == "__main__":
    unittest.main()
