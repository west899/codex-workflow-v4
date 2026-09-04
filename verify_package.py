#!/usr/bin/env python3
"""Verify that the Codex Workflow V4 package is complete and portable."""

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
    "LICENSE",
    "CHANGELOG.md",
    "功能.md",
    "docs/history/README.md",
    "docs/history/problem.md",
    "docs/history/改进建议.md",
    "docs/history/always-on-and-lite.md",
    "install.py",
    "payload/AGENTS.md",
    "payload/.gitignore.fragment",
    "payload/.codex/hooks.json",
    "payload/.codex/agents/developer.toml",
    "payload/.codex/agents/reviewer.toml",
    "payload/.codex-workflow/layout.json",
    "payload/.codex-workflow/protocol/AGENTS.md",
    "payload/.codex-workflow/governance/AGENTS.md",
    "payload/.codex-workflow/governance/PROJECT.md",
    "payload/.codex-workflow/governance/PLAN.md",
    "payload/.codex-workflow/governance/DECISIONS.md",
    "payload/.codex-workflow/state/MVP_BACKLOG.md",
    "payload/.codex-workflow/state/STATUS.md",
    "payload/.codex-workflow/state/requirements-impacts/.gitkeep",
    "payload/.codex-workflow/docs/WORKFLOW.md",
    "payload/.codex-workflow/bin/workflow_paths.py",
    "payload/.codex-workflow/bin/workflow_lock.py",
    "payload/.codex-workflow/bin/workflow_common.py",
    "payload/.codex-workflow/bin/workflow_check.py",
    "payload/.codex-workflow/bin/workflow_state.py",
    "payload/.codex-workflow/bin/workflow_lane.py",
    "payload/.codex-workflow/bin/codex_stop_hook.py",
    "payload/.codex-workflow/schemas/task-record-v3.schema.json",
    "payload/.codex-workflow/schemas/task-record-v4.schema.json",
    "payload/.codex-workflow/schemas/developer-evidence-v1.schema.json",
    "payload/.codex-workflow/schemas/review-evidence-v1.schema.json",
    "payload/.codex-workflow/schemas/requirements-v1.schema.json",
    "payload/.codex-workflow/schemas/lane-v1.schema.json",
    "payload/.codex-workflow/schemas/remote-claim-v1.schema.json",
    "payload/.codex-workflow/schemas/provider-receipt-v1.schema.json",
    "payload/.agents/skills/orchestrate-lite/SKILL.md",
    "payload/.agents/skills/orchestrate-lite/agents/openai.yaml",
    "payload/.agents/skills/orchestrate-project-task/SKILL.md",
    "payload/.agents/skills/implement-project-task/SKILL.md",
    "payload/.agents/skills/review-project-change/SKILL.md",
    "payload/.agents/skills/orchestrate-project-task/agents/openai.yaml",
    "payload/.agents/skills/implement-project-task/agents/openai.yaml",
    "payload/.agents/skills/review-project-change/agents/openai.yaml",
    "payload/.agents/skills/orchestrate-project-task/references/task-record-template.json",
    "payload/.agents/skills/orchestrate-project-task/references/task-record-v4-core-template.json",
    "payload/.agents/skills/orchestrate-project-task/references/task-record-v4-supporting-template.json",
    "payload/.agents/skills/orchestrate-project-task/references/requirements-brief-template.md",
    "payload/.agents/skills/orchestrate-project-task/references/rule-proposal-template.json",
    "payload/.agents/skills/orchestrate-project-task/references/mvp-backlog-template.md",
    "payload/.agents/skills/orchestrate-project-task/references/claim-lane.md",
    "payload/.agents/skills/orchestrate-project-task/references/closeout.md",
    "payload/.agents/skills/implement-project-task/references/exec-plan-template.md",
    "payload/.agents/skills/implement-project-task/references/developer-evidence.md",
    "payload/.agents/skills/review-project-change/references/review-checklist.md",
    "tests/test_v4_always_on.py",
    "tests/test_v4_lite_authorize.py",
    "tests/support.py",
    "tests/test_install.py",
    "tests/test_local_bootstrap_expiry.py",
    "tests/test_role_locks.py",
    "tests/test_schema_gate.py",
    "tests/test_requirements_impact.py",
    "tests/test_canonical_delivery.py",
    "tests/test_workflow_check.py",
    "tests/test_workflow_state.py",
    "tests/test_workflow_lane.py",
    "tests/test_parallel_closeout.py",
    "tests/test_fault_recovery.py",
    "tests/test_remote_claim_recovery.py",
    "tests/test_remote_release.py",
    "tests/test_stop_hook.py",
    "tests/test_end_to_end.py",
    "tests/test_v4_contract.py",
    "tests/test_v4_workflow.py",
    "tests/test_v4_status.py",
    "tests/test_v4_closeout.py",
    "tests/test_v4_lifecycle.py",
    "tests/test_v4_phase_b.py",
    "tests/test_v4_phase_c.py",
}
FORBIDDEN_FILES = {
    "payload/PROJECT.md",
    "payload/PLAN.md",
    "payload/DECISIONS.md",
    "payload/scripts/workflow_check.py",
    "payload/scripts/codex_stop_hook.py",
}
PROJECT_SPECIFIC = {
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
    forbidden = [path for path in sorted(FORBIDDEN_FILES) if (ROOT / path).exists()]
    if forbidden:
        raise SystemExit("Legacy V2 payload files must not ship:\n" + "\n".join(forbidden))

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

    install_text = (ROOT / "install.py").read_text(encoding="utf-8")
    version_match = re.search(r'^PACKAGE_VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$', install_text, re.MULTILINE)
    if version_match is None:
        raise SystemExit("install.py PACKAGE_VERSION is missing.")
    package_version = version_match.group(1)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {package_version}" not in changelog.splitlines():
        raise SystemExit(f"CHANGELOG.md must include ## {package_version}.")
    released_headings = [
        line
        for line in changelog.splitlines()
        if line.startswith("## ") and line not in {"## Unreleased", "## Changelog"}
    ]
    if not released_headings or released_headings[0] != f"## {package_version}":
        raise SystemExit("CHANGELOG.md latest release heading must match install.py PACKAGE_VERSION.")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if f"v{package_version}" not in readme:
        raise SystemExit("README.md must pin the current product tag.")
    if "不要从后续 V5 开发分支安装" not in readme:
        raise SystemExit("README.md must tell installers not to follow a future V5 branch.")
    if "仍能发现 `codex-workflow-v4` runtime" not in readme:
        raise SystemExit("README.md must require future V5 packages to keep discovering the V4 runtime.")

    layout = json.loads((ROOT / "payload/.codex-workflow/layout.json").read_text(encoding="utf-8"))
    for field in ("layout_version", "protocol_version", "workflow_schema_version"):
        if layout.get(field) != 3:
            raise SystemExit(f"layout.json requires {field}=3")
    if layout.get("task_record_versions") != [3, 4]:
        raise SystemExit("layout.json must declare task_record_versions [3, 4].")
    if layout.get("default_new_task_version") != 4:
        raise SystemExit("layout.json default_new_task_version must be 4.")
    if layout.get("paths", {}).get("bin") != ".codex-workflow/bin":
        raise SystemExit("layout.json bin path is not the final V3 location.")

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

    hooks = json.loads((ROOT / "payload/.codex/hooks.json").read_text(encoding="utf-8"))
    serialized_hooks = json.dumps(hooks)
    if ".codex-workflow/bin/workflow_check.py" not in serialized_hooks or ".codex-workflow/bin/codex_stop_hook.py" not in serialized_hooks:
        raise SystemExit("Hooks do not target the final V3 bin paths.")
    if serialized_hooks.count("workflow_check.py") != 2 or serialized_hooks.count("codex_stop_hook.py") != 2:
        raise SystemExit("Payload Hooks must contain one Unix and one Windows command per V3 handler.")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    schema_heading = "## JSON Schema 结构门禁"
    schema_start = readme.find(schema_heading)
    if schema_start < 0:
        raise SystemExit("README.md is missing the JSON Schema gate section.")
    schema_section = readme[schema_start:]
    next_heading = schema_section.find("\n## ", 1)
    if next_heading > 0:
        schema_section = schema_section[:next_heading]
    if "task-record-v4" not in schema_section:
        raise SystemExit("README.md schema gate must include task-record-v4.")
    if "所有 V3 task record 读取时" in schema_section:
        raise SystemExit("README.md schema gate must not describe V3-only record validation.")
    if "六份 schema" in schema_section:
        raise SystemExit("README.md schema count must include task-record-v4.")

    entry = (ROOT / "payload/AGENTS.md").read_text(encoding="utf-8")
    if entry.count("BEGIN CODEX WORKFLOW ENTRY") != 1 or entry.count("END CODEX WORKFLOW ENTRY") != 1:
        raise SystemExit("Root AGENTS payload is not a unique thin V3 entry.")
    for path in ROOT.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for forbidden_text in PROJECT_SPECIFIC:
            if forbidden_text in text:
                raise SystemExit(f"Project-specific text {forbidden_text!r} in {path.relative_to(ROOT)}")

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    tests = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if tests.returncode != 0:
        raise SystemExit("Package regression tests failed:\n" + tests.stdout + tests.stderr)
    summary = next((line for line in reversed(tests.stderr.splitlines()) if line.startswith("Ran ")), "tests passed")
    print(f"PACKAGE_OK ({summary})")


if __name__ == "__main__":
    main()
