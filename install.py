#!/usr/bin/env python3
"""Install the portable Codex Workflow V2.1 payload into a project root."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


PACKAGE_VERSION = "2.1.0"
PACKAGE_ROOT = Path(__file__).resolve().parent
PAYLOAD_ROOT = PACKAGE_ROOT / "payload"
README_PATH = PACKAGE_ROOT / "README.md"
WORKFLOW_START = "<!-- WORKFLOW_DOC_START -->"
WORKFLOW_END = "<!-- WORKFLOW_DOC_END -->"
HOOKS_PATH = Path(".codex/hooks.json")
GITIGNORE_FRAGMENT = Path(".gitignore.fragment")
MANIFEST_PATH = Path(".codex/workflow-v2-install.json")
GITIGNORE_START = "# BEGIN CODEX WORKFLOW V2"
GITIGNORE_END = "# END CODEX WORKFLOW V2"
MANAGED_HOOK_SCRIPTS = {"workflow_check.py", "codex_stop_hook.py"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install Codex Workflow V2.1 into a Git project root."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="Target project root. Defaults to the current directory.",
    )
    parser.add_argument(
        "--project-name",
        help="Project name written into initial governance documents.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Back up and replace conflicting workflow files.",
    )
    parser.add_argument(
        "--no-git-init",
        action="store_true",
        help="Do not initialize Git when the target is not already a repository.",
    )
    parser.add_argument(
        "--skip-check",
        action="store_true",
        help="Skip the installed workflow self-check.",
    )
    return parser.parse_args()


def workflow_document() -> str:
    readme = README_PATH.read_text(encoding="utf-8")
    if WORKFLOW_START not in readme or WORKFLOW_END not in readme:
        raise ValueError("README.md is missing workflow document markers.")
    body = readme.split(WORKFLOW_START, 1)[1].split(WORKFLOW_END, 1)[0]
    return body.strip() + "\n"


def render(data: bytes, project_name: str, install_date: str) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return (
        text.replace("{{PROJECT_NAME}}", project_name)
        .replace("{{INSTALL_DATE}}", install_date)
        .encode("utf-8")
    )


def payload_files(project_name: str, install_date: str):
    for source in sorted(PAYLOAD_ROOT.rglob("*")):
        if not source.is_file():
            continue
        if "__pycache__" in source.parts or source.suffix in {".pyc", ".pyo"}:
            continue
        relative = source.relative_to(PAYLOAD_ROOT)
        if relative in {HOOKS_PATH, GITIGNORE_FRAGMENT}:
            continue
        yield relative, render(source.read_bytes(), project_name, install_date)
    yield Path("docs/WORKFLOW_V2.md"), workflow_document().encode("utf-8")


def backup_file(target_root: Path, target_file: Path, backup_root: Path) -> None:
    if not target_file.exists():
        return
    relative = target_file.relative_to(target_root)
    destination = backup_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target_file, destination)


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(target_root: Path) -> dict:
    target = target_root / MANIFEST_PATH
    if not target.is_file():
        return {}
    try:
        manifest = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Existing {MANIFEST_PATH} is invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files", {}), dict):
        raise ValueError(f"Existing {MANIFEST_PATH} has an invalid structure.")
    return manifest


def remember_file(
    target: Path,
    originals: dict[Path, tuple[bytes, int] | None],
) -> None:
    if target in originals:
        return
    if target.exists():
        originals[target] = (target.read_bytes(), target.stat().st_mode)
    else:
        originals[target] = None


def write_bytes(
    target: Path,
    data: bytes,
    originals: dict[Path, tuple[bytes, int] | None],
) -> None:
    remember_file(target, originals)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def restore_files(originals: dict[Path, tuple[bytes, int] | None]) -> None:
    for target, original in reversed(list(originals.items())):
        if original is None:
            if target.exists():
                target.unlink()
            continue
        data, mode = original
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)


def handler_is_managed(handler: object) -> bool:
    if not isinstance(handler, dict):
        return False
    commands = [
        handler.get("command", ""),
        handler.get("commandWindows", ""),
        handler.get("command_windows", ""),
    ]
    return any(
        isinstance(command, str) and script_name in command
        for command in commands
        for script_name in MANAGED_HOOK_SCRIPTS
    )


def merge_hooks(
    target_root: Path,
    backup_root: Path,
    originals: dict[Path, tuple[bytes, int] | None],
) -> bool:
    source = json.loads((PAYLOAD_ROOT / HOOKS_PATH).read_text(encoding="utf-8"))
    target_path = target_root / HOOKS_PATH
    if target_path.exists():
        try:
            target = json.loads(target_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Existing {HOOKS_PATH} is invalid JSON: {exc}") from exc
    else:
        target = {"hooks": {}}

    original_serialized = json.dumps(target, sort_keys=True, ensure_ascii=False)
    target_hooks = target.setdefault("hooks", {})
    if not isinstance(target_hooks, dict):
        raise ValueError(f"Existing {HOOKS_PATH} hooks must be an object.")
    for event, groups in source.get("hooks", {}).items():
        preserved_groups = []
        existing_groups = target_hooks.get(event, [])
        if not isinstance(existing_groups, list):
            raise ValueError(f"Existing {HOOKS_PATH} {event} must be a list.")
        for group in existing_groups:
            if not isinstance(group, dict):
                preserved_groups.append(group)
                continue
            group_handlers = group.get("hooks", [])
            if not isinstance(group_handlers, list):
                raise ValueError(
                    f"Existing {HOOKS_PATH} {event} group hooks must be a list."
                )
            preserved_handlers = [
                handler
                for handler in group_handlers
                if not handler_is_managed(handler)
            ]
            if preserved_handlers:
                updated_group = dict(group)
                updated_group["hooks"] = preserved_handlers
                preserved_groups.append(updated_group)
        target_hooks[event] = preserved_groups + groups

    changed = (
        original_serialized != json.dumps(target, sort_keys=True, ensure_ascii=False)
        or not target_path.exists()
    )
    if changed:
        backup_file(target_root, target_path, backup_root)
        write_bytes(
            target_path,
            (json.dumps(target, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            originals,
        )
    return changed


def validate_existing_hooks(target_root: Path) -> None:
    target_path = target_root / HOOKS_PATH
    if not target_path.exists():
        return
    try:
        json.loads(target_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Existing {HOOKS_PATH} is invalid JSON: {exc}") from exc


def update_gitignore(
    target_root: Path,
    backup_root: Path,
    originals: dict[Path, tuple[bytes, int] | None],
) -> bool:
    fragment = (PAYLOAD_ROOT / GITIGNORE_FRAGMENT).read_text(encoding="utf-8").strip()
    target = target_root / ".gitignore"
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    start_count = existing.count(GITIGNORE_START)
    end_count = existing.count(GITIGNORE_END)
    has_start = start_count > 0
    has_end = end_count > 0
    if has_start != has_end:
        raise ValueError(
            ".gitignore contains an incomplete Codex Workflow V2 managed block."
        )
    if start_count > 1 or end_count > 1:
        raise ValueError(
            ".gitignore contains duplicate Codex Workflow V2 managed blocks."
        )
    if has_start:
        before, remainder = existing.split(GITIGNORE_START, 1)
        _, after = remainder.split(GITIGNORE_END, 1)
        updated = before + fragment + after
        if not updated.endswith("\n"):
            updated += "\n"
    else:
        separator = "" if not existing or existing.endswith("\n") else "\n"
        prefix = "\n" if existing.strip() else ""
        updated = existing + separator + prefix + fragment + "\n"
    if updated == existing:
        return False
    backup_file(target_root, target, backup_root)
    write_bytes(target, updated.encode("utf-8"), originals)
    return True


def ensure_git_root(target_root: Path, no_git_init: bool) -> bool:
    git = shutil.which("git")
    if not git:
        raise ValueError("Git is required but was not found in PATH.")

    result = subprocess.run(
        [git, "-C", str(target_root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        detected = Path(result.stdout.strip()).resolve()
        if detected != target_root:
            raise ValueError(
                f"Target is inside another Git repository ({detected}); "
                "install at that repository root."
            )
        return True

    if no_git_init:
        return False
    subprocess.run([git, "-C", str(target_root), "init"], check=True)
    return True


def run_check(target_root: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(target_root / "scripts/workflow_check.py"),
            "manual",
        ],
        cwd=target_root,
        text=True,
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise ValueError("Installed workflow self-check failed.")


def main() -> None:
    if sys.version_info < (3, 9):
        raise SystemExit("Python 3.9 or newer is required.")
    args = parse_args()
    target_root = Path(args.target).expanduser().resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    if target_root == PACKAGE_ROOT or PACKAGE_ROOT in target_root.parents:
        raise SystemExit("Choose a project directory outside the workflow package.")

    project_name = args.project_name or target_root.name
    install_date = dt.date.today().isoformat()
    planned = list(payload_files(project_name, install_date))
    try:
        validate_existing_hooks(target_root)
        previous_manifest = load_manifest(target_root)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    previous_hashes = previous_manifest.get("files", {})
    conflicts = []
    for relative, data in planned:
        destination = target_root / relative
        if not destination.exists() or destination.read_bytes() == data:
            continue
        previous_hash = previous_hashes.get(str(relative))
        if previous_hash != file_sha256(destination.read_bytes()):
            conflicts.append(relative)
    if conflicts and not args.force:
        print("Installation stopped; these files already exist with other content:")
        for path in conflicts:
            print(f"  - {path}")
        print("Re-run with --force to back them up and replace them.")
        raise SystemExit(2)

    try:
        has_git = ensure_git_root(target_root, args.no_git_init)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_root = target_root / ".codex-workflow-backup" / stamp
    originals: dict[Path, tuple[bytes, int] | None] = {}
    manifest_files = {str(relative): file_sha256(data) for relative, data in planned}
    manifest = {
        "package": "codex-workflow-v2",
        "version": PACKAGE_VERSION,
        "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": manifest_files,
    }
    if (
        previous_manifest.get("package") == manifest["package"]
        and previous_manifest.get("version") == manifest["version"]
        and previous_manifest.get("files") == manifest_files
        and isinstance(previous_manifest.get("installed_at"), str)
    ):
        manifest["installed_at"] = previous_manifest["installed_at"]
    try:
        for relative, data in planned:
            destination = target_root / relative
            if destination.exists() and destination.read_bytes() == data:
                continue
            if destination.exists():
                backup_file(target_root, destination, backup_root)
            write_bytes(destination, data, originals)

        merge_hooks(target_root, backup_root, originals)
        update_gitignore(target_root, backup_root, originals)

        for script in ("scripts/workflow_check.py", "scripts/codex_stop_hook.py"):
            path = target_root / script
            remember_file(path, originals)
            path.chmod(path.stat().st_mode | 0o111)

        if not args.skip_check:
            if not has_git:
                print("Self-check skipped because the target is not a Git repository.")
            else:
                run_check(target_root)

        manifest_target = target_root / MANIFEST_PATH
        manifest_data = (
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        if manifest_target.exists() and manifest_target.read_bytes() == manifest_data:
            pass
        else:
            backup_file(target_root, manifest_target, backup_root)
            write_bytes(manifest_target, manifest_data, originals)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        restore_files(originals)
        raise SystemExit(f"Installation failed and file changes were restored: {exc}") from exc

    print(f"Codex Workflow V2.1 installed in: {target_root}")
    print(f"Package version: {PACKAGE_VERSION}")
    if backup_root.exists():
        print(f"Backups written to: {backup_root}")
    print("Review and trust the project hooks in Codex before relying on automation.")
    print("Start a Codex session and confirm .codex-log/last-session-check.json is created.")
    print("No baseline commit was created. Human approval is required before code tasks.")


if __name__ == "__main__":
    main()
