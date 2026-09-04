from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import (
    PACKAGE_ROOT,
    approved_requirements,
    commit_all,
    configure_v4_architecture_baseline,
    create_baseline,
    install_project,
    run,
    workflow_command,
)


def _card(**overrides: object) -> dict:
    payload = {
        "task_id": "MVP-LITE-001",
        "request": "Fix the README typo.",
        "scope_in": ["README.md wording"],
        "scope_out": ["Behavior change", "Production release"],
        "allowed_paths": ["README.md"],
        "acceptance": "README.md no longer contains the typo.",
        "authorized_by": "test-owner",
        "source": "user:lite-test",
    }
    payload.update(overrides)
    return payload


class LiteAuthorizeTests(unittest.TestCase):
    def _project(self) -> tuple[Path, str]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        target = Path(directory.name) / "project"
        installed = install_project(target)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        configure_v4_architecture_baseline(target)
        base = create_baseline(target)
        return target, base

    def _write_card(self, target: Path, payload: dict) -> Path:
        path = target.parent / "lite-card.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def test_dry_run_does_not_write_and_apply_authorizes_without_brief(self) -> None:
        target, _base = self._project()
        card = self._write_card(target, _card())
        relative = ".codex-workflow/state/runs/MVP-LITE-001.json"
        dry = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--card-json",
                str(card),
            ),
            cwd=target,
        )
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("LITE_AUTHORIZE_DRY_RUN", dry.stdout)
        self.assertFalse((target / relative).exists())
        applied = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--card-json",
                str(card),
                "--apply",
            ),
            cwd=target,
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn("LITE_AUTHORIZED", applied.stdout)
        record_path = target / relative
        self.assertTrue(record_path.is_file())
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["planning"]["level"], "small")
        self.assertEqual(record["delivery_contract"]["checkpoint"]["mode"], "not_required")
        self.assertEqual(
            record["delivery_contract"]["checkpoint"]["source"],
            "workflow:lite-authorize",
        )
        self.assertTrue(record["implementation_authorization"]["authorized"])
        self.assertEqual(record["lane"]["mode"], "single")
        self.assertEqual(record["source"]["type"], "user_directive")
        self.assertEqual(record["delivery_contract"]["kind"], "governance")
        self.assertEqual(record["source"]["requirements_baseline"]["brief_id"], "REQ-LITE-LOCAL")
        preflight = run(
            workflow_command(target, "workflow_check.py", "preflight", relative),
            cwd=target,
        )
        self.assertEqual(preflight.returncode, 0, preflight.stderr)
        status = (target / ".codex-workflow/state/STATUS.md").read_text(encoding="utf-8")
        self.assertNotIn("lite-observe", status)
        self.assertNotIn("project-script:not-required", status)
        self.assertIn("无独立观察 runner", status)
        skill = (
            PACKAGE_ROOT / "payload/.agents/skills/orchestrate-lite/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("lite-authorize", skill)
        self.assertNotIn("checkpoint.py", skill)

    def test_risk_trigger_and_overwrite_fail_closed(self) -> None:
        target, _base = self._project()
        card = self._write_card(target, _card(task_id="not-a-backlog-id"))
        bad_id = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--card-json",
                str(card),
            ),
            cwd=target,
        )
        self.assertNotEqual(bad_id.returncode, 0)
        extra = self._write_card(target, _card(sensitive_data=True))
        extra_run = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--card-json",
                str(extra),
            ),
            cwd=target,
        )
        self.assertNotEqual(extra_run.returncode, 0)
        self.assertIn("unsupported fields", extra_run.stderr.lower() + extra_run.stdout.lower())
        good = self._write_card(target, _card())
        first = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--card-json",
                str(good),
                "--apply",
            ),
            cwd=target,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--card-json",
                str(good),
                "--apply",
            ),
            cwd=target,
        )
        self.assertNotEqual(second.returncode, 0)
        self.assertRegex(
            (second.stderr + second.stdout).lower(),
            r"overwrite|replace existing",
        )

    def test_flags_authorize_without_json_card(self) -> None:
        target, _base = self._project()
        relative = ".codex-workflow/state/runs/MVP-LITE-002.json"
        dry = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-002",
                "--request",
                "Fix the README typo.",
                "--acceptance",
                "README.md no longer contains the typo.",
                "--allowed-path",
                "README.md",
                "--authorized-by",
                "test-owner",
            ),
            cwd=target,
        )
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("LITE_AUTHORIZE_DRY_RUN", dry.stdout)
        self.assertFalse((target / relative).exists())
        mixed = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--card-json",
                str(self._write_card(target, _card())),
                "--task-id",
                "MVP-LITE-002",
            ),
            cwd=target,
        )
        self.assertNotEqual(mixed.returncode, 0)
        applied = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-002",
                "--request",
                "Fix the README typo.",
                "--acceptance",
                "README.md no longer contains the typo.",
                "--allowed-path",
                "README.md",
                "--authorized-by",
                "test-owner",
                "--apply",
            ),
            cwd=target,
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)
        record = json.loads((target / relative).read_text(encoding="utf-8"))
        self.assertEqual(record["source"]["reference"], "user:lite-authorize")
        self.assertEqual(record["scope"]["allowed_paths"], ["README.md"])
        preflight = run(
            workflow_command(target, "workflow_check.py", "preflight", relative),
            cwd=target,
        )
        self.assertEqual(preflight.returncode, 0, preflight.stderr)

    def test_p1_fail_closed_globs_occupancy_brief_and_template_id(self) -> None:
        target, _base = self._project()
        star = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-STAR",
                "--request",
                "Too wide.",
                "--acceptance",
                "No.",
                "--allowed-path",
                "*",
                "--authorized-by",
                "test-owner",
            ),
            cwd=target,
        )
        self.assertNotEqual(star.returncode, 0)
        self.assertIn("glob", (star.stderr + star.stdout).lower())
        product = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-SRC",
                "--request",
                "Change app.",
                "--acceptance",
                "App changes.",
                "--allowed-path",
                "src/app.py",
                "--authorized-by",
                "test-owner",
            ),
            cwd=target,
        )
        self.assertNotEqual(product.returncode, 0)
        src_test = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-SRCTEST",
                "--request",
                "Change src test helper.",
                "--acceptance",
                "No.",
                "--allowed-path",
                "src/test_app.py",
                "--authorized-by",
                "test-owner",
            ),
            cwd=target,
        )
        self.assertNotEqual(src_test.returncode, 0)
        docs_py = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-DOCSPY",
                "--request",
                "Change renderer.",
                "--acceptance",
                "No.",
                "--allowed-path",
                "src/docs/foo.py",
                "--authorized-by",
                "test-owner",
            ),
            cwd=target,
        )
        self.assertNotEqual(docs_py.returncode, 0)
        txt = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-TXT",
                "--request",
                "Change config.",
                "--acceptance",
                "No.",
                "--allowed-path",
                "config.txt",
                "--authorized-by",
                "test-owner",
            ),
            cwd=target,
        )
        self.assertNotEqual(txt.returncode, 0)
        hijack = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-001",
                "--request",
                "Hijack template.",
                "--acceptance",
                "No.",
                "--allowed-path",
                "README.md",
                "--authorized-by",
                "test-owner",
                "--apply",
            ),
            cwd=target,
        )
        self.assertNotEqual(hijack.returncode, 0)
        first = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-001",
                "--request",
                "Fix the README typo.",
                "--acceptance",
                "README.md no longer contains the typo.",
                "--allowed-path",
                "README.md",
                "--authorized-by",
                "test-owner",
                "--apply",
            ),
            cwd=target,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        commit_all(target, "lite authorize first")
        second = run(
            workflow_command(
                target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-002",
                "--request",
                "Another README fix.",
                "--acceptance",
                "Still a typo.",
                "--allowed-path",
                "README.md",
                "--authorized-by",
                "test-owner",
                "--apply",
            ),
            cwd=target,
        )
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("occupies", (second.stderr + second.stdout).lower())
        dirty_target, _ = self._project()
        (dirty_target / "README.md").write_text("dirty\n", encoding="utf-8")
        dirty = run(
            workflow_command(
                dirty_target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-DIRTY",
                "--request",
                "Fix typo.",
                "--acceptance",
                "Clean.",
                "--allowed-path",
                "README.md",
                "--authorized-by",
                "test-owner",
            ),
            cwd=dirty_target,
        )
        self.assertNotEqual(dirty.returncode, 0)
        self.assertIn("clean worktree", (dirty.stderr + dirty.stdout).lower())
        brief_target, _ = self._project()
        approved_requirements(brief_target)
        commit_all(brief_target, "approve requirements")
        missing_req = run(
            workflow_command(
                brief_target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-BRIEF",
                "--request",
                "Fix typo.",
                "--acceptance",
                "Clean.",
                "--allowed-path",
                "README.md",
                "--authorized-by",
                "test-owner",
            ),
            cwd=brief_target,
        )
        self.assertNotEqual(missing_req.returncode, 0)
        self.assertIn("requirement-id", (missing_req.stderr + missing_req.stdout).lower())
        unknown = run(
            workflow_command(
                brief_target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-BRIEF",
                "--request",
                "Fix typo.",
                "--acceptance",
                "Clean.",
                "--allowed-path",
                "README.md",
                "--authorized-by",
                "test-owner",
                "--requirement-id",
                "REQ-DOES-NOT-EXIST",
            ),
            cwd=brief_target,
        )
        self.assertNotEqual(unknown.returncode, 0)
        bound = run(
            workflow_command(
                brief_target,
                "workflow_state.py",
                "lite-authorize",
                "--task-id",
                "MVP-LITE-BRIEF",
                "--request",
                "Fix typo.",
                "--acceptance",
                "Clean.",
                "--allowed-path",
                "README.md",
                "--authorized-by",
                "test-owner",
                "--requirement-id",
                "REQ-F-001",
                "--apply",
            ),
            cwd=brief_target,
        )
        self.assertEqual(bound.returncode, 0, bound.stderr)
        bound_record = json.loads(
            (brief_target / ".codex-workflow/state/runs/MVP-LITE-BRIEF.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(bound_record["delivery_contract"]["requirement_ids"], ["REQ-F-001"])
        bound_preflight = run(
            workflow_command(
                brief_target,
                "workflow_check.py",
                "preflight",
                ".codex-workflow/state/runs/MVP-LITE-BRIEF.json",
            ),
            cwd=brief_target,
        )
        self.assertEqual(bound_preflight.returncode, 0, bound_preflight.stderr)

