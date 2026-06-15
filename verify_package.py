#!/usr/bin/env python3
"""Verify that the portable package is complete and project-neutral."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REQUIRED = {
    "README.md",
    "install.py",
    "payload/AGENTS.md",
    "payload/PROJECT.md",
    "payload/PLAN.md",
    "payload/DECISIONS.md",
    "payload/.gitignore.fragment",
    "payload/.codex/hooks.json",
    "payload/.codex/agents/developer.toml",
    "payload/.codex/agents/reviewer.toml",
    "payload/scripts/workflow_check.py",
    "payload/scripts/codex_stop_hook.py",
    "payload/.agents/skills/orchestrate-project-task/SKILL.md",
    "payload/.agents/skills/implement-project-task/SKILL.md",
    "payload/.agents/skills/review-project-change/SKILL.md",
    "payload/.agents/skills/orchestrate-project-task/agents/openai.yaml",
    "payload/.agents/skills/implement-project-task/agents/openai.yaml",
    "payload/.agents/skills/review-project-change/agents/openai.yaml",
    "payload/.agents/skills/orchestrate-project-task/references/task-record-template.json",
    "payload/.agents/skills/orchestrate-project-task/references/rule-proposal-template.json",
    "payload/.agents/skills/orchestrate-project-task/references/mvp-backlog-template.md",
    "payload/.agents/skills/implement-project-task/references/exec-plan-template.md",
    "payload/.agents/skills/review-project-change/references/review-checklist.md",
    "tests/support.py",
    "tests/test_end_to_end.py",
    "tests/test_install.py",
    "tests/test_workflow_check.py",
    "tests/test_stop_hook.py",
}
FORBIDDEN = {
    "/Users/" + "xy",
    "社交" + "软件",
    "具体要解决哪一种" + "社交问题",
    "2026-" + "06-09",
}


def main() -> None:
    if sys.version_info < (3, 9):
        raise SystemExit("Python 3.9 or newer is required.")

    missing = [path for path in sorted(REQUIRED) if not (ROOT / path).is_file()]
    if missing:
        raise SystemExit("Missing package files:\n" + "\n".join(missing))

    generated = [
        path
        for path in ROOT.rglob("*")
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}
    ]
    if generated:
        raise SystemExit(
            "Generated cache files must be removed:\n"
            + "\n".join(str(path.relative_to(ROOT)) for path in generated)
        )

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for marker in ("<!-- WORKFLOW_DOC_START -->", "<!-- WORKFLOW_DOC_END -->"):
        if marker not in readme:
            raise SystemExit(f"README.md is missing marker: {marker}")

    for path in ROOT.rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))

    for path in ROOT.rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    for path in ROOT.rglob("*.toml"):
        content = path.read_text(encoding="utf-8")
        for key in ("name", "description", "developer_instructions"):
            if not re.search(rf"^{key}\s*=", content, re.MULTILINE):
                raise SystemExit(f"{path.relative_to(ROOT)} is missing {key}")

    for path in ROOT.rglob("openai.yaml"):
        content = path.read_text(encoding="utf-8")
        for marker in ("interface:", "default_prompt:", "allow_implicit_invocation:"):
            if marker not in content:
                raise SystemExit(f"{path.relative_to(ROOT)} is missing {marker}")

    for path in ROOT.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for forbidden in FORBIDDEN:
            if forbidden in text:
                raise SystemExit(f"Project-specific text {forbidden!r} in {path}")

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    tests = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    if tests.returncode != 0:
        raise SystemExit(
            "Package regression tests failed:\n" + tests.stdout + tests.stderr
        )

    print(f"PACKAGE_OK ({tests.stderr.strip().splitlines()[-1]})")


if __name__ == "__main__":
    main()
