#!/usr/bin/env python3
"""Shared deterministic data and Git helpers for Codex Workflow V3."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from workflow_paths import WorkflowPathError, WorkflowPaths, normalize_repo_path


REQUIREMENTS_START = "<!-- CODEX_REQUIREMENTS_JSON_START -->"
REQUIREMENTS_END = "<!-- CODEX_REQUIREMENTS_JSON_END -->"
HEX_OID = re.compile(r"^[0-9a-f]{40,64}$")


class WorkflowDataError(ValueError):
    """Tracked workflow data is missing, inconsistent, or unsafe."""


def fault_injection(name: str) -> None:
    """Abort a workflow write boundary when an isolated regression test requests it."""

    if os.environ.get("CODEX_WORKFLOW_TEST_FAIL_AT") == name:
        raise OSError(f"Injected workflow failure at {name}.")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


_SUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "$ref",
        "type",
        "required",
        "properties",
        "const",
        "enum",
        "minLength",
        "minimum",
        "minItems",
        "items",
        "pattern",
    }
)
_SUPPORTED_JSON_TYPES = frozenset({"null", "boolean", "object", "array", "number", "integer", "string"})


def _schema_path(parent: str, key: str) -> str:
    return f"{parent}.{key}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) else f"{parent}[{json.dumps(key)}]"


def _schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _schema_json_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's bool-is-int equivalence."""

    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(_schema_json_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_schema_json_equal(left[key], right[key]) for key in left)
    return left == right


def _load_json_schema(path: Path, schema_root: Path, cache: dict[Path, Any]) -> Any:
    resolved = path.resolve()
    if schema_root != resolved.parent and schema_root not in resolved.parents:
        raise WorkflowDataError(f"JSON Schema reference escapes the schemas directory: {path}")
    if resolved in cache:
        return cache[resolved]
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowDataError(f"Unable to read JSON Schema {resolved.name}: {exc}") from exc
    if not isinstance(payload, (dict, bool)):
        raise WorkflowDataError(f"JSON Schema {resolved.name} must be an object or boolean.")
    cache[resolved] = payload
    return payload


def _resolve_json_schema_ref(
    reference: Any,
    *,
    current_path: Path,
    schema_root: Path,
    cache: dict[Path, Any],
) -> tuple[Any, Path]:
    if not isinstance(reference, str) or not reference:
        raise WorkflowDataError("JSON Schema $ref must be a non-empty string.")
    document, marker, fragment = reference.partition("#")
    if "://" in document or document.startswith(("/", "\\")):
        raise WorkflowDataError(f"JSON Schema external $ref is not allowed: {reference}")
    target_path = current_path if not document else (current_path.parent / document).resolve()
    target = _load_json_schema(target_path, schema_root, cache)
    if not marker:
        return target, target_path
    if fragment and not fragment.startswith("/"):
        raise WorkflowDataError(f"JSON Schema $ref fragment is not a JSON pointer: {reference}")
    for raw_part in filter(None, fragment.split("/")):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(target, dict) and part in target:
            target = target[part]
        elif isinstance(target, list) and part.isdigit() and int(part) < len(target):
            target = target[int(part)]
        else:
            raise WorkflowDataError(f"JSON Schema $ref cannot resolve: {reference}")
    return target, target_path


def _validate_json_schema_definition(
    schema: Any,
    *,
    schema_path: Path,
    schema_root: Path,
    cache: dict[Path, Any],
    ref_chain: set[tuple[Path, str]],
    location: str,
) -> None:
    """Fail closed if a shipped schema uses a construct this validator cannot honor."""

    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise WorkflowDataError(
            f"Invalid JSON Schema {schema_path.name} at {location}: node must be an object or boolean."
        )
    unsupported = sorted(set(schema) - _SUPPORTED_SCHEMA_KEYS)
    if unsupported:
        raise WorkflowDataError(
            f"Invalid JSON Schema {schema_path.name} at {location}: unsupported keyword(s): "
            + ", ".join(unsupported)
        )

    if "$ref" in schema:
        reference = schema["$ref"]
        try:
            target, target_path = _resolve_json_schema_ref(
                reference,
                current_path=schema_path,
                schema_root=schema_root,
                cache=cache,
            )
        except WorkflowDataError as exc:
            raise WorkflowDataError(
                f"Invalid JSON Schema {schema_path.name} at {location}: {exc}"
            ) from exc
        marker = (target_path, str(reference))
        if marker in ref_chain:
            raise WorkflowDataError(
                f"Invalid JSON Schema {schema_path.name} at {location}: $ref cycle detected: {reference}"
            )
        _validate_json_schema_definition(
            target,
            schema_path=target_path,
            schema_root=schema_root,
            cache=cache,
            ref_chain=ref_chain | {marker},
            location=f"{location}->$ref({reference})",
        )

    if "type" in schema:
        declared = schema["type"]
        types = [declared] if isinstance(declared, str) else declared
        if (
            not isinstance(types, list)
            or not types
            or any(not isinstance(item, str) or item not in _SUPPORTED_JSON_TYPES for item in types)
        ):
            raise WorkflowDataError(
                f"Invalid JSON Schema {schema_path.name} at {location}: type must be a supported JSON type or non-empty array of types."
            )
    if "required" in schema:
        required = schema["required"]
        if (
            not isinstance(required, list)
            or any(not isinstance(item, str) for item in required)
            or len(required) != len(set(required))
        ):
            raise WorkflowDataError(
                f"Invalid JSON Schema {schema_path.name} at {location}: required must be a unique array of property names."
            )
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise WorkflowDataError(
            f"Invalid JSON Schema {schema_path.name} at {location}: enum must be a non-empty array."
        )
    for keyword in ("minLength", "minItems"):
        if keyword in schema:
            value = schema[keyword]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise WorkflowDataError(
                    f"Invalid JSON Schema {schema_path.name} at {location}: {keyword} must be a non-negative integer."
                )
    if "minimum" in schema:
        minimum = schema["minimum"]
        if not isinstance(minimum, (int, float)) or isinstance(minimum, bool):
            raise WorkflowDataError(
                f"Invalid JSON Schema {schema_path.name} at {location}: minimum must be a number."
            )
    if "pattern" in schema:
        pattern = schema["pattern"]
        if not isinstance(pattern, str):
            raise WorkflowDataError(
                f"Invalid JSON Schema {schema_path.name} at {location}: pattern must be a string."
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise WorkflowDataError(
                f"Invalid JSON Schema {schema_path.name} at {location}: invalid pattern: {exc}"
            ) from exc
    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, dict) or any(not isinstance(key, str) for key in properties):
            raise WorkflowDataError(
                f"Invalid JSON Schema {schema_path.name} at {location}: properties must be an object with string names."
            )
        for key, child in properties.items():
            _validate_json_schema_definition(
                child,
                schema_path=schema_path,
                schema_root=schema_root,
                cache=cache,
                ref_chain=ref_chain,
                location=_schema_path(location, key),
            )
    if "items" in schema:
        _validate_json_schema_definition(
            schema["items"],
            schema_path=schema_path,
            schema_root=schema_root,
            cache=cache,
            ref_chain=ref_chain,
            location=f"{location}.items",
        )


def _validate_json_schema_node(
    schema: Any,
    value: Any,
    *,
    path: str,
    schema_path: Path,
    schema_root: Path,
    cache: dict[Path, Any],
    ref_chain: set[tuple[Path, str]],
    errors: list[str],
) -> None:
    if schema is True:
        return
    if schema is False:
        errors.append(f"{path}: value is disallowed by the schema")
        return
    if not isinstance(schema, dict):
        errors.append(f"{path}: schema node is not an object or boolean")
        return

    unsupported = sorted(set(schema) - _SUPPORTED_SCHEMA_KEYS)
    if unsupported:
        errors.append(f"{path}: schema uses unsupported keyword(s): {', '.join(unsupported)}")
        return

    reference = schema.get("$ref")
    if reference is not None:
        try:
            target, target_path = _resolve_json_schema_ref(
                reference,
                current_path=schema_path,
                schema_root=schema_root,
                cache=cache,
            )
        except WorkflowDataError as exc:
            errors.append(f"{path}: {exc}")
            return
        marker = (target_path, str(reference))
        if marker in ref_chain:
            errors.append(f"{path}: JSON Schema $ref cycle detected: {reference}")
            return
        _validate_json_schema_node(
            target,
            value,
            path=path,
            schema_path=target_path,
            schema_root=schema_root,
            cache=cache,
            ref_chain=ref_chain | {marker},
            errors=errors,
        )

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = [expected_type] if isinstance(expected_type, str) else expected_type
        if (
            not isinstance(expected_types, list)
            or not expected_types
            or any(not isinstance(item, str) or item not in _SUPPORTED_JSON_TYPES for item in expected_types)
        ):
            errors.append(f"{path}: schema type must be a supported JSON type or non-empty array of types")
            return
        if not any(_schema_type_matches(value, item) for item in expected_types):
            errors.append(f"{path}: expected type {' or '.join(expected_types)}")
            return

    if "const" in schema and not _schema_json_equal(value, schema["const"]):
        errors.append(f"{path}: must equal {json.dumps(schema['const'], ensure_ascii=False)}")
    if "enum" in schema:
        choices = schema["enum"]
        if not isinstance(choices, list) or not choices:
            errors.append(f"{path}: schema enum must be a non-empty array")
            return
        if not any(_schema_json_equal(value, item) for item in choices):
            errors.append(f"{path}: must be one of {json.dumps(choices, ensure_ascii=False)}")

    if "minLength" in schema:
        minimum_length = schema["minLength"]
        if not isinstance(minimum_length, int) or isinstance(minimum_length, bool) or minimum_length < 0:
            errors.append(f"{path}: schema minLength must be a non-negative integer")
            return
        if isinstance(value, str) and len(value) < minimum_length:
            errors.append(f"{path}: length must be at least {minimum_length}")
    if "minimum" in schema:
        minimum = schema["minimum"]
        if not isinstance(minimum, (int, float)) or isinstance(minimum, bool):
            errors.append(f"{path}: schema minimum must be a number")
            return
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < minimum:
            errors.append(f"{path}: must be at least {minimum}")
    if "pattern" in schema:
        pattern = schema["pattern"]
        if not isinstance(pattern, str):
            errors.append(f"{path}: schema pattern must be a string")
            return
        try:
            matches = not isinstance(value, str) or re.search(pattern, value) is not None
        except re.error as exc:
            errors.append(f"{path}: schema pattern is invalid: {exc}")
            return
        if not matches:
            errors.append(f"{path}: must match pattern {pattern!r}")

    if isinstance(value, list):
        if "minItems" in schema:
            minimum_items = schema["minItems"]
            if not isinstance(minimum_items, int) or isinstance(minimum_items, bool) or minimum_items < 0:
                errors.append(f"{path}: schema minItems must be a non-negative integer")
                return
            if len(value) < minimum_items:
                errors.append(f"{path}: must contain at least {minimum_items} item(s)")
        if "items" in schema:
            item_schema = schema["items"]
            for index, item in enumerate(value):
                _validate_json_schema_node(
                    item_schema,
                    item,
                    path=f"{path}[{index}]",
                    schema_path=schema_path,
                    schema_root=schema_root,
                    cache=cache,
                    ref_chain=ref_chain,
                    errors=errors,
                )

    if isinstance(value, dict):
        if "required" in schema:
            required = schema["required"]
            if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
                errors.append(f"{path}: schema required must be an array of property names")
                return
            for key in required:
                if key not in value:
                    errors.append(f"{_schema_path(path, key)}: required property is missing")
        if "properties" in schema:
            properties = schema["properties"]
            if not isinstance(properties, dict):
                errors.append(f"{path}: schema properties must be an object")
                return
            for key, child_schema in properties.items():
                if not isinstance(key, str):
                    errors.append(f"{path}: schema property names must be strings")
                    return
                if key in value:
                    _validate_json_schema_node(
                        child_schema,
                        value[key],
                        path=_schema_path(path, key),
                        schema_path=schema_path,
                        schema_root=schema_root,
                        cache=cache,
                        ref_chain=ref_chain,
                        errors=errors,
                    )


def validate_json_schema(schema_path: Path, payload: Any, *, label: str) -> None:
    """Validate a workflow payload with the portable JSON Schema subset we ship.

    The schemas are a structural, fail-closed gate.  Keeping this subset in the
    standard library makes the package portable to a fresh Python installation.
    """

    resolved = schema_path.resolve()
    schema_root = resolved.parent
    cache: dict[Path, Any] = {}
    schema = _load_json_schema(resolved, schema_root, cache)
    _validate_json_schema_definition(
        schema,
        schema_path=resolved,
        schema_root=schema_root,
        cache=cache,
        ref_chain=set(),
        location="$",
    )
    errors: list[str] = []
    _validate_json_schema_node(
        schema,
        payload,
        path="$",
        schema_path=resolved,
        schema_root=schema_root,
        cache=cache,
        ref_chain=set(),
        errors=errors,
    )
    if errors:
        details = "\n".join(f"- {item}" for item in sorted(dict.fromkeys(errors))[:24])
        suffix = "\n- additional validation errors omitted" if len(set(errors)) > 24 else ""
        raise WorkflowDataError(f"{label} violates JSON Schema {resolved.name}:\n{details}{suffix}")


def validate_workflow_schema(
    paths: WorkflowPaths,
    schema_name: str,
    payload: Any,
    *,
    label: str,
) -> None:
    if Path(schema_name).name != schema_name:
        raise WorkflowDataError(f"Workflow schema name is unsafe: {schema_name}")
    schemas = paths.tracked("schemas").resolve()
    schema_path = (schemas / schema_name).resolve()
    if schemas not in schema_path.parents or not schema_path.is_file():
        raise WorkflowDataError(f"Workflow JSON Schema is unavailable: {schema_name}")
    validate_json_schema(schema_path, payload, label=label)


def normalize_markdown(value: str) -> str:
    value = value.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", value).rstrip("\n") + "\n"


@dataclass(frozen=True)
class RequirementsBrief:
    path: Path
    metadata: dict[str, Any]
    markdown: str
    fingerprint: str


def read_requirements_brief(
    path: Path,
    *,
    schema_path: Path | None = None,
) -> RequirementsBrief:
    source = path.read_text(encoding="utf-8-sig")
    if source.count(REQUIREMENTS_START) != 1 or source.count(REQUIREMENTS_END) != 1:
        raise WorkflowDataError("Requirements brief must contain one JSON marker pair.")
    before, rest = source.split(REQUIREMENTS_START, 1)
    raw_json, markdown = rest.split(REQUIREMENTS_END, 1)
    if before.strip():
        raise WorkflowDataError("Requirements JSON marker must be the first brief content.")
    try:
        metadata = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise WorkflowDataError(f"Requirements metadata is invalid JSON: {exc}") from exc
    if not isinstance(metadata, dict):
        raise WorkflowDataError("Requirements metadata root must be an object.")
    if schema_path is not None:
        validate_json_schema(schema_path, metadata, label="Requirements brief metadata")
    fingerprint_payload = {
        key: metadata.get(key)
        for key in ("schema_version", "brief_id", "revision", "target_release", "requirements")
    }
    digest = hashlib.sha256(
        canonical_json_bytes(fingerprint_payload)
        + b"\0"
        + normalize_markdown(markdown).encode("utf-8")
    ).hexdigest()
    return RequirementsBrief(path, metadata, normalize_markdown(markdown), digest)


def git(paths: WorkflowPaths, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(paths.root), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise WorkflowDataError(detail or f"Git failed: {' '.join(arguments)}")
    return result


def rev_parse(paths: WorkflowPaths, reference: str) -> str:
    value = git(paths, "rev-parse", "--verify", reference).stdout.strip()
    if not HEX_OID.fullmatch(value):
        raise WorkflowDataError(f"Git reference did not resolve to an object ID: {reference}")
    return value


def is_ancestor(paths: WorkflowPaths, ancestor: str, descendant: str) -> bool:
    result = git(paths, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    if result.returncode not in {0, 1}:
        raise WorkflowDataError((result.stderr or result.stdout).strip())
    return result.returncode == 0


def current_branch(paths: WorkflowPaths) -> str | None:
    result = git(paths, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def status_paths(paths: WorkflowPaths) -> list[str]:
    raw = subprocess.run(
        ["git", "-C", str(paths.root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True,
        check=True,
    ).stdout
    entries = [item for item in raw.split(b"\0") if item]
    changed: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index].decode("utf-8", errors="surrogateescape")
        path = entry[3:]
        if entry[:2] in {"R ", "C ", " R", " C"} and index + 1 < len(entries):
            changed.append(entries[index + 1].decode("utf-8", errors="surrogateescape").replace("\\", "/"))
            index += 1
        changed.append(path.replace("\\", "/"))
        index += 1
    return sorted(set(changed))


def allowed_path(path: str, patterns: Iterable[str]) -> bool:
    normalized = normalize_repo_path(path)
    for raw in patterns:
        pattern = normalize_repo_path(raw)
        if fnmatch.fnmatchcase(normalized, pattern):
            return True
        prefix = pattern.removesuffix("/**").rstrip("/")
        if pattern.endswith("/**") and (normalized == prefix or normalized.startswith(prefix + "/")):
            return True
    return False


def _symlink_target(paths: WorkflowPaths, oid: str, *, path: str, side: str) -> str:
    if not HEX_OID.fullmatch(oid):
        raise WorkflowDataError(f"Symlink {side} object ID is invalid for {path}.")
    try:
        result = subprocess.run(
            ["git", "-C", str(paths.root), "cat-file", "blob", oid],
            capture_output=True,
        )
    except OSError as exc:
        raise WorkflowDataError(f"Unable to read symlink {side} target for {path}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise WorkflowDataError(
            f"Unable to read symlink {side} target for {path}: {detail or oid}"
        )
    if b"\0" in result.stdout:
        raise WorkflowDataError(f"Symlink {side} target contains a NUL byte: {path}")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowDataError(
            f"Symlink {side} target is not valid UTF-8: {path}"
        ) from exc


def _raw_delta(paths: WorkflowPaths, base: str, target: str) -> list[dict[str, Any]]:
    output = subprocess.run(
        [
            "git", "-C", str(paths.root), "diff-tree", "-r", "--raw", "-z",
            "--no-renames", "--full-index", "--no-commit-id", base, target,
        ],
        capture_output=True,
        check=True,
    ).stdout
    items = output.split(b"\0")
    records: list[dict[str, Any]] = []
    index = 0
    while index < len(items):
        header = items[index]
        index += 1
        if not header:
            continue
        if index >= len(items):
            raise WorkflowDataError("Malformed raw Git delta.")
        path = items[index].decode("utf-8", errors="surrogateescape").replace("\\", "/")
        index += 1
        fields = header.decode("ascii").split()
        if len(fields) != 5 or not fields[0].startswith(":"):
            raise WorkflowDataError("Malformed raw Git delta header.")
        entry: dict[str, Any] = {
            "status": fields[4][0],
            "old_path": path,
            "new_path": path,
            "old_mode": fields[0][1:],
            "new_mode": fields[1],
            "old_oid": fields[2],
            "new_oid": fields[3],
        }
        if entry["old_mode"] == "120000":
            entry["old_symlink_target"] = _symlink_target(
                paths,
                entry["old_oid"],
                path=path,
                side="old",
            )
        if entry["new_mode"] == "120000":
            entry["new_symlink_target"] = _symlink_target(
                paths,
                entry["new_oid"],
                path=path,
                side="new",
            )
        records.append(entry)
    return sorted(records, key=lambda row: (row["new_path"], row["old_path"], row["status"]))


def canonical_delivery(paths: WorkflowPaths, base: str, target: str) -> dict[str, Any]:
    base_oid = rev_parse(paths, base)
    target_oid = rev_parse(paths, target)
    entries = [
        entry
        for entry in _raw_delta(paths, base_oid, target_oid)
        if not is_mutable_control_path(entry["new_path"])
        and not is_mutable_control_path(entry["old_path"])
    ]
    patch_hash = sha256_json({"algorithm": "codex-delta-v1", "entries": entries})
    delivery_hash = sha256_json(
        {"algorithm": "codex-delta-v1", "base_commit": base_oid, "entries": entries}
    )
    return {
        "base_commit": base_oid,
        "target_commit": target_oid,
        "entries": entries,
        "changed_paths": sorted({row["new_path"] for row in entries}),
        "patch_hash": patch_hash,
        "delivery_hash": delivery_hash,
    }


def is_mutable_control_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized == ".codex-workflow/governance/PLAN.md" or normalized == ".codex-workflow/state/MVP_BACKLOG.md" or normalized.startswith(
        ".codex-workflow/state/runs/"
    ) or normalized.startswith(".codex-workflow/state/plans/")


def read_embedded_json(text: str, marker_name: str) -> dict[str, Any]:
    start = f"<!-- {marker_name}_START -->"
    end = f"<!-- {marker_name}_END -->"
    if text.count(start) != 1 or text.count(end) != 1:
        raise WorkflowDataError(f"Expected one {marker_name} marker pair.")
    raw = text.split(start, 1)[1].split(end, 1)[0]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkflowDataError(f"Invalid {marker_name} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowDataError(f"{marker_name} JSON must be an object.")
    return payload


def snapshot_id(record: dict[str, Any], delivery_hash: str) -> str:
    lane = record.get("lane") or {}
    return sha256_json(
        {
            "task_id": record.get("task_id"),
            "lane_id": lane.get("lane_id"),
            "base_commit": record.get("base_commit") or lane.get("base_commit"),
            "branch": lane.get("branch"),
            "delivery_hash": delivery_hash,
        }
    )


def load_record(paths: WorkflowPaths, relative: str | Path) -> tuple[Path, dict[str, Any]]:
    raw = str(relative).replace("\\", "/")
    normalized = normalize_repo_path(raw)
    candidate = (paths.root / Path(*PurePosixPath(normalized).parts)).resolve()
    runs = paths.tracked("runs").resolve()
    if runs not in candidate.parents or not candidate.is_file():
        raise WorkflowDataError("Task record must be an existing file under the configured runs directory.")
    try:
        record = json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowDataError(f"Task record is invalid JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise WorkflowDataError("Task record root must be an object.")
    # V2 records remain readable for historical gate compatibility.  Every
    # other record must satisfy the V3 structural contract before any caller
    # can inspect or mutate it.
    if record.get("version") != 2:
        validate_workflow_schema(
            paths,
            "task-record-v3.schema.json",
            record,
            label="Task record",
        )
    return candidate, record


def split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in stripped[1:]:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if current:
        cells.append("".join(current).strip())
    return cells


def backlog_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        cells = split_markdown_row(line)
        if len(cells) < 9 or not re.fullmatch(r"(?:MVP|OPS)-[A-Za-z0-9._-]+", cells[0]):
            continue
        rows.append(
            {
                "line": index,
                "id": cells[0],
                "dependencies": [] if cells[3] in {"", "无", "-"} else [item.strip() for item in re.split(r"[,，]", cells[3]) if item.strip()],
                "status": cells[6],
                "blocking_type": cells[7],
                "cells": cells,
            }
        )
    return rows


def local_bootstrap_policy_gate(
    integration_policy: Any,
    backlog_text: str,
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Validate local bootstrap policy and, for a task, require it to be active."""

    if not isinstance(integration_policy, dict):
        raise WorkflowDataError("layout integration_policy must be an object.")
    local = integration_policy.get("local_bootstrap")
    if not isinstance(local, dict):
        raise WorkflowDataError("integration_policy.local_bootstrap must be an object.")

    enabled = local.get("enabled")
    if not isinstance(enabled, bool):
        raise WorkflowDataError("local_bootstrap.enabled must be boolean.")
    allowed_value = local.get("allowed_task_ids")
    if not isinstance(allowed_value, list):
        raise WorkflowDataError("local_bootstrap.allowed_task_ids must be an array.")
    if any(not isinstance(item, str) or not item.strip() for item in allowed_value):
        raise WorkflowDataError("local_bootstrap.allowed_task_ids must contain non-empty task IDs.")
    allowed = [item.strip() for item in allowed_value]
    if len(allowed) != len(set(allowed)):
        raise WorkflowDataError("local_bootstrap.allowed_task_ids must be unique and ordered.")

    expires_after = local.get("expires_after_task")
    if expires_after is not None and (
        not isinstance(expires_after, str) or not expires_after.strip()
    ):
        raise WorkflowDataError("local_bootstrap.expires_after_task must be null or a non-empty task ID.")
    if isinstance(expires_after, str):
        expires_after = expires_after.strip()

    require_ff_only = local.get("require_ff_only")
    if not isinstance(require_ff_only, bool):
        raise WorkflowDataError("local_bootstrap.require_ff_only must be boolean.")

    state = {
        "enabled": enabled,
        "allowed_task_ids": allowed,
        "expires_after_task": expires_after,
        "expired": False,
    }
    if not enabled:
        if task_id is not None:
            raise WorkflowDataError("Local bootstrap is disabled by policy.")
        return state
    if not allowed:
        raise WorkflowDataError("Enabled local_bootstrap requires a non-empty allowed_task_ids array.")
    if expires_after is None:
        raise WorkflowDataError("Enabled local_bootstrap requires expires_after_task.")
    if expires_after not in allowed:
        raise WorkflowDataError(
            "local_bootstrap.expires_after_task must be present in allowed_task_ids."
        )
    if require_ff_only is not True:
        raise WorkflowDataError("Enabled local_bootstrap requires require_ff_only=true.")

    cutoff_rows = [row for row in backlog_rows(backlog_text) if row["id"] == expires_after]
    if len(cutoff_rows) != 1:
        raise WorkflowDataError(
            f"local_bootstrap.expires_after_task {expires_after} must identify exactly one Backlog task."
        )
    cutoff_status = cutoff_rows[0]["status"]
    if cutoff_status == "removed":
        raise WorkflowDataError(
            f"local_bootstrap.expires_after_task {expires_after} is removed from Backlog."
        )
    state["expired"] = cutoff_status == "done"

    if task_id is None:
        return state
    if task_id not in allowed:
        raise WorkflowDataError(f"Task {task_id} is not in the local bootstrap allowlist.")
    if allowed.index(task_id) > allowed.index(expires_after):
        raise WorkflowDataError(
            f"Task {task_id} is after local_bootstrap.expires_after_task {expires_after}."
        )
    if state["expired"]:
        raise WorkflowDataError(
            f"Local bootstrap policy expired after {expires_after} reached Backlog done."
        )
    return state


def update_backlog_status(text: str, task_id: str, status: str, *, integration: str | None = None) -> str:
    lines = text.splitlines()
    matches = [row for row in backlog_rows(text) if row["id"] == task_id]
    if len(matches) != 1:
        raise WorkflowDataError(f"Backlog must contain exactly one row for {task_id}.")
    row = matches[0]
    cells = row["cells"]
    cells[6] = status
    if integration is not None and len(cells) >= 11:
        cells[10] = integration
    lines[row["line"]] = "| " + " | ".join(cells) + " |"
    return "\n".join(lines).rstrip("\n") + "\n"


def unlock_ready_dependencies(text: str) -> tuple[str, list[str]]:
    rows = backlog_rows(text)
    by_id = {row["id"]: row for row in rows}
    unlocked: list[str] = []
    for row in rows:
        if row["status"] != "blocked" or row["blocking_type"] != "dependencies":
            continue
        dependencies = row["dependencies"]
        if dependencies and all(
            dependency in by_id and by_id[dependency]["status"] == "done"
            for dependency in dependencies
        ):
            cells = row["cells"]
            cells[6] = "ready"
            cells[7] = "none"
            text_lines = text.splitlines()
            text_lines[row["line"]] = "| " + " | ".join(cells) + " |"
            text = "\n".join(text_lines).rstrip("\n") + "\n"
            unlocked.append(row["id"])
            rows = backlog_rows(text)
            by_id = {item["id"]: item for item in rows}
    return text, unlocked


def closeout_state_fingerprint(record: dict[str, Any], backlog_text: str) -> str:
    integration = record.get("integration") or {}
    state = {
        "task_id": record.get("task_id"),
        "integration": {
            key: integration.get(key)
            for key in (
                "status", "mode", "policy_id", "source_ref", "target_ref",
                "target_parent", "pr_head_commit", "result_commit", "merge_strategy",
                "queue_id", "closeout_commit", "pr_url", "ci_checks", "evidence",
            )
        },
        "backlog": normalize_markdown(backlog_text),
    }
    return sha256_json(state)
