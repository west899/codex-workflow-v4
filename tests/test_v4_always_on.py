from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from support import PACKAGE_ROOT, install_project

ENTRY_START = "<!-- BEGIN CODEX WORKFLOW ENTRY -->"
ENTRY_END = "<!-- END CODEX WORKFLOW ENTRY -->"
NO_AUTO = "不得自动 push、开 PR、合并、force、删除 branch/worktree、发布或部署"
NUMBERED_ITEM = re.compile(r"(?m)^\s*\d+\.\s+(.+)$")
HANDBOOK = re.compile(
    r"WORKFLOW\.md|protocol/AGENTS\.md|完整协议手册|full protocol handbook|protocol/governance",
    re.I,
)
DEFERRED = re.compile(
    r"不要通读|不要在动手前|do not start|do not read|do not load|"
    r"not session-start|not at session start|"
    r"before record-developer|before `record-developer`|"
    r"only when claiming|only when sealing|only when preparing|"
    r"until that claim|only at this closeout|only at that evidence|"
    r"按需|当前阶段|before the current phase|when reconstructing",
    re.I,
)
EARLY_MANDATE = re.compile(
    r"before editing.{0,80}protocol"
    r"|read the .{0,60}protocol/governance"
    r"|依次读取.{0,240}WORKFLOW"
    r"|every file it points to"
    r"|开始任何项目工作前.{0,240}WORKFLOW"
    r"|开始前请阅读.{0,80}WORKFLOW"
    r"|先读.{0,40}WORKFLOW",
    re.I | re.S,
)


def _entry_block(text: str) -> str:
    start = text.find(ENTRY_START)
    end = text.find(ENTRY_END)
    if start < 0 or end < 0 or end <= start:
        raise AssertionError("unique BEGIN/END workflow entry block is missing")
    return text[start : end + len(ENTRY_END)]


def _markdown_section(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\n.*?(?=^## |\Z)",
        text,
        re.M | re.S,
    )
    if match is None:
        raise AssertionError(f"missing markdown section {heading!r}")
    return match.group(0)


def _numbered_items(text: str) -> list[str]:
    return [item.strip() for item in NUMBERED_ITEM.findall(text)]


class AlwaysOnGuidanceTests(unittest.TestCase):
    def test_shipped_and_installed_start_path_stays_thin(self) -> None:
        self._assert_thin_tree(PACKAGE_ROOT / "payload")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            installed = install_project(target)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self._assert_thin_tree(target)
            self.assertTrue((target / ".codex-workflow/docs/WORKFLOW.md").is_file())
            self.assertTrue((target / ".codex-workflow/protocol/AGENTS.md").is_file())
            hooks = json.loads((target / ".codex/hooks.json").read_text(encoding="utf-8"))
            session_start = json.dumps(hooks["hooks"]["SessionStart"])
            self.assertIn("workflow_check.py", session_start)
            self.assertRegex(session_start, r"workflow_check\.py\\\" start")
            manifest = json.loads(
                (target / ".codex-workflow/install/manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["package"], "codex-workflow-v4")
            self.assertTrue((target / ".git/codex-workflow-v4").exists())

    def _assert_thin_tree(self, root: Path) -> None:
        agents = (root / "AGENTS.md").read_text(encoding="utf-8")
        protocol = (root / ".codex-workflow/protocol/AGENTS.md").read_text(encoding="utf-8")
        coordinator = (
            root / ".agents/skills/orchestrate-project-task/SKILL.md"
        ).read_text(encoding="utf-8")
        implement = (
            root / ".agents/skills/implement-project-task/SKILL.md"
        ).read_text(encoding="utf-8")
        review_skill = (
            root / ".agents/skills/review-project-change/SKILL.md"
        ).read_text(encoding="utf-8")
        developer = (root / ".codex/agents/developer.toml").read_text(encoding="utf-8")
        reviewer = (root / ".codex/agents/reviewer.toml").read_text(encoding="utf-8")
        self.assertEqual(agents.count("BEGIN CODEX WORKFLOW ENTRY"), 1)
        self.assertEqual(agents.count("END CODEX WORKFLOW ENTRY"), 1)
        entry = _entry_block(agents)
        start = _markdown_section(protocol, "1. 开始顺序")
        coord_start = _markdown_section(coordinator, "1. Establish ground truth")
        implement_start = _markdown_section(implement, "1. Verify lane identity before editing")
        review_start = _markdown_section(review_skill, "1. Bind to one lane and snapshot")
        always_on = entry + "\n" + start
        self.assertLessEqual(
            len(always_on.splitlines()),
            200,
            f"always-on start text is {len(always_on.splitlines())} lines",
        )
        self.assertIn(NO_AUTO, entry)
        self.assertIn("workflow_check.py start", start)
        self.assertNotIn("checkpoint.py", always_on)
        for item in _numbered_items(entry) + _numbered_items(start):
            self.assertNotIn("WORKFLOW.md", item)
        for label, text in (
            ("root AGENTS.md", agents),
            ("protocol start", start),
            ("coordinator start", coord_start),
            ("developer skill start", implement_start),
            ("reviewer skill start", review_start),
            ("developer.toml", developer),
            ("reviewer.toml", reviewer),
        ):
            self._assert_no_early_handbook(text, label)
        for label, text in (
            ("full coordinator skill", coordinator),
            ("full developer skill", implement),
            ("full reviewer skill", review_skill),
        ):
            self.assertIsNone(
                EARLY_MANDATE.search(text),
                f"{label} has a handbook-before-work mandate",
            )
            self.assertNotIn("依次读取", text)
            self.assertNotIn("every file it points to", text)
        self.assertNotIn("protocol/governance", developer)
        self.assertNotIn("Before editing, read the V3 protocol", developer)
        self.assertIn("Do not start by reading", developer)
        self.assertIn("Before record-developer", developer)
        self.assertIn("Do not start by reading the full protocol handbook", reviewer)
        self.assertNotIn("canonical_delivery_paths", implement)
        self.assertIn("canonical_delivery_paths", (
            root / ".agents/skills/implement-project-task/references/developer-evidence.md"
        ).read_text(encoding="utf-8"))

    def _assert_no_early_handbook(self, text: str, label: str) -> None:
        self.assertIsNone(
            EARLY_MANDATE.search(text),
            f"{label} mandates the handbook before work: {text[:240]!r}",
        )
        self.assertNotIn("依次读取", text)
        self.assertNotIn("every file it points to", text)
        for match in HANDBOOK.finditer(text):
            window = text[max(0, match.start() - 160) : match.end() + 80]
            self.assertRegex(
                window,
                DEFERRED,
                f"{label} mentions {match.group(0)!r} without deferring it: {window!r}",
            )


class OnDemandPhaseCitationTests(unittest.TestCase):
    def test_phase_references_cite_protocol_or_workflow_without_skill_blob(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            installed = install_project(target)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            skills = target / ".agents/skills"
            coordinator = (skills / "orchestrate-project-task/SKILL.md").read_text(
                encoding="utf-8"
            )
            claim_ref = (
                skills / "orchestrate-project-task/references/claim-lane.md"
            ).read_text(encoding="utf-8")
            closeout_ref = (
                skills / "orchestrate-project-task/references/closeout.md"
            ).read_text(encoding="utf-8")
            developer = (skills / "implement-project-task/SKILL.md").read_text(
                encoding="utf-8"
            )
            evidence_ref = (
                skills / "implement-project-task/references/developer-evidence.md"
            ).read_text(encoding="utf-8")
            reviewer = (skills / "review-project-change/SKILL.md").read_text(
                encoding="utf-8"
            )
            claim_section = _markdown_section(coordinator, "4. Choose single or isolated lane")
            evidence_section = _markdown_section(developer, "1. Verify lane identity before editing")
            closeout_section = _markdown_section(coordinator, "8. Two-phase closeout")

            self.assertIn("references/claim-lane.md", claim_section)
            self.assertIn("workflow_lane.py claim", claim_ref)
            self.assertIn("protocol/AGENTS.md", claim_ref)
            self.assertIn("docs/WORKFLOW.md", claim_ref)
            self.assertIn("not session-start", claim_ref)

            self.assertIn("references/developer-evidence.md", evidence_section)
            self.assertIn("record-developer", evidence_ref)
            self.assertIn("docs/WORKFLOW.md", evidence_ref)
            self.assertIn("not at session start", evidence_ref)

            self.assertIn("references/closeout.md", closeout_section)
            self.assertIn("confirm-closeout", closeout_ref)
            self.assertIn("docs/WORKFLOW.md", closeout_ref)
            self.assertIn("Independent Reviewer", closeout_ref)

            self.assertIn("docs/WORKFLOW.md", reviewer)
            self.assertNotIn("checkpoint.py", coordinator + developer + reviewer)
            self.assertIn("Do not invent a second checkpoint command", coordinator)

    def test_early_mandate_detector_catches_prose_relapse(self) -> None:
        self.assertIsNotNone(EARLY_MANDATE.search("开始前请阅读 WORKFLOW.md"))
        self.assertIsNotNone(EARLY_MANDATE.search("先读 docs/WORKFLOW.md 再 claim"))
        self.assertIsNone(EARLY_MANDATE.search("不要在动手前通读 WORKFLOW.md"))
        relapse = "开始工作时先读 WORKFLOW.md 再 claim"
        window = relapse
        self.assertIsNone(DEFERRED.search("先读 WORKFLOW.md 再 claim"))
        self.assertIsNotNone(HANDBOOK.search(relapse))
        self.assertIsNone(DEFERRED.search(window))
