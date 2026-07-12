#!/usr/bin/env python3
"""Install or atomically migrate Codex Workflow V3 into a Git project."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PACKAGE_VERSION = "3.0.0"
PROTOCOL_VERSION = 3
PACKAGE_ROOT = Path(__file__).resolve().parent
PAYLOAD_ROOT = PACKAGE_ROOT / "payload"
HOOKS_PATH = Path(".codex/hooks.json")
GITIGNORE_FRAGMENT = Path(".gitignore.fragment")
V3_MANIFEST = Path(".codex-workflow/install/manifest.json")
V2_MANIFEST = Path(".codex/workflow-v2-install.json")
ENTRY_START = "<!-- BEGIN CODEX WORKFLOW ENTRY -->"
ENTRY_END = "<!-- END CODEX WORKFLOW ENTRY -->"
GITIGNORE_V3_START = "# BEGIN CODEX WORKFLOW V3"
GITIGNORE_V3_END = "# END CODEX WORKFLOW V3"
GITIGNORE_V2_START = "# BEGIN CODEX WORKFLOW V2"
GITIGNORE_V2_END = "# END CODEX WORKFLOW V2"
MANAGED_HOOK_SCRIPTS = {"workflow_check.py", "codex_stop_hook.py"}
LEGACY_PAYLOAD_PREFIXES = {"scripts"}
LEGACY_PAYLOAD_FILES = {Path("PROJECT.md"), Path("PLAN.md"), Path("DECISIONS.md")}


class InstallError(ValueError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Codex Workflow V3.")
    parser.add_argument("target", nargs="?", default=".")
    parser.add_argument("--project-name")
    parser.add_argument("--parallel-mode", choices=("single", "local_worktree"), default="single")
    parser.add_argument("--plan-upgrade", action="store_true", help="Read-only V2-to-V3 migration plan.")
    parser.add_argument("--adopt-v2", action="store_true", help="Explicitly adopt legacy V2 files when no V2 manifest exists.")
    parser.add_argument("--agents-merge-file", help="Human-approved project-only rules extracted from a modified V2 AGENTS.md.")
    parser.add_argument("--force-package", action="store_true", help="Back up and replace only conflicting package-owned files.")
    parser.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--skip-check", action="store_true")
    parser.add_argument("--no-git-init", action="store_true")
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def posix(path: Path | str) -> str:
    raw = str(path).replace("\\", "/")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or (len(raw) >= 2 and raw[1] == ":"):
        raise InstallError(f"Unsafe manifest path: {raw}")
    return pure.as_posix()


def render(data: bytes, project_name: str, install_date: str) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return text.replace("{{PROJECT_NAME}}", project_name).replace("{{INSTALL_DATE}}", install_date).encode("utf-8")


def ownership(relative: Path) -> str:
    value = posix(relative)
    if value == ".codex-workflow/layout.json":
        return "project"
    if value.startswith(".codex-workflow/governance/") or value.startswith(".codex-workflow/state/"):
        return "project"
    return "package"


def payload_entries(project_name: str, install_date: str, parallel_mode: str, *, legacy: bool) -> dict[str, tuple[str, bytes]]:
    entries: dict[str, tuple[str, bytes]] = {}
    for source in sorted(PAYLOAD_ROOT.rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts or source.suffix in {".pyc", ".pyo"}:
            continue
        relative = source.relative_to(PAYLOAD_ROOT)
        if relative in {Path("AGENTS.md"), HOOKS_PATH, GITIGNORE_FRAGMENT, *LEGACY_PAYLOAD_FILES}:
            continue
        if relative.parts and relative.parts[0] in LEGACY_PAYLOAD_PREFIXES:
            continue
        data = render(source.read_bytes(), project_name, install_date)
        if relative == Path(".codex-workflow/layout.json"):
            layout = json.loads(data.decode("utf-8"))
            layout["parallel"]["mode"] = parallel_mode
            if legacy:
                layout["requirements_gate_mode"] = "legacy_warn"
            data = (json.dumps(layout, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        entries[posix(relative)] = (ownership(relative), data)
    return entries


def read_json(path: Path, *, missing_ok: bool = True) -> dict[str, Any]:
    if not path.is_file():
        if missing_ok:
            return {}
        raise InstallError(f"Missing required JSON file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise InstallError(f"JSON root must be an object: {path}")
    return payload


def normalized_v2_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    files = manifest.get("files", {})
    if not isinstance(files, dict):
        raise InstallError("V2 manifest files must be an object.")
    result = {}
    for key, value in files.items():
        if isinstance(key, str) and isinstance(value, str):
            result[posix(key)] = value
    return result


def v3_entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    files = manifest.get("files", {})
    if not isinstance(files, dict):
        raise InstallError("V3 manifest files must be an object.")
    return {posix(key): value for key, value in files.items() if isinstance(key, str) and isinstance(value, dict)}


def extract_block(text: str, start: str, end: str) -> str:
    if text.count(start) != 1 or text.count(end) != 1:
        raise InstallError(f"Managed source must contain exactly one block: {start}")
    _, rest = text.split(start, 1)
    body, after = rest.split(end, 1)
    return start + body + end


def merge_text_block(existing: str, block: str, start: str, end: str, *, legacy_start: str | None = None, legacy_end: str | None = None) -> str:
    starts = existing.count(start)
    ends = existing.count(end)
    if starts != ends or starts > 1:
        raise InstallError(f"Existing file has incomplete or duplicate managed block: {start}")
    if starts == 1:
        before, rest = existing.split(start, 1)
        _, after = rest.split(end, 1)
        result = before.rstrip("\n") + ("\n" if before.strip() else "") + block + ("\n" if after.strip() else "") + after.lstrip("\n")
    elif legacy_start and legacy_start in existing:
        if existing.count(legacy_start) != 1 or existing.count(legacy_end or "") != 1:
            raise InstallError("Legacy managed block is incomplete or duplicated.")
        before, rest = existing.split(legacy_start, 1)
        _, after = rest.split(legacy_end or "", 1)
        result = before.rstrip("\n") + ("\n" if before.strip() else "") + block + ("\n" if after.strip() else "") + after.lstrip("\n")
    else:
        result = existing.rstrip("\n")
        if result.strip():
            result += "\n\n"
        result += block
    return result.rstrip("\n") + "\n"


def handler_is_managed(handler: object) -> bool:
    if not isinstance(handler, dict):
        return False
    commands = [handler.get("command", ""), handler.get("commandWindows", ""), handler.get("command_windows", "")]
    return any(isinstance(command, str) and name in command for command in commands for name in MANAGED_HOOK_SCRIPTS)


def merged_hooks(target_root: Path) -> bytes:
    source = read_json(PAYLOAD_ROOT / HOOKS_PATH, missing_ok=False)
    target_path = target_root / HOOKS_PATH
    target = read_json(target_path) if target_path.exists() else {"hooks": {}}
    hooks = target.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError("Existing .codex/hooks.json hooks must be an object.")
    for event, groups in source.get("hooks", {}).items():
        existing = hooks.get(event, [])
        if not isinstance(existing, list) or not isinstance(groups, list):
            raise InstallError(f"Hook event {event} must contain an array.")
        preserved = []
        for group in existing:
            if not isinstance(group, dict):
                preserved.append(group)
                continue
            handlers = group.get("hooks", [])
            if not isinstance(handlers, list):
                raise InstallError(f"Hook event {event} has an invalid handler group.")
            remaining = [handler for handler in handlers if not handler_is_managed(handler)]
            if remaining:
                updated = dict(group)
                updated["hooks"] = remaining
                preserved.append(updated)
        hooks[event] = preserved + groups
    return (json.dumps(target, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def merged_gitignore(target_root: Path) -> bytes:
    source = (PAYLOAD_ROOT / GITIGNORE_FRAGMENT).read_text(encoding="utf-8").strip()
    if not source.startswith(GITIGNORE_V3_START) or not source.endswith(GITIGNORE_V3_END):
        raise InstallError("Payload gitignore fragment is not a V3 managed block.")
    target = target_root / ".gitignore"
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    return merge_text_block(
        existing, source, GITIGNORE_V3_START, GITIGNORE_V3_END,
        legacy_start=GITIGNORE_V2_START, legacy_end=GITIGNORE_V2_END,
    ).encode("utf-8")


def merged_agents(target_root: Path, *, replace_v2: bool) -> bytes:
    source = (PAYLOAD_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    block = extract_block(source, ENTRY_START, ENTRY_END)
    target = target_root / "AGENTS.md"
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if replace_v2 and ENTRY_START not in existing:
        return ("# AGENTS：Codex Workflow V3 入口\n\n" + block + "\n").encode("utf-8")
    return merge_text_block(existing, block, ENTRY_START, ENTRY_END).encode("utf-8")


def detect_legacy(target_root: Path) -> bool:
    return (target_root / V2_MANIFEST).is_file() or any(
        (target_root / path).exists()
        for path in ("scripts/workflow_check.py", "PROJECT.md", ".agent", "docs/WORKFLOW_V2.md")
    )


def transform_project(text: str) -> bytes:
    marker = (
        "<!-- CODEX_REQUIREMENTS_BASELINE_START -->\n"
        '{"brief_id": null, "revision": null, "approval_fingerprint": null}\n'
        "<!-- CODEX_REQUIREMENTS_BASELINE_END -->\n\n"
    )
    if "CODEX_REQUIREMENTS_BASELINE_START" in text:
        return text.encode("utf-8")
    lines = text.splitlines()
    if lines:
        return (lines[0] + "\n\n" + marker + "\n".join(lines[1:]).lstrip("\n") + "\n").encode("utf-8")
    return ("# PROJECT\n\n" + marker).encode("utf-8")


def split_row(line: str) -> list[str]:
    if not line.strip().startswith("|"):
        return []
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def transform_backlog(text: str) -> bytes:
    metadata = (
        "<!-- CODEX_REQUIREMENTS_BASELINE_START -->\n"
        '{"workflow_schema_version": 3, "requirements_gate_mode": "legacy_warn", '
        '"brief_id": null, "revision": null, "approval_fingerprint": null, '
        '"target_release": null, "status": "legacy"}\n'
        "<!-- CODEX_REQUIREMENTS_BASELINE_END -->\n\n"
    )
    lines = text.splitlines()
    transformed: list[str] = []
    in_table = False
    for line in lines:
        cells = split_row(line)
        if cells and cells[0] == "ID" and len(cells) == 9:
            transformed.append("| " + " | ".join(cells[:7] + ["阻塞类型", cells[7], "Lane/资源", cells[8]]) + " |")
            in_table = True
            continue
        if in_table and cells and len(cells) == 9 and set(cells[0]) <= {"-", ":"}:
            transformed.append("| " + " | ".join(cells[:7] + ["---", cells[7], "---", cells[8]]) + " |")
            continue
        if cells and len(cells) == 9 and re.fullmatch(r"(?:MVP|OPS)-[A-Za-z0-9._-]+", cells[0]):
            if cells[6] == "active":
                raise InstallError(f"Legacy Backlog still contains active task {cells[0]}; close it in V2 first.")
            blocking = "dependencies" if cells[6] == "blocked" and cells[3] not in {"", "无", "-"} else "none"
            transformed.append("| " + " | ".join(cells[:7] + [blocking, cells[7], "-", cells[8]]) + " |")
            continue
        transformed.append(line)
    return (metadata + "\n".join(transformed).rstrip("\n") + "\n").encode("utf-8")


def migrate_record(data: bytes) -> bytes:
    try:
        record = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data
    if not isinstance(record, dict):
        return data
    planning = record.get("planning")
    if isinstance(planning, dict) and isinstance(planning.get("exec_plan"), str):
        planning["exec_plan"] = planning["exec_plan"].replace("\\", "/").replace(".agent/plans/", ".codex-workflow/state/plans/")
    return (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def migration_overrides(target_root: Path, agents_merge_file: str | None) -> tuple[dict[str, bytes], list[Path]]:
    overrides: dict[str, bytes] = {}
    deletes: list[Path] = []
    mappings = {
        Path("PROJECT.md"): ".codex-workflow/governance/PROJECT.md",
        Path("PLAN.md"): ".codex-workflow/governance/PLAN.md",
        Path("DECISIONS.md"): ".codex-workflow/governance/DECISIONS.md",
        Path("docs/MVP_BACKLOG.md"): ".codex-workflow/state/MVP_BACKLOG.md",
    }
    for source_relative, destination in mappings.items():
        source = target_root / source_relative
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        if source_relative == Path("PROJECT.md"):
            data = transform_project(text)
        elif source_relative == Path("docs/MVP_BACKLOG.md"):
            data = transform_backlog(text)
        else:
            data = text.encode("utf-8")
        overrides[destination] = data
        deletes.append(source_relative)
    for source_dir, destination_dir in ((Path(".agent/runs"), ".codex-workflow/state/runs"), (Path(".agent/plans"), ".codex-workflow/state/plans")):
        root = target_root / source_dir
        if not root.is_dir():
            continue
        for source in sorted(root.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(root).as_posix()
            data = source.read_bytes()
            if source_dir.name == "runs" and source.suffix == ".json":
                data = migrate_record(data)
            overrides[f"{destination_dir}/{relative}"] = data
            deletes.append(source.relative_to(target_root))
    if agents_merge_file:
        merge_path = Path(agents_merge_file).expanduser().resolve()
        data = merge_path.read_bytes()
        if not data.strip():
            raise InstallError("--agents-merge-file must contain confirmed project rules.")
        overrides[".codex-workflow/governance/AGENTS.md"] = data.rstrip(b"\r\n") + b"\n"
    for relative in (
        Path("scripts/workflow_check.py"), Path("scripts/codex_stop_hook.py"),
        Path("docs/WORKFLOW_V2.md"), Path(".codex-log/last-session-check.json"), V2_MANIFEST,
    ):
        if (target_root / relative).is_file():
            deletes.append(relative)
    return overrides, sorted(set(deletes), key=lambda path: posix(path))


def git_root(target_root: Path, *, initialize: bool) -> Path:
    if not shutil.which("git"):
        raise InstallError("Git is required but was not found in PATH.")
    result = subprocess.run(["git", "-C", str(target_root), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if result.returncode != 0:
        if not initialize:
            raise InstallError("Target is not a Git repository and --no-git-init was requested.")
        initialized = subprocess.run(
            ["git", "-C", str(target_root), "init", "-b", "main"],
            capture_output=True,
            text=True,
        )
        if initialized.returncode != 0:
            subprocess.run(["git", "-C", str(target_root), "init"], check=True, capture_output=True, text=True)
        result = subprocess.run(["git", "-C", str(target_root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
    detected = Path(result.stdout.strip()).resolve()
    if detected != target_root.resolve():
        raise InstallError(f"Install at the Git repository root, not nested inside {detected}.")
    return detected


def git_common_dir(target_root: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(target_root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return Path(result.stdout.strip()).resolve()
    fallback = subprocess.run(["git", "-C", str(target_root), "rev-parse", "--git-common-dir"], capture_output=True, text=True, check=True)
    candidate = Path(fallback.stdout.strip())
    return (target_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != data:
            raise OSError(f"Temporary reread failed for {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def backup_file(target_root: Path, target: Path, backup_root: Path) -> None:
    if not target.is_file():
        return
    destination = backup_root / target.relative_to(target_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, destination)


def remember(path: Path, originals: dict[Path, tuple[bytes, int] | None]) -> None:
    if path in originals:
        return
    originals[path] = (path.read_bytes(), path.stat().st_mode) if path.is_file() else None


def write_managed(path: Path, data: bytes, originals: dict[Path, tuple[bytes, int] | None], target_root: Path, backup_root: Path) -> None:
    if path.is_file() and path.read_bytes() == data:
        return
    remember(path, originals)
    backup_file(target_root, path, backup_root)
    atomic_write(path, data)


def delete_managed(path: Path, originals: dict[Path, tuple[bytes, int] | None], target_root: Path, backup_root: Path) -> None:
    if not path.is_file():
        return
    remember(path, originals)
    backup_file(target_root, path, backup_root)
    path.unlink()


def restore(originals: dict[Path, tuple[bytes, int] | None]) -> None:
    for path, original in reversed(list(originals.items())):
        if original is None:
            if path.is_file():
                path.unlink()
            continue
        data, mode = original
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, data)
        path.chmod(mode)


def failpoint(name: str) -> None:
    if os.environ.get("CODEX_WORKFLOW_TEST_FAIL_AT") == name:
        raise InstallError(f"Injected installation failure at {name}.")


def run_check(target_root: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-B", str(target_root / ".codex-workflow/bin/workflow_check.py"), "manual"],
        cwd=target_root, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise InstallError("Installed V3 workflow self-check failed.")


def main() -> None:
    if sys.version_info < (3, 9):
        raise SystemExit("Python 3.9 or newer is required.")
    args = parse_args()
    args.force_package = args.force_package or args.force
    target_root = Path(args.target).expanduser().resolve()
    if target_root == PACKAGE_ROOT or PACKAGE_ROOT in target_root.parents:
        raise SystemExit("Choose a project directory outside the workflow package.")
    if not target_root.exists() and args.plan_upgrade:
        raise SystemExit("--plan-upgrade requires an existing target.")

    # V2 active work is an absolute zero-write migration blocker.
    active_pointer = target_root / ".agent/active-task"
    if active_pointer.exists():
        print("V3 migration refused: .agent/active-task exists. Close or explicitly abandon it in V2 first.", file=sys.stderr)
        raise SystemExit(2)

    legacy = target_root.exists() and detect_legacy(target_root)
    v2_manifest = read_json(target_root / V2_MANIFEST) if target_root.exists() else {}
    if legacy and not v2_manifest and not args.adopt_v2:
        raise SystemExit("Legacy V2 files have no ownership manifest; rerun read-only with --plan-upgrade, then use --adopt-v2 explicitly.")
    v2_hashes = normalized_v2_hashes(v2_manifest) if v2_manifest else {}
    modified_v2_agents = False
    if legacy and (target_root / "AGENTS.md").is_file():
        expected = v2_hashes.get("AGENTS.md")
        modified_v2_agents = expected is None or sha256((target_root / "AGENTS.md").read_bytes()) != expected
        if modified_v2_agents and not args.agents_merge_file:
            message = (
                "Modified V2 AGENTS.md requires a human three-way merge. Compare the V2 manifest baseline, "
                "current AGENTS.md, and payload/.codex-workflow/protocol/AGENTS.md; place confirmed project-only "
                "rules in a file and pass --agents-merge-file. No files were changed."
            )
            print(message, file=sys.stderr)
            raise SystemExit(2)

    project_name = args.project_name or target_root.name
    install_date = dt.date.today().isoformat()
    entries = payload_entries(project_name, install_date, args.parallel_mode, legacy=legacy)
    overrides, legacy_deletes = migration_overrides(target_root, args.agents_merge_file) if legacy else ({}, [])
    for relative, data in overrides.items():
        entries[relative] = ("project", data)

    previous_v3 = read_json(target_root / V3_MANIFEST) if target_root.exists() else {}
    previous_entries = v3_entries(previous_v3) if previous_v3 else {}
    conflicts: list[str] = []
    planned_writes: list[str] = []
    for relative, (owner, data) in entries.items():
        destination = target_root / Path(*PurePosixPath(relative).parts)
        if owner == "project" and destination.exists() and relative not in overrides:
            continue
        if not destination.is_file() or destination.read_bytes() == data:
            if not destination.is_file():
                planned_writes.append(relative)
            continue
        if owner == "project":
            if relative in overrides:
                planned_writes.append(relative)
            continue
        previous = previous_entries.get(relative, {})
        if previous.get("ownership") == "package" and previous.get("managed_sha256") == sha256(destination.read_bytes()):
            planned_writes.append(relative)
        elif args.force_package:
            planned_writes.append(relative)
        else:
            conflicts.append(relative)

    merge_outputs = {
        "AGENTS.md": merged_agents(target_root, replace_v2=legacy),
        ".codex/hooks.json": merged_hooks(target_root),
        ".gitignore": merged_gitignore(target_root),
    }
    for relative, data in merge_outputs.items():
        destination = target_root / Path(*PurePosixPath(relative).parts)
        if not destination.is_file() or destination.read_bytes() != data:
            planned_writes.append(relative)

    plan = {
        "package": "codex-workflow-v3",
        "version": PACKAGE_VERSION,
        "target": str(target_root),
        "migration": "v2_to_v3" if legacy else "new_or_v3_upgrade",
        "modified_v2_agents": modified_v2_agents,
        "writes": sorted(set(planned_writes)),
        "legacy_deletes": [posix(path) for path in legacy_deletes],
        "package_conflicts": sorted(conflicts),
        "parallel_mode": args.parallel_mode,
    }
    if args.plan_upgrade:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(2 if conflicts else 0)
    if conflicts:
        print("Installation stopped; package-owned files have user changes:")
        for relative in conflicts:
            print(f"  - {relative}")
        print("Use --force-package only after reviewing the backup impact. Project-owned governance is never overwritten.")
        raise SystemExit(2)

    target_root.mkdir(parents=True, exist_ok=True)
    try:
        git_root(target_root, initialize=not args.no_git_init)
        common_dir = git_common_dir(target_root)
    except (InstallError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    runtime = common_dir / "codex-workflow-v3"
    backup_root = runtime / "backups" / stamp
    audit_root = runtime / "audit"
    audit_root.mkdir(parents=True, exist_ok=True)
    originals: dict[Path, tuple[bytes, int] | None] = {}
    journal_path = audit_root / f"install-{stamp}.json"
    journal = {"started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "target": str(target_root), "plan": plan, "state": "started"}
    atomic_write(journal_path, (json.dumps(journal, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    manifest_files: dict[str, dict[str, Any]] = {}
    try:
        for relative, (owner, data) in entries.items():
            destination = target_root / Path(*PurePosixPath(relative).parts)
            if owner == "project" and destination.exists() and relative not in overrides:
                pass
            else:
                write_managed(destination, data, originals, target_root, backup_root)
            manifest_files[relative] = {
                "ownership": owner,
                ("managed_sha256" if owner == "package" else "seed_sha256"): sha256(data),
            }

        for relative, data in merge_outputs.items():
            destination = target_root / Path(*PurePosixPath(relative).parts)
            write_managed(destination, data, originals, target_root, backup_root)
            if relative == "AGENTS.md":
                fragment = extract_block((PAYLOAD_ROOT / "AGENTS.md").read_text(encoding="utf-8"), ENTRY_START, ENTRY_END).encode("utf-8")
            elif relative == ".gitignore":
                fragment = (PAYLOAD_ROOT / GITIGNORE_FRAGMENT).read_bytes()
            else:
                fragment = (PAYLOAD_ROOT / HOOKS_PATH).read_bytes()
            manifest_files[relative] = {"ownership": "merge", "managed_sha256": sha256(fragment)}

        for relative in (
            ".codex-workflow/bin/workflow_check.py",
            ".codex-workflow/bin/workflow_state.py",
            ".codex-workflow/bin/workflow_lane.py",
            ".codex-workflow/bin/codex_stop_hook.py",
        ):
            script = target_root / Path(*PurePosixPath(relative).parts)
            if script.is_file():
                remember(script, originals)
                script.chmod(script.stat().st_mode | 0o111)

        failpoint("after-stage")
        if not args.skip_check:
            run_check(target_root)

        for relative in legacy_deletes:
            delete_managed(target_root / relative, originals, target_root, backup_root)
        for directory in (target_root / ".agent/runs", target_root / ".agent/plans", target_root / ".agent", target_root / "scripts", target_root / ".codex-log"):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        remaining = [posix(path) for path in legacy_deletes if (target_root / path).is_file()]
        if remaining:
            raise InstallError("Legacy cleanup proof failed: " + ", ".join(remaining))
        failpoint("after-legacy-cleanup")

        manifest = {
            "package": "codex-workflow-v3",
            "version": PACKAGE_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "files": {key: manifest_files[key] for key in sorted(manifest_files)},
        }
        manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        write_managed(target_root / V3_MANIFEST, manifest_data, originals, target_root, backup_root)
        failpoint("after-manifest")
        if not args.skip_check:
            run_check(target_root)
    except (InstallError, OSError, subprocess.CalledProcessError) as exc:
        restore(originals)
        journal["state"] = "rolled_back"
        journal["error"] = str(exc)
        journal["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        atomic_write(journal_path, (json.dumps(journal, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        raise SystemExit(f"Installation failed and tracked file changes were restored: {exc}") from exc

    journal["state"] = "complete"
    journal["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    journal["manifest_sha256"] = sha256((target_root / V3_MANIFEST).read_bytes())
    atomic_write(journal_path, (json.dumps(journal, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"Codex Workflow V3 installed in: {target_root}")
    print(f"Package version: {PACKAGE_VERSION}")
    if backup_root.exists():
        print(f"Backups written under Git common-dir runtime: {backup_root}")
    print("Review and trust the updated project Hooks; start a new Codex session and verify the runtime heartbeat.")
    print("No product push, PR, merge, baseline commit, release, or deployment was performed.")


if __name__ == "__main__":
    main()
