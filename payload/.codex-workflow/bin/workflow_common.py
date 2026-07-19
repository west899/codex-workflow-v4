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

from workflow_paths import (
    WorkflowPathError,
    WorkflowPaths,
    atomic_write_text,
    normalize_repo_path,
    parse_bounded_json,
    resolve_path,
)


REQUIREMENTS_START = "<!-- CODEX_REQUIREMENTS_JSON_START -->"
REQUIREMENTS_END = "<!-- CODEX_REQUIREMENTS_JSON_END -->"
REQUIREMENTS_BASELINE_MARKER = "CODEX_REQUIREMENTS_BASELINE"
STATUS_MARKER = "CODEX_WORKFLOW_STATUS_JSON"
HEX_OID = re.compile(r"^[0-9a-f]{40,64}$")
REQUIREMENT_ID = re.compile(r"\bREQ-[A-Za-z0-9._-]+\b")


class WorkflowDataError(ValueError):
    """Tracked workflow data is missing, inconsistent, or unsafe."""


class WorkflowJSONError(WorkflowDataError):
    """A JSON resource failed inside the standard-library parser."""


def parse_json_resource(text: str, *, label: str) -> Any:
    """Parse one JSON resource and classify failures at the parser boundary."""

    try:
        return parse_bounded_json(text)
    except (ValueError, RecursionError) as exc:
        raise WorkflowJSONError(f"{label} is invalid JSON: {exc}") from exc


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
    resolved = resolve_path(path, label="JSON Schema path")
    if schema_root != resolved.parent and schema_root not in resolved.parents:
        raise WorkflowDataError(f"JSON Schema reference escapes the schemas directory: {path}")
    if resolved in cache:
        return cache[resolved]
    try:
        payload = parse_json_resource(
            resolved.read_text(encoding="utf-8"),
            label=f"JSON Schema {resolved.name}",
        )
    except WorkflowJSONError:
        raise
    except (OSError, WorkflowDataError) as exc:
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
    target_path = (
        current_path
        if not document
        else resolve_path(current_path.parent / document, label="JSON Schema reference")
    )
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

    resolved = resolve_path(schema_path, label="workflow schema")
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
    schemas = resolve_path(paths.tracked("schemas"), label="schemas directory")
    schema_path = resolve_path(schemas / schema_name, label="workflow schema")
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


def parse_requirements_brief(
    source: str,
    path: Path,
    *,
    schema_path: Path | None = None,
) -> RequirementsBrief:
    if source.count(REQUIREMENTS_START) != 1 or source.count(REQUIREMENTS_END) != 1:
        raise WorkflowDataError("Requirements brief must contain one JSON marker pair.")
    before, rest = source.split(REQUIREMENTS_START, 1)
    raw_json, markdown = rest.split(REQUIREMENTS_END, 1)
    if before.strip():
        raise WorkflowDataError("Requirements JSON marker must be the first brief content.")
    try:
        metadata = parse_json_resource(raw_json, label="Requirements metadata")
    except WorkflowDataError:
        raise
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


def read_requirements_brief(
    path: Path,
    *,
    schema_path: Path | None = None,
) -> RequirementsBrief:
    return parse_requirements_brief(
        path.read_text(encoding="utf-8-sig"),
        path,
        schema_path=schema_path,
    )


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
    return normalized == ".codex-workflow/governance/PLAN.md" or normalized == ".codex-workflow/state/MVP_BACKLOG.md" or normalized == ".codex-workflow/state/STATUS.md" or normalized.startswith(
        ".codex-workflow/state/requirements-impacts/"
    ) or normalized.startswith(".codex-workflow/state/runs/") or normalized.startswith(
        ".codex-workflow/state/plans/"
    )


def read_embedded_json(text: str, marker_name: str) -> dict[str, Any]:
    start = f"<!-- {marker_name}_START -->"
    end = f"<!-- {marker_name}_END -->"
    if text.count(start) != 1 or text.count(end) != 1:
        raise WorkflowDataError(f"Expected one {marker_name} marker pair.")
    raw = text.split(start, 1)[1].split(end, 1)[0]
    try:
        payload = parse_json_resource(raw, label=marker_name)
    except WorkflowDataError:
        raise
    if not isinstance(payload, dict):
        raise WorkflowDataError(f"{marker_name} JSON must be an object.")
    return payload


def replace_embedded_json(text: str, marker_name: str, payload: dict[str, Any]) -> str:
    """Replace one managed JSON marker block without touching surrounding prose."""

    start = f"<!-- {marker_name}_START -->"
    end = f"<!-- {marker_name}_END -->"
    if text.count(start) != 1 or text.count(end) != 1:
        raise WorkflowDataError(f"Expected one {marker_name} marker pair.")
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    return (
        before
        + start
        + "\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
        + end
        + after
    )


def _baseline_fields(payload: dict[str, Any], *, label: str) -> dict[str, Any]:
    fields = {key: payload.get(key) for key in ("brief_id", "revision", "approval_fingerprint")}
    if all(value is None for value in fields.values()):
        return fields
    if (
        not isinstance(fields["brief_id"], str)
        or not fields["brief_id"].strip()
        or not isinstance(fields["revision"], int)
        or isinstance(fields["revision"], bool)
        or fields["revision"] < 1
        or not isinstance(fields["approval_fingerprint"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", fields["approval_fingerprint"])
    ):
        raise WorkflowDataError(f"{label} requirements baseline is incomplete or invalid.")
    return fields


def requirements_baseline(paths: WorkflowPaths) -> dict[str, Any]:
    values: list[tuple[str, dict[str, Any]]] = []
    for key in ("project", "backlog"):
        path = paths.tracked(key)
        try:
            payload = read_embedded_json(
                path.read_text(encoding="utf-8"),
                REQUIREMENTS_BASELINE_MARKER,
            )
        except OSError as exc:
            raise WorkflowDataError(f"Unable to read {key} requirements baseline: {exc}") from exc
        values.append((key, _baseline_fields(payload, label=key)))
    project, backlog = values[0][1], values[1][1]
    if project != backlog:
        raise WorkflowDataError("PROJECT and Backlog requirements baselines do not match.")
    return dict(backlog)


def current_requirements_baseline(
    paths: WorkflowPaths,
) -> tuple[dict[str, Any], dict[str, Any] | None, Path | None]:
    """Return the managed baseline and the currently approved Brief baseline.

    A populated PROJECT/Backlog baseline names exactly one live Brief.  Keeping
    this comparison here lets every caller fail closed before it performs work
    against a silently edited or superseded Requirements contract.
    """

    baseline = requirements_baseline(paths)
    if all(value is None for value in baseline.values()):
        return baseline, None, None
    brief_id = baseline.get("brief_id")
    if not isinstance(brief_id, str) or not brief_id:
        raise WorkflowDataError("Configured Requirements baseline has no Brief ID.")
    brief_path = paths.tracked("requirements") / f"{brief_id}.md"
    if not brief_path.is_file():
        raise WorkflowDataError(
            "Configured Requirements Brief is missing: " + paths.relative(brief_path)
        )
    brief = read_requirements_brief(
        brief_path,
        schema_path=paths.tracked("schemas") / "requirements-v1.schema.json",
    )
    return baseline, requirements_baseline_from_brief(brief), brief_path


def requirements_baseline_from_brief(brief: RequirementsBrief) -> dict[str, Any]:
    metadata = brief.metadata
    approval = metadata.get("approval")
    if metadata.get("status") != "approved" or not isinstance(approval, dict):
        raise WorkflowDataError("Requirements impact requires an approved Requirements Brief.")
    baseline = _baseline_fields(
        {
            "brief_id": metadata.get("brief_id"),
            "revision": metadata.get("revision"),
            "approval_fingerprint": approval.get("approved_fingerprint"),
        },
        label="Requirements Brief",
    )
    if baseline["approval_fingerprint"] != brief.fingerprint:
        raise WorkflowDataError("Requirements impact requires a current approval fingerprint.")
    return baseline


def _historical_requirements_brief(
    paths: WorkflowPaths,
    current_path: Path,
    baseline: dict[str, Any],
) -> RequirementsBrief | None:
    previous_brief_id = baseline.get("brief_id")
    if not isinstance(previous_brief_id, str) or not previous_brief_id:
        return None
    schema_path = paths.tracked("schemas") / "requirements-v1.schema.json"
    historical_path = paths.tracked("requirements") / f"{previous_brief_id}.md"
    candidates = [current_path]
    if resolve_path(historical_path, label="historical Requirements path") != resolve_path(
        current_path, label="current Requirements path"
    ):
        candidates.append(historical_path)
    for candidate_path in candidates:
        relative = paths.relative(candidate_path)
        history = git(paths, "log", "--format=%H", "--all", "--", relative, check=False)
        if history.returncode != 0:
            raise WorkflowDataError((history.stderr or history.stdout).strip())
        for commit in (line.strip() for line in history.stdout.splitlines()):
            if not HEX_OID.fullmatch(commit):
                continue
            source = git(paths, "show", f"{commit}:{relative}", check=False)
            if source.returncode != 0:
                continue
            try:
                candidate = parse_requirements_brief(
                    source.stdout,
                    candidate_path,
                    schema_path=schema_path,
                )
                candidate_baseline = requirements_baseline_from_brief(candidate)
            except WorkflowDataError:
                continue
            if candidate_baseline == baseline:
                return candidate
    return None


def _requirements_entities(brief: RequirementsBrief) -> dict[str, dict[str, Any]]:
    requirements = brief.metadata.get("requirements")
    if not isinstance(requirements, dict):
        raise WorkflowDataError("Requirements impact requires a requirements object.")
    entities: dict[str, dict[str, Any]] = {
        "$scope": {"group": "scope", "value": requirements.get("scope")},
        "$constraints": {"group": "constraints", "value": requirements.get("constraints")},
    }
    for group in (
        "users",
        "outcomes",
        "flows",
        "capabilities",
        "scenarios",
        "non_goals",
        "open_questions",
    ):
        values = requirements.get(group)
        if not isinstance(values, list):
            raise WorkflowDataError(f"Requirements impact requires requirements.{group} to be an array.")
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
                raise WorkflowDataError(f"Requirements impact requires stable IDs in requirements.{group}.")
            item_id = item["id"].strip()
            if item_id in entities:
                raise WorkflowDataError(f"Requirements impact found duplicate stable ID: {item_id}.")
            entities[item_id] = {"group": group, "value": item}
    return entities


def _requirements_changes(previous: RequirementsBrief, current: RequirementsBrief) -> list[dict[str, str]]:
    before = _requirements_entities(previous)
    after = _requirements_entities(current)
    changes: list[dict[str, str]] = []
    for item_id in sorted(set(before) | set(after)):
        old = before.get(item_id)
        new = after.get(item_id)
        if old is None:
            changes.append({"id": item_id, "kind": "added", "group": str(new["group"])})
        elif new is None:
            changes.append({"id": item_id, "kind": "removed", "group": str(old["group"])})
        elif canonical_json_bytes(old["value"]) != canonical_json_bytes(new["value"]):
            changes.append({"id": item_id, "kind": "modified", "group": str(new["group"])})
    if previous.markdown != current.markdown:
        changes.append({"id": "$markdown", "kind": "modified", "group": "markdown"})
    for key in ("brief_id", "target_release"):
        if previous.metadata.get(key) != current.metadata.get(key):
            changes.append({"id": f"${key}", "kind": "modified", "group": "metadata"})
    if not changes and previous.metadata.get("revision") != current.metadata.get("revision"):
        changes.append({"id": "$revision", "kind": "modified", "group": "metadata"})
    return sorted(changes, key=lambda item: (item["id"], item["kind"], item["group"]))


def _impact_records(paths: WorkflowPaths, brief_id: str) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}

    def add_record(
        record: dict[str, Any],
        record_path: str,
        *,
        runtime_lane_id: str | None = None,
    ) -> None:
        if not isinstance(record, dict) or not isinstance(record.get("task_id"), str):
            raise WorkflowDataError(f"Requirements impact found invalid task record: {record_path}")
        source = record.get("source")
        baseline = source.get("requirements_baseline") if isinstance(source, dict) else None
        if not (
            isinstance(source, dict)
            and source.get("type") == "mvp_backlog"
            and isinstance(baseline, dict)
            and baseline.get("brief_id") == brief_id
        ):
            return
        entry = {"path": record_path, "record": record}
        if runtime_lane_id is not None:
            entry["runtime_lane_id"] = runtime_lane_id
        existing = records.get(record["task_id"])
        if existing is not None and runtime_lane_id is None:
            return
        if existing is not None and runtime_lane_id is not None:
            # The lane worktree holds the live record; the coordinator branch
            # may still contain its pre-claim copy.
            records[record["task_id"]] = entry
            return
        records[record["task_id"]] = entry

    runs = paths.tracked("runs")
    if runs.exists():
        for path in sorted(runs.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowDataError(f"Requirements impact refuses unreadable task record {path.name}: {exc}") from exc
            add_record(record, paths.relative(path))

    registry_root = paths.shared_runtime / "registry" / "lanes"
    if not registry_root.exists():
        return records
    for registry_path in sorted(registry_root.glob("*.json")):
        try:
            lane = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowDataError(f"Requirements impact refuses unreadable lane registry {registry_path.name}: {exc}") from exc
        if not isinstance(lane, dict):
            raise WorkflowDataError(f"Requirements impact found invalid lane registry: {registry_path.name}")
        task_id = lane.get("task_id")
        lane_id = lane.get("lane_id")
        worktree_value = lane.get("worktree")
        record_value = lane.get("record")
        if not all(isinstance(value, str) and value for value in (task_id, lane_id, worktree_value, record_value)):
            raise WorkflowDataError(f"Requirements impact lane registry is incomplete: {registry_path.name}")
        worktree = Path(worktree_value)
        if not worktree.is_dir():
            raise WorkflowDataError(
                "Requirements impact cannot inspect live lane worktree for "
                f"{task_id}; rebuild or recover its registry before changing requirements."
            )
        try:
            lane_paths = WorkflowPaths.discover(worktree)
            _, record = load_record(lane_paths, record_value)
        except (WorkflowDataError, WorkflowPathError, OSError) as exc:
            raise WorkflowDataError(
                f"Requirements impact cannot inspect live lane {lane_id}: {exc}"
            ) from exc
        if record.get("task_id") != task_id:
            raise WorkflowDataError(f"Requirements impact lane {lane_id} task ID does not match its record.")
        add_record(record, record_value.replace("\\", "/"), runtime_lane_id=lane_id)
    return records


def _record_is_active(record: dict[str, Any]) -> bool:
    verification = record.get("verification") if isinstance(record.get("verification"), dict) else {}
    integration = record.get("integration") if isinstance(record.get("integration"), dict) else {}
    return (
        record.get("status") in {"in_progress", "completed"}
        or verification.get("status") == "passed"
        or integration.get("status") in {"pending", "queued", "merged_pending_closeout", "integrated"}
    )


def requirements_impact(paths: WorkflowPaths, brief_path: Path) -> dict[str, Any]:
    """Derive a conservative, stable-ID impact report for an approved brief revision."""

    current = read_requirements_brief(
        brief_path,
        schema_path=paths.tracked("schemas") / "requirements-v1.schema.json",
    )
    current_baseline = requirements_baseline_from_brief(current)
    previous_baseline = requirements_baseline(paths)
    common = {
        "schema_version": 1,
        "previous_baseline": previous_baseline,
        "current_baseline": current_baseline,
        "brief": paths.relative(brief_path),
    }
    if all(value is None for value in previous_baseline.values()):
        return {
            **common,
            "status": "initial_baseline",
            "reason": "PROJECT and Backlog do not yet hold an approved baseline.",
            "changes": [],
            "backlog_tasks": [],
            "active_tasks": [],
            "blocked_task_ids": [],
            "analysis_id": sha256_json({**common, "status": "initial_baseline"}),
            "analyzed_at": utc_now(),
        }
    if previous_baseline == current_baseline:
        return {
            **common,
            "status": "no_change",
            "changes": [],
            "backlog_tasks": [],
            "active_tasks": [],
            "blocked_task_ids": [],
            "analysis_id": sha256_json({**common, "status": "no_change"}),
            "analyzed_at": utc_now(),
        }
    previous = _historical_requirements_brief(paths, brief_path, previous_baseline)
    if previous is None:
        return {
            **common,
            "status": "blocked",
            "reason": "The prior approved Requirements Brief is unavailable in Git history; no impact can be safely inferred.",
            "changes": [],
            "backlog_tasks": [],
            "active_tasks": [],
            "blocked_task_ids": [],
            "analysis_id": sha256_json({**common, "status": "blocked"}),
            "analyzed_at": utc_now(),
        }

    changes = _requirements_changes(previous, current)
    changed_ids = {item["id"] for item in changes}
    global_change_ids = {"$scope", "$constraints", "$markdown", "$brief_id", "$target_release"}
    global_change = bool(changed_ids & global_change_ids) or changed_ids == {"$revision"}
    records = _impact_records(paths, str(previous_baseline["brief_id"]))
    backlog_text = paths.tracked("backlog").read_text(encoding="utf-8")
    backlog_tasks: list[dict[str, Any]] = []
    active_tasks: list[dict[str, Any]] = []
    blocked_task_ids: list[str] = []
    for row in backlog_rows(backlog_text):
        task_id = row["id"]
        references = sorted(set(REQUIREMENT_ID.findall(" ".join(row["cells"][2:5]))))
        matches = sorted(set(references) & {item for item in changed_ids if not item.startswith("$")})
        impacted = global_change or bool(matches) or not references
        reason = "global_change" if global_change else "changed_requirement_ids" if matches else "unmapped_requirements" if not references else "unchanged"
        record_entry = records.get(task_id)
        active = record_entry is not None and _record_is_active(record_entry["record"])
        if row["status"] == "done":
            action = "preserve_history"
        elif active and impacted:
            action = "human_decision_required"
        elif impacted:
            action = "block"
            blocked_task_ids.append(task_id)
        else:
            action = "retain"
        entry = {
            "task_id": task_id,
            "durable_status": row["status"],
            "references": references,
            "matched_requirement_ids": matches,
            "impact_reason": reason,
            "action": action,
        }
        if record_entry is not None:
            entry["record"] = record_entry["path"]
            if isinstance(record_entry.get("runtime_lane_id"), str):
                entry["runtime_lane_id"] = record_entry["runtime_lane_id"]
        backlog_tasks.append(entry)
        if action == "human_decision_required":
            active_tasks.append(entry)
    for task_id, record_entry in sorted(records.items()):
        if any(item["task_id"] == task_id for item in backlog_tasks):
            continue
        if _record_is_active(record_entry["record"]):
            entry = {
                "task_id": task_id,
                "record": record_entry["path"],
                "references": [],
                "matched_requirement_ids": [],
                "impact_reason": "missing_backlog_row",
                "action": "human_decision_required",
            }
            if isinstance(record_entry.get("runtime_lane_id"), str):
                entry["runtime_lane_id"] = record_entry["runtime_lane_id"]
            active_tasks.append(entry)
    identity = {
        **common,
        "status": "ready",
        "changes": changes,
        "backlog_tasks": backlog_tasks,
        "active_tasks": active_tasks,
        "blocked_task_ids": sorted(blocked_task_ids),
    }
    return {
        **identity,
        "analysis_id": sha256_json(identity),
        "analyzed_at": utc_now(),
    }


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
    candidate = resolve_path(
        paths.root / Path(*PurePosixPath(normalized).parts),
        label="task record",
    )
    runs = resolve_path(paths.tracked("runs"), label="task runs directory")
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


def block_backlog_for_requirements(
    text: str,
    task_ids: Iterable[str],
    analysis_id: str,
) -> str:
    requested = set(task_ids)
    if not requested:
        return normalize_markdown(text)
    if not re.fullmatch(r"[0-9a-f]{64}", analysis_id):
        raise WorkflowDataError("Requirements impact analysis ID is invalid.")
    rows = {row["id"]: row for row in backlog_rows(text)}
    missing = sorted(requested - set(rows))
    if missing:
        raise WorkflowDataError("Requirements impact references missing Backlog task(s): " + ", ".join(missing))
    lines = text.splitlines()
    marker = f"manual:requirements-impact-{analysis_id[:12]}"
    for task_id in sorted(requested):
        row = rows[task_id]
        if row["status"] == "done":
            continue
        cells = row["cells"]
        cells[6] = "blocked"
        cells[7] = marker
        if len(cells) >= 11:
            cells[10] = f"requirements-impact:{analysis_id}"
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


def requirements_impact_path(paths: WorkflowPaths, brief_id: str, revision: int) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", brief_id) or not isinstance(revision, int) or revision < 1:
        raise WorkflowDataError("Requirements impact report identity is invalid.")
    return paths.tracked("requirements_impacts") / f"{brief_id}-r{revision}.json"


def workflow_status_snapshot(paths: WorkflowPaths) -> dict[str, Any]:
    baseline = requirements_baseline(paths)
    if all(value is None for value in baseline.values()):
        requirements_contract: dict[str, Any] = {
            "status": "not_configured",
            "managed_baseline": baseline,
            "observed_baseline": None,
        }
    else:
        try:
            managed, observed, brief_path = current_requirements_baseline(paths)
            requirements_contract = {
                "status": "current" if observed == managed else "drift",
                "managed_baseline": managed,
                "observed_baseline": observed,
                "brief": paths.relative(brief_path) if brief_path is not None else None,
            }
        except WorkflowDataError as exc:
            requirements_contract = {
                "status": "invalid",
                "managed_baseline": baseline,
                "observed_baseline": None,
                "reason": str(exc),
            }
    backlog_text = paths.tracked("backlog").read_text(encoding="utf-8")
    counts = {status: 0 for status in ("draft", "blocked", "ready", "done", "removed")}
    for row in backlog_rows(backlog_text):
        if row["status"] in counts:
            counts[row["status"]] += 1
    records_by_task: dict[str, dict[str, Any]] = {}

    def add_status_record(
        record: dict[str, Any],
        record_path: str,
        *,
        runtime_lane_id: str | None = None,
    ) -> None:
        if not isinstance(record, dict) or not isinstance(record.get("task_id"), str):
            raise WorkflowDataError(f"Unable to build status from invalid task record: {record_path}")
        verification = record.get("verification") if isinstance(record.get("verification"), dict) else {}
        integration = record.get("integration") if isinstance(record.get("integration"), dict) else {}
        impact = record.get("requirements_impact") if isinstance(record.get("requirements_impact"), dict) else None
        entry = {
            "task_id": record["task_id"],
            "record": record_path,
            "status": record.get("status"),
            "phase": record.get("phase"),
            "verification": verification.get("status"),
            "integration": integration.get("status"),
            "requirements_impact": impact.get("decision") if impact else None,
        }
        if runtime_lane_id is not None:
            entry["runtime_lane_id"] = runtime_lane_id
        # A registered lane is the live copy of its task record; the current
        # coordinator branch can legitimately hold its pre-claim version.
        if runtime_lane_id is not None or record["task_id"] not in records_by_task:
            records_by_task[record["task_id"]] = entry

    runs = paths.tracked("runs")
    if runs.exists():
        for path in sorted(runs.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowDataError(f"Unable to build status from task record {path.name}: {exc}") from exc
            add_status_record(record, paths.relative(path))

    registry_root = paths.shared_runtime / "registry" / "lanes"
    if registry_root.exists():
        for registry_path in sorted(registry_root.glob("*.json")):
            try:
                lane = json.loads(registry_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowDataError(f"Unable to build status from lane registry {registry_path.name}: {exc}") from exc
            if not isinstance(lane, dict):
                raise WorkflowDataError(f"Invalid lane registry for status: {registry_path.name}")
            task_id = lane.get("task_id")
            lane_id = lane.get("lane_id")
            worktree_value = lane.get("worktree")
            record_value = lane.get("record")
            if not all(isinstance(value, str) and value for value in (task_id, lane_id, worktree_value, record_value)):
                raise WorkflowDataError(f"Lane registry is incomplete for status: {registry_path.name}")
            worktree = Path(worktree_value)
            if not worktree.is_dir():
                raise WorkflowDataError(
                    f"Status cannot inspect registered lane {lane_id}; rebuild or recover its registry first."
                )
            try:
                lane_paths = WorkflowPaths.discover(worktree)
                _, record = load_record(lane_paths, record_value)
            except (WorkflowDataError, WorkflowPathError, OSError) as exc:
                raise WorkflowDataError(f"Status cannot inspect registered lane {lane_id}: {exc}") from exc
            if record.get("task_id") != task_id:
                raise WorkflowDataError(f"Lane {lane_id} task ID does not match its task record.")
            add_status_record(record, record_value.replace("\\", "/"), runtime_lane_id=lane_id)
    records = [records_by_task[task_id] for task_id in sorted(records_by_task)]
    impacts: list[dict[str, Any]] = []
    impact_root = paths.tracked("requirements_impacts")
    if impact_root.exists():
        for path in sorted(impact_root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowDataError(f"Unable to build status from impact report {path.name}: {exc}") from exc
            if not isinstance(payload, dict):
                raise WorkflowDataError(f"Requirements impact report must be an object: {path.name}")
            impacts.append(
                {
                    "analysis_id": payload.get("analysis_id"),
                    "brief_id": (payload.get("current_baseline") or {}).get("brief_id"),
                    "revision": (payload.get("current_baseline") or {}).get("revision"),
                    "status": payload.get("status"),
                    "blocked_task_ids": payload.get("blocked_task_ids", []),
                    "active_task_ids": [item.get("task_id") for item in payload.get("active_tasks", []) if isinstance(item, dict)],
                }
            )
    state = {
        "schema_version": 1,
        "requirements_baseline": baseline,
        "requirements_contract": requirements_contract,
        "backlog_counts": counts,
        "task_records": records,
        "requirements_impacts": impacts,
    }
    return {
        **state,
        "status_fingerprint": sha256_json(state),
        "generated_at": utc_now(),
    }


def render_workflow_status(snapshot: dict[str, Any]) -> str:
    baseline = snapshot["requirements_baseline"]
    contract = snapshot["requirements_contract"]
    counts = snapshot["backlog_counts"]
    impacts = snapshot["requirements_impacts"]
    lines = [
        "# Workflow 状态快照",
        "",
        "> 此文件由工作流脚本生成；机器可读状态以 JSON 区块为准，不手工编辑。",
        "",
        f"> 更新时间：{snapshot['generated_at']}｜状态指纹：`{snapshot['status_fingerprint']}`",
        "",
        "## 当前摘要",
        "",
        f"- Requirements：`{baseline.get('brief_id')}` revision `{baseline.get('revision')}`。",
        f"- Requirements contract：{contract.get('status')}。",
        "- Backlog：" + "，".join(f"{key}={counts[key]}" for key in ("draft", "blocked", "ready", "done", "removed")) + "。",
        f"- Task records：{len(snapshot['task_records'])}；Requirements impact reports：{len(impacts)}。",
        "",
        f"<!-- {STATUS_MARKER}_START -->",
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2),
        f"<!-- {STATUS_MARKER}_END -->",
        "",
        "## 使用规则",
        "",
        "- 所有 task、Backlog 与 Requirements 的真实状态来自受管状态文件；本快照只汇总它们。",
        "- Requirements impact 报告出现 active task 时，必须先取得人类决定，再继续该 lane。",
        "- 如果外部 Git 操作或人工编辑改变了受管状态，运行 `workflow_state.py sync-status --apply` 重新生成。",
        "",
    ]
    return "\n".join(lines)


def sync_workflow_status(paths: WorkflowPaths) -> dict[str, Any]:
    snapshot = workflow_status_snapshot(paths)
    atomic_write_text(paths.tracked("status"), render_workflow_status(snapshot))
    return snapshot


def workflow_status_is_current(paths: WorkflowPaths) -> tuple[dict[str, Any], bool]:
    snapshot = workflow_status_snapshot(paths)
    status_path = paths.tracked("status")
    try:
        stored = read_embedded_json(status_path.read_text(encoding="utf-8"), STATUS_MARKER)
    except (OSError, WorkflowDataError):
        return snapshot, False
    return snapshot, stored.get("status_fingerprint") == snapshot["status_fingerprint"]
