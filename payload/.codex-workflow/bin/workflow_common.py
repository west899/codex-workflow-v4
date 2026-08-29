#!/usr/bin/env python3
"""Shared deterministic data and Git helpers for Workflow V3 and Phase A V4."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit

from workflow_paths import (
    JSONResourceLimitError,
    MAX_JSON_INTEGER_DIGITS,
    MAX_JSON_NESTING,
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


class WorkflowJSONError(Exception):
    """Common marker for classified JSON parser failures."""


class WorkflowJSONSyntaxError(WorkflowDataError, WorkflowJSONError):
    """A JSON resource contains ordinary invalid syntax or data."""


class WorkflowJSONResourceError(RuntimeError, WorkflowJSONError):
    """A JSON resource exceeded a deterministic parser resource boundary."""


def parse_json_resource(text: str, *, label: str) -> Any:
    """Parse one JSON resource and classify failures at the parser boundary."""

    try:
        return parse_bounded_json(text)
    except json.JSONDecodeError as exc:
        raise WorkflowJSONSyntaxError(f"{label} is invalid JSON: {exc}") from exc
    except (JSONResourceLimitError, RecursionError) as exc:
        raise WorkflowJSONResourceError(f"{label} is invalid JSON: {exc}") from exc


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


V4_CONTRACT_FINGERPRINT_ALGORITHM = "codex-contract-v1"
V4_DECISION_FINGERPRINT_ALGORITHM = "codex-decision-v1"
V4_DECISION_STATE_FINGERPRINT_ALGORITHM = "codex-decision-state-v1"
V4_OBSERVATION_FINGERPRINT_ALGORITHM = "codex-observation-v1"
V4_OBSERVATION_RECEIPT_FINGERPRINT_ALGORITHM = "codex-observation-receipt-v1"
V4_ARCHITECTURE_FINGERPRINT_VERSION = 1

# This matrix is the M1 contract for later reset/checker work. Empty tuples
# identify lifecycle-only changes that preserve all three semantic identities.
V4_FINGERPRINT_INVALIDATION_MATRIX_V1 = {
    "request": ("contract", "observation"),
    "requirements_baseline": (
        "contract",
        "decision:architecture_decision",
        "decision:product_checkpoint",
        "decision:product_decision",
        "decision:risk_acceptance",
        "observation",
    ),
    "scope": ("contract", "observation"),
    "planning": ("contract",),
    "risk": ("contract",),
    "acceptance": ("contract", "decision:product_checkpoint", "observation"),
    "observation_recipe": ("contract", "decision:product_checkpoint", "observation"),
    "fixture": ("contract", "decision:product_checkpoint", "observation"),
    "public_api_or_schema": ("decision:product_checkpoint", "observation"),
    "dependencies": ("contract", "decision:product_checkpoint", "observation"),
    "architecture": (
        "contract",
        "decision:architecture_decision",
        "decision:product_checkpoint",
        "observation",
    ),
    "decision_identity": (
        "decision:architecture_decision",
        "decision:product_checkpoint",
        "decision:product_decision",
        "decision:risk_acceptance",
    ),
    "decision_blocking_policy": (
        "decision:architecture_decision",
        "decision:product_checkpoint",
        "decision:product_decision",
        "decision:risk_acceptance",
    ),
    "decision_affected_scope": (
        "decision:architecture_decision",
        "decision:product_checkpoint",
        "decision:product_decision",
        "decision:risk_acceptance",
    ),
    "decision_question": (
        "decision:architecture_decision",
        "decision:product_checkpoint",
        "decision:product_decision",
        "decision:risk_acceptance",
    ),
    "decision_options_or_recommendation": (
        "decision:architecture_decision",
        "decision:product_decision",
        "decision:risk_acceptance",
    ),
    "decision_checkpoint_binding_or_observation": ("decision:product_checkpoint",),
    "decision_product_context": ("decision:product_decision",),
    "decision_risk_context": ("decision:risk_acceptance",),
    "resolution_or_deferral": (),
    "delivery_snapshot_or_evidence": ("decision:product_checkpoint",),
}

_V4_SET_LIKE_ARRAY_FIELDS = frozenset(
    {
        "acceptance_ids",
        "affected_scope",
        "allowed_paths",
        "assertions",
        "decision_refs",
        "dependencies",
        "dependency_refs",
        "evidence_refs",
        "excluded_targets",
        "guardrail_ids",
        "guardrails",
        "in",
        "known_limitations",
        "known_placeholders",
        "material_changes",
        "mitigations",
        "notes",
        "observed_surfaces",
        "out",
        "public_api",
        "real_components",
        "requirement_ids",
        "resource_keys",
        "reversible_assumptions",
        "schemas",
        "stable_output",
        "temporary_components",
        "verification_refs",
    }
)
_V4_SET_MEMBER_IDENTITY_FIELDS = {
    "assertions": "id",
    "decision_refs": "decision_id",
    "dependencies": "ref",
    "dependency_refs": "task_id",
    "guardrails": "id",
    "public_api": "ref",
    "schemas": "ref",
    "stable_output": "name",
}


def _canonical_v4_value(value: Any, *, path: str, active: set[int], depth: int) -> Any:
    """Normalize a bounded JSON value for cross-version V4 fingerprints."""

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) >= 10**MAX_JSON_INTEGER_DIGITS:
            raise WorkflowDataError(
                f"{path} exceeds the {MAX_JSON_INTEGER_DIGITS}-digit V4 integer boundary."
            )
        return value
    if isinstance(value, float):
        raise WorkflowDataError(f"{path} uses a floating-point value, which V4 fingerprints reject.")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        if depth >= MAX_JSON_NESTING:
            raise WorkflowDataError(
                f"{path} exceeds the {MAX_JSON_NESTING}-level V4 nesting boundary."
            )
        marker = id(value)
        if marker in active:
            raise WorkflowDataError(f"{path} contains a cyclic JSON array.")
        active.add(marker)
        try:
            return [
                _canonical_v4_value(
                    item,
                    path=f"{path}[{index}]",
                    active=active,
                    depth=depth + 1,
                )
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(marker)
    if isinstance(value, dict):
        if depth >= MAX_JSON_NESTING:
            raise WorkflowDataError(
                f"{path} exceeds the {MAX_JSON_NESTING}-level V4 nesting boundary."
            )
        marker = id(value)
        if marker in active:
            raise WorkflowDataError(f"{path} contains a cyclic JSON object.")
        active.add(marker)
        try:
            normalized: dict[str, Any] = {}
            for raw_key, item in value.items():
                if not isinstance(raw_key, str):
                    raise WorkflowDataError(f"{path} contains a non-text JSON object key.")
                key = unicodedata.normalize("NFC", raw_key)
                if key in normalized:
                    raise WorkflowDataError(
                        f"{path} contains object keys that collide after Unicode normalization: {key!r}."
                    )
                normalized[key] = _canonical_v4_value(
                    item,
                    path=_schema_path(path, key),
                    active=active,
                    depth=depth + 1,
                )
            return normalized
        finally:
            active.remove(marker)
    raise WorkflowDataError(f"{path} contains a non-JSON value of type {type(value).__name__}.")


def canonical_v4_json_bytes(payload: Any) -> bytes:
    """Return the exact canonical UTF-8 representation used by V4 identities."""

    normalized = _canonical_v4_value(payload, path="$", active=set(), depth=0)
    try:
        return json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (UnicodeEncodeError, ValueError) as exc:
        raise WorkflowDataError(f"V4 fingerprint material cannot be encoded: {exc}") from exc


def _normalize_v4_semantic_sets(value: Any, *, path: str) -> Any:
    """Sort domain sets while retaining order for steps, options, and other sequences."""

    if isinstance(value, list):
        return [
            _normalize_v4_semantic_sets(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if not isinstance(value, dict):
        return value
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise WorkflowDataError(f"{path} contains a non-text JSON object key.")
        child_path = _schema_path(path, key)
        child = _normalize_v4_semantic_sets(item, path=child_path)
        if key in _V4_SET_LIKE_ARRAY_FIELDS and isinstance(child, list):
            identity_field = _V4_SET_MEMBER_IDENTITY_FIELDS.get(key)
            encoded: list[tuple[bytes, Any]] = []
            for index, entry in enumerate(child):
                if identity_field is None:
                    identity = canonical_v4_json_bytes(entry)
                else:
                    if not isinstance(entry, dict) or identity_field not in entry:
                        raise WorkflowDataError(
                            f"{child_path}[{index}] lacks set identity field {identity_field!r}."
                        )
                    identity = canonical_v4_json_bytes(entry[identity_field])
                encoded.append((identity, entry))
            identities = [identity for identity, _ in encoded]
            if len(identities) != len(set(identities)):
                identity_label = identity_field or "canonical member"
                raise WorkflowDataError(
                    f"{child_path} contains duplicate set identity {identity_label!r}."
                )
            child = [entry for _, entry in sorted(encoded, key=lambda pair: pair[0])]
        normalized[key] = child
    return normalized


def _v4_fingerprint(algorithm: str, material: dict[str, Any]) -> str:
    normalized = _normalize_v4_semantic_sets(material, path="$.material")
    return hashlib.sha256(
        canonical_v4_json_bytes({"algorithm": algorithm, "material": normalized})
    ).hexdigest()


def _v4_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowDataError(f"{label} must be an object before fingerprinting.")
    return value


def _v4_required(mapping: dict[str, Any], key: str, *, label: str) -> Any:
    if key not in mapping:
        raise WorkflowDataError(f"{label} is missing required fingerprint field {key!r}.")
    return mapping[key]


def _v4_requirements_baseline(record: dict[str, Any]) -> dict[str, Any]:
    source = _v4_object(_v4_required(record, "source", label="V4 task record"), label="V4 source")
    return _v4_object(
        _v4_required(source, "requirements_baseline", label="V4 source"),
        label="V4 Requirements baseline",
    )


def contract_fingerprint_material(record: dict[str, Any]) -> dict[str, Any]:
    """Project the immutable task semantics covered by contract_fingerprint."""

    if record.get("version") != 4:
        raise WorkflowDataError("Contract fingerprints require a V4 task record.")
    acceptance = _v4_required(record, "acceptance", label="V4 task record")
    if not isinstance(acceptance, list) or not acceptance:
        raise WorkflowDataError("V4 acceptance must be a non-empty array before fingerprinting.")
    projected_acceptance: list[dict[str, Any]] = []
    for index, raw in enumerate(acceptance):
        item = _v4_object(raw, label=f"V4 acceptance[{index}]")
        projected_acceptance.append(
            {
                "id": _v4_required(item, "id", label=f"V4 acceptance[{index}]"),
                "criterion": _v4_required(item, "criterion", label=f"V4 acceptance[{index}]"),
            }
        )
    acceptance_ids = [item["id"] for item in projected_acceptance]
    if len(acceptance_ids) != len(set(acceptance_ids)):
        raise WorkflowDataError("V4 acceptance IDs must be unique before fingerprinting.")
    projected_acceptance.sort(key=lambda item: canonical_v4_json_bytes(item))
    return {
        "request": _v4_required(record, "request", label="V4 task record"),
        "requirements_baseline": _v4_requirements_baseline(record),
        "scope": _v4_object(
            _v4_required(record, "scope", label="V4 task record"), label="V4 scope"
        ),
        "planning": _v4_object(
            _v4_required(record, "planning", label="V4 task record"), label="V4 planning"
        ),
        "risk": _v4_object(
            _v4_required(record, "risk", label="V4 task record"), label="V4 risk"
        ),
        "acceptance": projected_acceptance,
        "delivery_contract": _v4_object(
            _v4_required(record, "delivery_contract", label="V4 task record"),
            label="V4 delivery_contract",
        ),
    }


def contract_fingerprint(record: dict[str, Any]) -> str:
    return contract_fingerprint_from_material(contract_fingerprint_material(record))


def contract_fingerprint_from_material(material: dict[str, Any]) -> str:
    """Fingerprint a previously captured V4 contract projection."""

    return _v4_fingerprint(V4_CONTRACT_FINGERPRINT_ALGORITHM, material)


def architecture_baseline_fingerprint(baseline: dict[str, Any]) -> str:
    """Return the approved minimal architecture-baseline identity."""

    baseline = _v4_object(baseline, label="Architecture baseline")
    material = {
        field: _v4_required(baseline, field, label="Architecture baseline")
        for field in ("schema_version", "baseline_id", "revision", "guardrails")
    }
    if material["schema_version"] != V4_ARCHITECTURE_FINGERPRINT_VERSION:
        raise WorkflowDataError("Architecture baseline schema_version must be 1.")
    return hashlib.sha256(canonical_v4_json_bytes(material)).hexdigest()


_V4_DECISION_REQUEST_FIELDS = {
    "product_checkpoint": (
        "id",
        "kind",
        "blocking",
        "affected_scope",
        "latest_decision_point",
        "current_delivery_independent",
        "binding",
        "observation_receipt",
        "observation_fingerprint",
        "question",
    ),
    "product_decision": (
        "id",
        "kind",
        "blocking",
        "affected_scope",
        "latest_decision_point",
        "current_delivery_independent",
        "question",
        "options",
        "recommendation",
        "requirements_baseline",
        "product_context",
    ),
    "architecture_decision": (
        "id",
        "kind",
        "blocking",
        "affected_scope",
        "latest_decision_point",
        "current_delivery_independent",
        "question",
        "options",
        "recommendation",
        "requirements_baseline",
        "architecture_context",
    ),
    "risk_acceptance": (
        "id",
        "kind",
        "blocking",
        "affected_scope",
        "latest_decision_point",
        "current_delivery_independent",
        "question",
        "options",
        "recommendation",
        "requirements_baseline",
        "risk_context",
    ),
}
V4_DECISION_FIELD_INVALIDATION_V1 = {
    "product_checkpoint": {
        "id": "decision_identity",
        "kind": "decision_identity",
        "blocking": "decision_blocking_policy",
        "affected_scope": "decision_affected_scope",
        "latest_decision_point": "decision_blocking_policy",
        "current_delivery_independent": "decision_blocking_policy",
        "binding": "decision_checkpoint_binding_or_observation",
        "observation_receipt": "decision_checkpoint_binding_or_observation",
        "observation_fingerprint": "decision_checkpoint_binding_or_observation",
        "question": "decision_question",
    },
    "product_decision": {
        "id": "decision_identity",
        "kind": "decision_identity",
        "blocking": "decision_blocking_policy",
        "affected_scope": "decision_affected_scope",
        "latest_decision_point": "decision_blocking_policy",
        "current_delivery_independent": "decision_blocking_policy",
        "question": "decision_question",
        "options": "decision_options_or_recommendation",
        "recommendation": "decision_options_or_recommendation",
        "requirements_baseline": "requirements_baseline",
        "product_context": "decision_product_context",
    },
    "architecture_decision": {
        "id": "decision_identity",
        "kind": "decision_identity",
        "blocking": "decision_blocking_policy",
        "affected_scope": "decision_affected_scope",
        "latest_decision_point": "decision_blocking_policy",
        "current_delivery_independent": "decision_blocking_policy",
        "question": "decision_question",
        "options": "decision_options_or_recommendation",
        "recommendation": "decision_options_or_recommendation",
        "requirements_baseline": "requirements_baseline",
        "architecture_context": "architecture",
    },
    "risk_acceptance": {
        "id": "decision_identity",
        "kind": "decision_identity",
        "blocking": "decision_blocking_policy",
        "affected_scope": "decision_affected_scope",
        "latest_decision_point": "decision_blocking_policy",
        "current_delivery_independent": "decision_blocking_policy",
        "question": "decision_question",
        "options": "decision_options_or_recommendation",
        "recommendation": "decision_options_or_recommendation",
        "requirements_baseline": "requirements_baseline",
        "risk_context": "decision_risk_context",
    },
}


def _v4_unique_object_ids(value: Any, *, field: str, label: str) -> list[str]:
    if not isinstance(value, list):
        raise WorkflowDataError(f"{label} must be an array before fingerprinting.")
    identities: list[str] = []
    for index, raw in enumerate(value):
        item = _v4_object(raw, label=f"{label}[{index}]")
        identity = item.get(field)
        if not isinstance(identity, str) or not identity:
            raise WorkflowDataError(f"{label}[{index}].{field} must be non-empty text.")
        identities.append(unicodedata.normalize("NFC", identity))
    if len(identities) != len(set(identities)):
        raise WorkflowDataError(f"{label} must have unique {field} values.")
    return identities


def decision_fingerprint_material(decision: dict[str, Any]) -> dict[str, Any]:
    """Project a decision request while excluding lifecycle answers and deferrals."""

    decision = _v4_object(decision, label="V4 decision")
    kind = decision.get("kind")
    fields = _V4_DECISION_REQUEST_FIELDS.get(kind)
    if fields is None:
        raise WorkflowDataError(f"Unsupported V4 decision kind for fingerprinting: {kind!r}.")
    if kind != "product_checkpoint":
        option_ids = _v4_unique_object_ids(
            decision.get("options"), field="id", label=f"V4 {kind} options"
        )
        recommendation = _v4_object(
            decision.get("recommendation"), label=f"V4 {kind} recommendation"
        )
        recommendation_id = recommendation.get("option_id")
        if not isinstance(recommendation_id, str) or not recommendation_id:
            raise WorkflowDataError(
                f"V4 {kind} recommendation option_id must be non-empty text."
            )
        if unicodedata.normalize("NFC", recommendation_id) not in option_ids:
            raise WorkflowDataError(
                f"V4 {kind} recommendation must reference one declared option ID."
            )
    return {
        field: _v4_required(decision, field, label=f"V4 {kind} decision")
        for field in fields
    }


def decision_fingerprint(decision: dict[str, Any]) -> str:
    return _v4_fingerprint(
        V4_DECISION_FINGERPRINT_ALGORITHM,
        decision_fingerprint_material(decision),
    )


def decision_state_fingerprint_material(decision: dict[str, Any]) -> dict[str, Any]:
    """Project answer/deferral/continuation state without changing request identity."""

    decision = _v4_object(decision, label="V4 decision state")
    kind = decision.get("kind")
    if kind not in _V4_DECISION_REQUEST_FIELDS:
        raise WorkflowDataError(f"Unsupported V4 decision kind for state identity: {kind!r}.")
    status = _v4_required(decision, "status", label="V4 decision state")
    resolution = _v4_required(decision, "resolution", label="V4 decision state")
    if status == "open" and resolution is not None:
        raise WorkflowDataError("Open V4 decisions cannot contain a resolution.")
    if status == "resolved" and not isinstance(resolution, dict):
        raise WorkflowDataError("Resolved V4 decisions require a resolution object.")
    if status not in {"open", "resolved"}:
        raise WorkflowDataError("V4 decision status is invalid for state identity.")
    deferrals = _v4_required(decision, "deferrals", label="V4 decision state")
    if not isinstance(deferrals, list):
        raise WorkflowDataError("V4 decision deferrals must be an array.")
    material = {
        "kind": kind,
        "decision_fingerprint": _v4_required(
            decision, "decision_fingerprint", label="V4 decision state"
        ),
        "status": status,
        "resolution": resolution,
        "deferrals": deferrals,
    }
    if kind == "product_checkpoint":
        continuations = decision.get("continuations", [])
        if not isinstance(continuations, list):
            raise WorkflowDataError("V4 checkpoint continuations must be an array.")
        material["continuations"] = continuations
    return material


def decision_state_fingerprint(decision: dict[str, Any]) -> str:
    return _v4_fingerprint(
        V4_DECISION_STATE_FINGERPRINT_ALGORITHM,
        decision_state_fingerprint_material(decision),
    )


def _v4_stable_normalized_result(receipt: dict[str, Any]) -> dict[str, Any]:
    normalized = _v4_object(
        _v4_required(receipt, "normalized_result", label="V4 observation receipt"),
        label="V4 normalized observation",
    )
    surface = _v4_required(normalized, "surface", label="V4 normalized observation")
    method_evidence = _v4_object(
        _v4_required(normalized, "method_evidence", label="V4 normalized observation"),
        label="V4 observation method_evidence",
    )
    stable_method_fields = {
        "ui": ("kind", "entrypoint_ref", "interaction_trace"),
        "api": (
            "kind", "entrypoint_ref", "status_code", "public_api_ref",
            "public_api_digest",
        ),
        "cli": ("kind", "command_ref", "exit_code"),
        "data": (
            "kind", "entrypoint_ref", "schema_ref", "schema_digest", "row_count",
        ),
        "background": ("kind", "entrypoint_ref"),
        "capability": (
            "kind", "entrypoint_ref", "contract_ref", "contract_digest",
        ),
    }.get(surface)
    if stable_method_fields is None or method_evidence.get("kind") != surface:
        raise WorkflowDataError(
            "Observation method_evidence does not match the normalized result surface."
        )
    return {
        "surface": surface,
        "method_evidence": {
            field: _v4_required(
                method_evidence, field, label="V4 observation method_evidence"
            )
            for field in stable_method_fields
        },
        "assertions": _v4_required(
            normalized, "assertions", label="V4 normalized observation"
        ),
        "stable_output": _v4_required(
            normalized, "stable_output", label="V4 normalized observation"
        ),
        "contracts": _v4_required(
            normalized, "contracts", label="V4 normalized observation"
        ),
    }


def observation_fingerprint_material(
    record: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Project stable product semantics without commit, artifact, time, or machine identity."""

    if record.get("version") != 4:
        raise WorkflowDataError("Observation fingerprints require a V4 task record.")
    delivery_contract = _v4_object(
        _v4_required(record, "delivery_contract", label="V4 task record"),
        label="V4 delivery_contract",
    )
    receipt = _v4_object(receipt, label="V4 observation receipt")
    expected_contract = contract_fingerprint(record)
    stored_contract = record.get("contract_fingerprint")
    if stored_contract != expected_contract:
        raise WorkflowDataError("V4 task contract_fingerprint is stale.")
    if receipt.get("contract_fingerprint") != expected_contract:
        raise WorkflowDataError("Observation receipt is not bound to the current V4 contract.")

    acceptance_ids = delivery_contract.get("acceptance_ids")
    if not isinstance(acceptance_ids, list) or not acceptance_ids:
        raise WorkflowDataError("V4 delivery_contract acceptance_ids must be non-empty.")
    acceptance_by_id: dict[str, dict[str, Any]] = {}
    raw_acceptance = _v4_required(record, "acceptance", label="V4 task record")
    if not isinstance(raw_acceptance, list):
        raise WorkflowDataError("V4 acceptance must be an array.")
    for index, raw in enumerate(raw_acceptance):
        item = _v4_object(raw, label=f"V4 acceptance[{index}]")
        item_id = item.get("id")
        if isinstance(item_id, str):
            acceptance_by_id[item_id] = item
    selected_acceptance = []
    for acceptance_id in acceptance_ids:
        item = acceptance_by_id.get(acceptance_id)
        if item is None:
            raise WorkflowDataError(
                f"V4 delivery_contract references missing acceptance {acceptance_id!r}."
            )
        selected_acceptance.append(
            {
                "id": acceptance_id,
                "criterion": _v4_required(
                    item, "criterion", label=f"V4 acceptance {acceptance_id}"
                ),
            }
        )
    selected_acceptance.sort(key=lambda item: canonical_v4_json_bytes(item))

    product_contract_fields = (
        "kind",
        "focus_slice_id",
        "supports_task_id",
        "supporting",
        "requirement_ids",
        "acceptance_ids",
        "checkpoint",
        "observation",
        "known_placeholders",
        "decision_refs",
        "dependency_refs",
        "execution_mode",
        "architecture",
    )
    environment = _v4_object(
        _v4_required(receipt, "environment", label="V4 observation receipt"),
        label="V4 observation environment",
    )
    stable_environment = {
        field: _v4_required(environment, field, label="V4 observation environment")
        for field in ("kind", "configuration_fingerprint")
    }
    return {
        "request": _v4_required(record, "request", label="V4 task record"),
        "requirements_baseline": _v4_requirements_baseline(record),
        "scope": _v4_object(
            _v4_required(record, "scope", label="V4 task record"), label="V4 scope"
        ),
        "acceptance": selected_acceptance,
        "delivery_contract": {
            field: _v4_required(
                delivery_contract, field, label="V4 delivery_contract"
            )
            for field in product_contract_fields
        },
        "receipt": {
            "environment": stable_environment,
            "fixture": _v4_required(receipt, "fixture", label="V4 observation receipt"),
            "requirement_ids": _v4_required(
                receipt, "requirement_ids", label="V4 observation receipt"
            ),
            "acceptance_ids": _v4_required(
                receipt, "acceptance_ids", label="V4 observation receipt"
            ),
            "normalized_result": _v4_stable_normalized_result(receipt),
            "real_components": _v4_required(
                receipt, "real_components", label="V4 observation receipt"
            ),
            "temporary_components": _v4_required(
                receipt, "temporary_components", label="V4 observation receipt"
            ),
            "material_changes": _v4_required(
                receipt, "material_changes", label="V4 observation receipt"
            ),
            "reversible_assumptions": _v4_required(
                receipt, "reversible_assumptions", label="V4 observation receipt"
            ),
            "known_limitations": _v4_required(
                receipt, "known_limitations", label="V4 observation receipt"
            ),
        },
    }


def observation_fingerprint(record: dict[str, Any], receipt: dict[str, Any]) -> str:
    return _v4_fingerprint(
        V4_OBSERVATION_FINGERPRINT_ALGORITHM,
        observation_fingerprint_material(record, receipt),
    )


def observation_receipt_fingerprint(receipt: dict[str, Any]) -> str:
    return _v4_fingerprint(
        V4_OBSERVATION_RECEIPT_FINGERPRINT_ALGORITHM,
        {"receipt": _v4_object(receipt, label="V4 observation receipt")},
    )


_V4_EXTERNAL_SOURCE_PREFIXES = ("external-receipt:", "provider:", "user:")
_V4_FUTURE_SCOPE_PREFIX = "future:"
_V4_MAX_CLOCK_SKEW = timedelta(minutes=5)
_V4_SENSITIVE_NAME_TOKENS = frozenset(
    {
        "access_key",
        "api_key",
        "auth",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "password",
        "phone",
        "pii",
        "private_key",
        "secret",
        "session",
        "signature",
        "sig",
        "token",
    }
)
_V4_SIGNED_QUERY_KEYS = frozenset(
    {
        "sig",
        "signature",
        "se",
        "sp",
        "sr",
        "st",
        "sv",
        "skoid",
        "sktid",
        "skt",
        "ske",
        "sks",
        "skv",
    }
)
_V4_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_V4_EMAIL = re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_V4_PHONE = re.compile(
    r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d{9}(?!\d)|"
    r"(?<!\d)(?:\+1[\s.-]?)?\(?[2-9]\d{2}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"
)
_V4_SECRET_TEXT = re.compile(
    r"\b(?:authorization\s*:\s*bearer|cookie\s*:|set-cookie\s*:|pii\s*:|secret\s*:|password\s*:|bearer\s+[A-Za-z0-9._~-]{8,})",
    re.IGNORECASE,
)
_V4_ASSIGNMENT_NAME = re.compile(
    r"(?<![A-Za-z0-9_])(?P<quote>[\"']?)(?P<name>[A-Za-z][A-Za-z0-9_.-]*(?:[ \t]+[A-Za-z][A-Za-z0-9_.-]*){0,2})(?P=quote)\s*[:=]"
)
_V4_TOKEN_PREFIX = re.compile(
    r"(?<![A-Za-z0-9])(?:ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|glpat-[A-Za-z0-9_-]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|sk-[A-Za-z0-9_-]{8,})(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def validate_v4_external_source(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise WorkflowDataError(f"{label} must identify an external source.")
    for prefix in _V4_EXTERNAL_SOURCE_PREFIXES:
        if value.startswith(prefix) and value[len(prefix):].strip():
            return value
    raise WorkflowDataError(
        f"{label} must use user:, provider:, or external-receipt: with a non-empty identity."
    )


def _v4_parse_time(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowDataError(f"{label} must be a non-empty ISO-8601 timestamp.")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise WorkflowDataError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkflowDataError(f"{label} must include a timezone.")
    return parsed


def _v4_lifecycle_time(value: Any, *, label: str) -> datetime:
    parsed = _v4_parse_time(value, label=label)
    if parsed > datetime.now(timezone.utc) + _V4_MAX_CLOCK_SKEW:
        raise WorkflowDataError(f"{label} must not be in the future.")
    return parsed


def _validate_v4_checkpoint_lifecycle_time(
    decision: dict[str, Any], lifecycle: dict[str, Any], *, deferred: bool
) -> None:
    receipt = _v4_object(
        decision.get("observation_receipt"), label="V4 checkpoint observation receipt"
    )
    time_field = "deferred_at" if deferred else "decided_at"
    answer_at = _v4_lifecycle_time(
        lifecycle.get(time_field), label=f"V4 checkpoint {time_field}"
    )
    observed_at = _v4_parse_time(
        receipt.get("observed_at"), label="V4 checkpoint observed_at"
    )
    if answer_at < observed_at:
        raise WorkflowDataError("V4 checkpoint answer cannot precede its observation.")
    for value in (
        (receipt.get("entrypoint") or {}).get("expires_at"),
        (receipt.get("environment") or {}).get("expires_at"),
    ):
        if value is not None and answer_at >= _v4_parse_time(
            value, label="V4 checkpoint observation expiry"
        ):
            raise WorkflowDataError(
                "V4 checkpoint answer must precede its observation expiry."
            )


def validate_v4_decision_lifecycle(
    record: dict[str, Any], decision: dict[str, Any]
) -> None:
    """Replay lifecycle semantics that public fingerprints only make tamper-evident."""

    kind = decision.get("kind")
    status = decision.get("status")
    resolution = decision.get("resolution")
    deferrals = decision.get("deferrals")
    if status not in {"open", "resolved"} or not isinstance(deferrals, list):
        raise WorkflowDataError("V4 decision lifecycle state is invalid.")
    if status == "open" and resolution is not None:
        raise WorkflowDataError("Open V4 decisions cannot contain a resolution.")
    if status == "resolved" and not isinstance(resolution, dict):
        raise WorkflowDataError("Resolved V4 decisions require a resolution object.")
    for index, deferral in enumerate(deferrals):
        deferral = _v4_object(deferral, label=f"V4 decision deferral[{index}]")
        validate_v4_external_source(
            deferral.get("source"), label=f"V4 decision deferral[{index}] source"
        )
        if kind == "product_checkpoint":
            _validate_v4_checkpoint_lifecycle_time(
                decision, deferral, deferred=True
            )
        else:
            _v4_lifecycle_time(
                deferral.get("deferred_at"),
                label=f"V4 decision deferral[{index}] deferred_at",
            )
    if status != "resolved":
        return
    assert isinstance(resolution, dict)
    validate_v4_external_source(
        resolution.get("source"), label="V4 decision resolution source"
    )
    if kind == "product_checkpoint":
        if resolution.get("outcome") not in {
            "accepted", "changes_requested", "stopped"
        }:
            raise WorkflowDataError("V4 checkpoint resolution outcome is invalid.")
        _validate_v4_checkpoint_lifecycle_time(
            decision, resolution, deferred=False
        )
        binding = decision.get("binding") or {}
        verification = record.get("verification") or {}
        if (
            binding.get("snapshot_id") == verification.get("snapshot_id")
            and binding.get("contract_fingerprint") == record.get("contract_fingerprint")
        ):
            observed = validate_v4_observation_receipt(
                record,
                decision.get("observation_receipt"),
                require_unexpired=False,
            )
            if observed != decision.get("observation_fingerprint"):
                raise WorkflowDataError(
                    "V4 checkpoint observation fingerprint is stale."
                )
        return
    _v4_lifecycle_time(
        resolution.get("decided_at"), label="V4 decision resolution decided_at"
    )
    option_ids = {
        item.get("id")
        for item in decision.get("options", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if resolution.get("selected_option_id") not in option_ids:
        raise WorkflowDataError(
            "V4 decision resolution must select one declared option ID."
        )
    if kind == "architecture_decision":
        context = decision.get("architecture_context") or {}
        current = (record.get("delivery_contract") or {}).get("architecture") or {}
        if context.get("baseline") != current.get("baseline"):
            raise WorkflowDataError(
                "V4 architecture decision baseline is not current."
            )
        current_guardrails = {
            item.get("id")
            for item in current.get("guardrails", [])
            if isinstance(item, dict)
        }
        if not set(context.get("guardrail_ids") or []).issubset(current_guardrails):
            raise WorkflowDataError(
                "V4 architecture decision guardrail_ids are not current."
            )
    if kind == "risk_acceptance":
        expires_at = (decision.get("risk_context") or {}).get("expires_at")
        if expires_at is not None:
            _v4_parse_time(expires_at, label="V4 risk acceptance expires_at")


def _v4_walk_strings(value: Any, *, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _v4_walk_strings(item, path=f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _v4_walk_strings(item, path=_schema_path(path, str(key)))


def _v4_sensitive_name(value: str) -> bool:
    """Recognize sensitive semantic names after normalizing camelCase and separators."""

    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    if not tokens:
        return False
    joined = "_".join(tokens)
    return (
        any(token in _V4_SENSITIVE_NAME_TOKENS for token in tokens)
        or joined in _V4_SENSITIVE_NAME_TOKENS
        or any(
            joined.startswith(prefix + "_")
            for prefix in (
                "api_key",
                "access_key",
                "private_key",
                "session",
                "phone",
            )
        )
    )


def _v4_contains_sensitive_assignment(value: str) -> bool:
    return any(
        _v4_sensitive_name(match.group("name"))
        for match in _V4_ASSIGNMENT_NAME.finditer(value)
    )


def _v4_display_url_is_signed_or_sensitive(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        return False
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if (
            _v4_sensitive_name(key)
            or key_lower in _V4_SIGNED_QUERY_KEYS
            or key_lower.startswith("x-amz-")
        ):
            return True
    return False


def v4_text_contains_sensitive_evidence(value: str) -> bool:
    """True when display text would leak token, cookie, PII, or a signed URL."""

    if not isinstance(value, str):
        return False
    if any(ord(character) < 32 and character not in "\t" for character in value):
        return True
    if (
        _V4_JWT.search(value)
        or _V4_EMAIL.search(value)
        or _V4_PHONE.search(value)
        or _V4_SECRET_TEXT.search(value)
        or _V4_TOKEN_PREFIX.search(value)
        or _v4_contains_sensitive_assignment(value)
        or _v4_display_url_is_signed_or_sensitive(value)
    ):
        return True
    return False


_V4_REDACTED_DISPLAY = "[omitted]"
_V4_HUMAN_SURFACE_LABELS = {
    "browser": "UI preview",
    "api": "API",
    "cli": "CLI",
    "data": "data proof",
    "background": "background",
    "capability": "capability",
}


def redact_v4_human_value(value: Any) -> Any:
    """Replace sensitive display strings with a controlled omission marker."""

    if isinstance(value, str):
        return _V4_REDACTED_DISPLAY if v4_text_contains_sensitive_evidence(value) else value
    if isinstance(value, list):
        return [redact_v4_human_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_v4_human_value(item) for key, item in value.items()}
    return value


def _validate_v4_redacted_receipt(receipt: dict[str, Any]) -> None:
    normalized_result = _v4_object(
        _v4_required(receipt, "normalized_result", label="Observation receipt"),
        label="Normalized observation",
    )
    stable_output = normalized_result.get("stable_output")
    if isinstance(stable_output, list):
        for index, item in enumerate(stable_output):
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and _v4_sensitive_name(name):
                raise WorkflowDataError(
                    "Observation receipt contains a sensitive stable output name at "
                    f"$.normalized_result.stable_output[{index}].name."
                )
    for path, value in _v4_walk_strings(receipt):
        if any(ord(character) < 32 and character not in "\t" for character in value):
            raise WorkflowDataError(f"Observation receipt contains control characters at {path}.")
        if (
            _V4_JWT.search(value)
            or _V4_EMAIL.search(value)
            or _V4_PHONE.search(value)
            or _V4_SECRET_TEXT.search(value)
            or _V4_TOKEN_PREFIX.search(value)
            or _v4_contains_sensitive_assignment(value)
        ):
            raise WorkflowDataError(f"Observation receipt contains sensitive evidence at {path}.")
        if _v4_display_url_is_signed_or_sensitive(value):
            raise WorkflowDataError(
                f"Observation receipt contains a signed or sensitive URL at {path}."
            )
    redaction = _v4_object(
        _v4_required(receipt, "redaction", label="Observation receipt"),
        label="Observation receipt redaction",
    )
    if redaction.get("status") == "applied" and not redaction.get("notes"):
        raise WorkflowDataError("Applied observation redaction requires non-empty notes.")


def _validate_v4_method_evidence(
    normalized: dict[str, Any], expected_surface: str, expected_entrypoint: str
) -> None:
    evidence = _v4_object(
        normalized.get("method_evidence"), label="Observation method_evidence"
    )
    fields = {
        "ui": {
            "kind", "entrypoint_ref", "interaction_trace", "dom_snapshot_ref",
            "accessibility_snapshot_ref", "visual_evidence_refs",
        },
        "api": {
            "kind", "entrypoint_ref", "request_ref", "response_ref", "status_code",
            "public_api_ref", "public_api_digest",
        },
        "cli": {"kind", "command_ref", "exit_code", "stdout_ref", "stderr_ref"},
        "data": {
            "kind", "entrypoint_ref", "query_ref", "result_ref", "schema_ref",
            "schema_digest", "row_count",
        },
        "background": {
            "kind", "entrypoint_ref", "trigger_ref", "completion_ref", "state_ref",
        },
        "capability": {
            "kind", "entrypoint_ref", "invocation_ref", "result_ref", "contract_ref",
            "contract_digest",
        },
    }.get(expected_surface)
    if fields is None or set(evidence) != fields or evidence.get("kind") != expected_surface:
        raise WorkflowDataError(
            "Observation method_evidence does not match the current recipe method."
        )
    binding_field = "command_ref" if expected_surface == "cli" else "entrypoint_ref"
    if evidence.get(binding_field) != expected_entrypoint:
        raise WorkflowDataError(
            "Observation method_evidence is not bound to the resolved recipe entrypoint."
        )
    for field, value in evidence.items():
        if field in {"kind", "exit_code", "status_code", "row_count", "stderr_ref"}:
            continue
        if field in {"interaction_trace", "visual_evidence_refs"}:
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(item, str) or not item.strip() for item in value)
            ):
                raise WorkflowDataError(
                    f"Observation {expected_surface} {field} must be non-empty text evidence."
                )
            continue
        if not isinstance(value, str) or not value.strip():
            raise WorkflowDataError(
                f"Observation {expected_surface} {field} must be non-empty text."
            )
    if expected_surface == "cli" and (
        not isinstance(evidence.get("exit_code"), int)
        or isinstance(evidence.get("exit_code"), bool)
        or evidence.get("exit_code") != 0
        or (
            evidence.get("stderr_ref") is not None
            and (
                not isinstance(evidence.get("stderr_ref"), str)
                or not evidence["stderr_ref"].strip()
            )
        )
    ):
        raise WorkflowDataError("Observation CLI evidence requires exit_code=0 and valid output refs.")
    if expected_surface == "api" and (
        not isinstance(evidence.get("status_code"), int)
        or isinstance(evidence.get("status_code"), bool)
        or not 100 <= evidence["status_code"] <= 599
    ):
        raise WorkflowDataError("Observation API evidence has an invalid status code.")
    if expected_surface == "data" and (
        not isinstance(evidence.get("row_count"), int)
        or isinstance(evidence.get("row_count"), bool)
        or evidence["row_count"] < 0
    ):
        raise WorkflowDataError("Observation data evidence has an invalid row count.")
    for digest_field in ("public_api_digest", "schema_digest", "contract_digest"):
        if digest_field in evidence and not re.fullmatch(
            r"[0-9a-f]{64}", str(evidence[digest_field])
        ):
            raise WorkflowDataError(
                f"Observation {expected_surface} {digest_field} must be a SHA-256 digest."
            )
    contracts = _v4_object(
        normalized.get("contracts"), label="Normalized observation contracts"
    )
    if expected_surface == "api":
        expected_contract = (
            evidence.get("public_api_ref"), evidence.get("public_api_digest")
        )
        available = {
            (item.get("ref"), item.get("digest"))
            for item in contracts.get("public_api") or []
            if isinstance(item, dict)
        }
        if expected_contract not in available:
            raise WorkflowDataError(
                "Observation API evidence is not bound to a public API contract digest."
            )
    if expected_surface == "data":
        expected_contract = (evidence.get("schema_ref"), evidence.get("schema_digest"))
        available = {
            (item.get("ref"), item.get("digest"))
            for item in contracts.get("schemas") or []
            if isinstance(item, dict)
        }
        if expected_contract not in available:
            raise WorkflowDataError(
                "Observation data evidence is not bound to a schema contract digest."
            )
    if expected_surface == "capability":
        expected_contract = (evidence.get("contract_ref"), evidence.get("contract_digest"))
        available = {
            (item.get("ref"), item.get("digest"))
            for field in ("public_api", "dependencies")
            for item in contracts.get(field) or []
            if isinstance(item, dict)
        }
        if expected_contract not in available:
            raise WorkflowDataError(
                "Observation capability evidence is not bound to a contract digest."
            )


def validate_v4_observation_receipt(
    record: dict[str, Any],
    receipt: dict[str, Any],
    *,
    require_current_snapshot: bool = True,
    require_unexpired: bool = True,
) -> str:
    """Validate an exact, reproducible, redacted Phase A observation receipt."""

    if record.get("version") != 4:
        raise WorkflowDataError("Observation receipts require a V4 task record.")
    receipt = _v4_object(receipt, label="Observation receipt")
    expected_contract = contract_fingerprint(record)
    if record.get("contract_fingerprint") != expected_contract:
        raise WorkflowDataError("V4 task contract_fingerprint is stale.")
    verification = _v4_object(record.get("verification"), label="V4 verification")
    delivery = record.get("delivery_contract") or {}
    source = record.get("source") or {}
    now = datetime.now(timezone.utc)
    if require_current_snapshot:
        expected = {
            "snapshot_id": verification.get("snapshot_id"),
            "delivery_commit": verification.get("delivery_commit"),
            "contract_fingerprint": expected_contract,
        }
        if any(not isinstance(value, str) or not value for value in expected.values()):
            raise WorkflowDataError("Observation receipt requires a sealed current delivery snapshot.")
        for field, value in expected.items():
            if receipt.get(field) != value:
                raise WorkflowDataError(
                    f"Observation receipt {field} is not bound to the current delivery."
                )
    artifact = _v4_object(receipt.get("artifact"), label="Observation artifact")
    if artifact.get("digest") != verification.get("delivery_hash"):
        raise WorkflowDataError("Observation artifact digest does not match the sealed delivery hash.")
    expected_artifact_reference = f"git:{receipt.get('delivery_commit')}"
    if artifact.get("reference") != expected_artifact_reference:
        raise WorkflowDataError(
            "Observation artifact reference does not match the sealed delivery commit."
        )
    recipe = _v4_object(delivery.get("observation"), label="V4 observation recipe")
    entrypoint = _v4_object(receipt.get("entrypoint"), label="Observation entrypoint")
    entrypoint_reference = recipe.get("entrypoint_ref")
    if entrypoint.get("reference") != entrypoint_reference:
        raise WorkflowDataError("Observation entrypoint does not match the current recipe.")
    if not isinstance(entrypoint_reference, str) or not entrypoint_reference.startswith(
        "project-script:"
    ):
        raise WorkflowDataError("Observation recipe entrypoint_ref uses an unsupported scheme.")
    expected_resolved_reference = "command:" + entrypoint_reference.removeprefix(
        "project-script:"
    )
    if entrypoint.get("resolved_reference") != expected_resolved_reference:
        raise WorkflowDataError(
            "Observation resolved entrypoint does not match the current recipe."
        )
    if entrypoint.get("status") != "ready":
        raise WorkflowDataError("Observation entrypoint is not ready.")
    entrypoint_checked_at = _v4_parse_time(
        entrypoint.get("checked_at"), label="Observation entrypoint checked_at"
    )
    entrypoint_expiry = entrypoint.get("expires_at")
    entrypoint_expires_at = (
        _v4_parse_time(entrypoint_expiry, label="Observation entrypoint expires_at")
        if entrypoint_expiry is not None
        else None
    )
    if (
        require_unexpired
        and entrypoint_expires_at is not None
        and entrypoint_expires_at <= now
    ):
        raise WorkflowDataError("Observation entrypoint has expired.")
    healthcheck_ref = recipe.get("healthcheck_ref")
    healthcheck = receipt.get("healthcheck")
    if healthcheck_ref is None:
        if healthcheck is not None:
            raise WorkflowDataError("Observation receipt has an undeclared healthcheck.")
        healthcheck_checked_at = None
    else:
        healthcheck = _v4_object(healthcheck, label="Observation healthcheck")
        if healthcheck.get("reference") != healthcheck_ref or healthcheck.get("status") != "passed":
            raise WorkflowDataError("Observation healthcheck does not match or pass the current recipe.")
        healthcheck_checked_at = _v4_parse_time(
            healthcheck.get("checked_at"), label="Observation healthcheck checked_at"
        )
    fixture = _v4_object(receipt.get("fixture"), label="Observation fixture")
    if fixture.get("reference") != recipe.get("fixture_ref"):
        raise WorkflowDataError("Observation fixture does not match the current recipe fixture_ref.")
    if receipt.get("recipe_replayed") is not True:
        raise WorkflowDataError("Observation receipt must prove the current recipe was replayed.")
    if sorted(receipt.get("requirement_ids", []), key=canonical_v4_json_bytes) != sorted(
        delivery.get("requirement_ids", []), key=canonical_v4_json_bytes
    ):
        raise WorkflowDataError("Observation receipt Requirements IDs do not match the delivery contract.")
    if sorted(receipt.get("acceptance_ids", []), key=canonical_v4_json_bytes) != sorted(
        delivery.get("acceptance_ids", []), key=canonical_v4_json_bytes
    ):
        raise WorkflowDataError("Observation receipt acceptance IDs do not match the delivery contract.")
    baseline = source.get("requirements_baseline")
    if not isinstance(baseline, dict):
        raise WorkflowDataError("V4 task has no Requirements baseline for observation binding.")
    normalized = _v4_object(receipt.get("normalized_result"), label="Normalized observation")
    expected_surface = {
        "browser": "ui",
        "api": "api",
        "cli": "cli",
        "data": "data",
        "background": "background",
        "capability": "capability",
    }.get(recipe.get("method"))
    if normalized.get("surface") != expected_surface:
        raise WorkflowDataError("Observation surface does not match the current recipe method.")
    if not isinstance(expected_surface, str):
        raise WorkflowDataError("Observation recipe method is unsupported.")
    _validate_v4_method_evidence(
        normalized, expected_surface, expected_resolved_reference
    )
    assertions = normalized.get("assertions")
    if not isinstance(assertions, list) or not assertions or any(
        not isinstance(item, dict) or item.get("status") != "passed" for item in assertions
    ):
        raise WorkflowDataError("Every normalized observation assertion must pass.")
    environment = _v4_object(receipt.get("environment"), label="Observation environment")
    expires_at = environment.get("expires_at")
    if environment.get("kind") == "isolated_preview" and not (
        expires_at or entrypoint_expiry
    ):
        raise WorkflowDataError("Isolated preview observations require an explicit expiry.")
    environment_expires_at = (
        _v4_parse_time(expires_at, label="Observation environment expires_at")
        if expires_at is not None
        else None
    )
    if (
        require_unexpired
        and environment_expires_at is not None
        and environment_expires_at <= now
    ):
        raise WorkflowDataError("Observation environment has expired.")
    observed_at = _v4_parse_time(
        receipt.get("observed_at"), label="Observation observed_at"
    )
    if observed_at > now + _V4_MAX_CLOCK_SKEW:
        raise WorkflowDataError("Observation observed_at is in the future.")
    for label, checked_at in (
        ("entrypoint", entrypoint_checked_at),
        ("healthcheck", healthcheck_checked_at),
    ):
        if checked_at is not None and checked_at > observed_at:
            raise WorkflowDataError(
                f"Observation {label} checked_at must not be later than observed_at."
            )
    for label, expiry in (
        ("entrypoint", entrypoint_expires_at),
        ("environment", environment_expires_at),
    ):
        if expiry is not None and observed_at >= expiry:
            raise WorkflowDataError(
                f"Observation observed_at must precede the {label} expiry."
            )
    validate_v4_external_source(
        receipt.get("observer_source"), label="Observation observer_source"
    )
    _validate_v4_redacted_receipt(receipt)
    redaction = _v4_object(receipt.get("redaction"), label="Observation receipt redaction")
    risk = _v4_object(record.get("risk"), label="V4 task risk")
    if (
        fixture.get("data_class") in {"real", "mixed"}
        or risk.get("sensitive_data") is True
    ) and redaction.get("status") != "applied":
        raise WorkflowDataError(
            "Real, mixed, or sensitive observation evidence requires applied redaction."
        )
    return observation_fingerprint(record, receipt)


def v4_decision_point(record: dict[str, Any]) -> str:
    """Derive the task's current decision boundary from sealed evidence."""

    integration = record.get("integration") or {}
    if record.get("phase") == "integration" or integration.get("status") != "not_ready":
        return "before_integration"
    review = record.get("review") or {}
    verification = record.get("verification") or {}
    if review.get("status") == "pass" or record.get("status") == "completed":
        return "before_dependency"
    if verification.get("snapshot_id") is not None or record.get("phase") == "review":
        return "before_review"
    return "before_implementation"


_V4_DECISION_POINT_RANK = {
    "before_implementation": 0,
    "before_review": 1,
    "before_dependency": 2,
    "before_integration": 3,
}


def derive_v4_decision_blocking(record: dict[str, Any], decision: dict[str, Any]) -> bool:
    """Derive persisted blocking classification without trusting caller text."""

    kind = decision.get("kind")
    if kind == "product_checkpoint":
        mode = (record.get("delivery_contract") or {}).get("checkpoint", {}).get("mode")
        if mode == "not_required":
            raise WorkflowDataError("not_required contracts cannot request a product checkpoint.")
        return mode == "required"
    affected = decision.get("affected_scope")
    future_only = isinstance(affected, list) and bool(affected) and all(
        isinstance(item, str) and item.startswith(_V4_FUTURE_SCOPE_PREFIX)
        for item in affected
    )
    independent = decision.get("current_delivery_independent") is True
    decision_id = decision.get("id")
    referenced = any(
        isinstance(item, dict) and item.get("decision_id") == decision_id
        for item in (record.get("delivery_contract") or {}).get("decision_refs", [])
    )
    latest = decision.get("latest_decision_point")
    current = v4_decision_point(record)
    due = (
        latest not in _V4_DECISION_POINT_RANK
        or _V4_DECISION_POINT_RANK[current] >= _V4_DECISION_POINT_RANK[latest]
    )
    return not (future_only and independent and not referenced and not due)


def validate_v4_current_observation_continuations(record: dict[str, Any]) -> None:
    """Fail closed when a current-snapshot continuation or review binding is tampered."""

    verification = record.get("verification") or {}
    current_snapshot = verification.get("snapshot_id")
    if not isinstance(current_snapshot, str):
        return
    matched_current = False
    for decision in record.get("decision_log") or []:
        if not isinstance(decision, dict) or decision.get("kind") != "product_checkpoint":
            continue
        if _require_decision_current_snapshot_continuations(
            record, decision, current_snapshot
        ):
            matched_current = True
    compact = (record.get("review") or {}).get("observation_equivalence")
    if compact is None:
        return
    if not isinstance(compact, dict):
        raise WorkflowDataError("V4 review observation_equivalence must be an object.")
    if compact.get("target_snapshot_id") != current_snapshot:
        raise WorkflowDataError(
            "V4 review observation_equivalence is not bound to the current snapshot."
        )
    if not matched_current:
        raise WorkflowDataError(
            "V4 review observation_equivalence has no matching current continuation."
        )


def validate_v4_contract_identity(record: dict[str, Any]) -> None:
    """Validate canonical contract and decision identities before any V4 action."""

    expected = contract_fingerprint(record)
    if record.get("contract_fingerprint") != expected:
        raise WorkflowDataError("V4 task contract_fingerprint is stale.")
    decisions = record.get("decision_log")
    if not isinstance(decisions, list):
        raise WorkflowDataError("V4 decision_log must be an array.")
    ids: set[str] = set()
    for index, decision in enumerate(decisions):
        decision = _v4_object(decision, label=f"V4 decision_log[{index}]")
        decision_id = decision.get("id")
        if not isinstance(decision_id, str) or decision_id in ids:
            raise WorkflowDataError("V4 decision IDs must be unique.")
        ids.add(decision_id)
        if decision.get("decision_fingerprint") != decision_fingerprint(decision):
            raise WorkflowDataError(f"V4 decision {decision_id} fingerprint is stale.")
        if decision.get("decision_state_fingerprint") != decision_state_fingerprint(decision):
            raise WorkflowDataError(f"V4 decision {decision_id} state fingerprint is stale.")
        validate_v4_decision_lifecycle(record, decision)


def validate_v4_decision_references(record: dict[str, Any]) -> None:
    contract = _v4_object(record.get("delivery_contract"), label="V4 delivery contract")
    decisions = {
        item.get("id"): item
        for item in record.get("decision_log", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    references = contract.get("decision_refs")
    if not isinstance(references, list):
        raise WorkflowDataError("V4 delivery contract decision_refs must be an array.")
    for reference in references:
        reference = _v4_object(reference, label="V4 decision reference")
        decision_id = reference.get("decision_id")
        decision = decisions.get(decision_id)
        if decision is None:
            raise WorkflowDataError(f"V4 contract references missing decision {decision_id}.")
        if decision.get("status") != "resolved":
            raise WorkflowDataError(f"V4 contract references unresolved decision {decision_id}.")
        decision_baseline = (
            (decision.get("binding") or {}).get("requirements_baseline")
            if decision.get("kind") == "product_checkpoint"
            else decision.get("requirements_baseline")
        )
        if decision_baseline != (record.get("source") or {}).get("requirements_baseline"):
            raise WorkflowDataError(
                f"V4 contract decision ref {decision_id} has a stale Requirements baseline."
            )
        if (decision.get("resolution") or {}).get("selected_option_id") != reference.get(
            "selected_option_id"
        ):
            raise WorkflowDataError(
                f"V4 contract decision ref {decision_id} selected option is stale."
            )
    architecture = contract.get("architecture") or {}
    if architecture.get("declared_impact") == "changes_guardrail":
        accepted = any(
            decision.get("kind") == "architecture_decision"
            and decision.get("status") == "resolved"
            and any(
                reference.get("decision_id") == decision.get("id")
                and reference.get("selected_option_id")
                == (decision.get("resolution") or {}).get("selected_option_id")
                for reference in references
                if isinstance(reference, dict)
            )
            for decision in decisions.values()
        )
        if not accepted:
            raise WorkflowDataError(
                "V4 changes_guardrail impact requires a resolved architecture decision in decision_refs."
            )


def v4_continuation_path_class(path: str) -> str:
    normalized = path.replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part]
    name = parts[-1] if parts else normalized
    sensitive_parts = {
        "api", "apis", "public", "schema", "schemas", "migration", "migrations",
        "infra", "infrastructure", "terraform", "helm", "k8s", "kubernetes",
        "deploy", "deployment", "deployments", "ops", "docker", "workflows",
        ".github", ".gitlab", ".circleci", ".buildkite", ".teamcity",
        "ci", "cd",
    }
    sensitive_names = {
        "package.json", "package-lock.json", "npm-shrinkwrap.json",
        "pnpm-lock.yaml", "pnpm-workspace.yaml", "yarn.lock",
        "requirements.txt", "constraints.txt", "pyproject.toml", "setup.py", "setup.cfg",
        "pipfile", "pipfile.lock", "poetry.lock", "pdm.lock", "uv.lock",
        "go.mod", "go.sum", "go.work", "go.work.sum", "cargo.toml", "cargo.lock",
        "composer.json", "composer.lock", "gemfile", "gemfile.lock",
        "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
        "settings.gradle.kts", "gradle.lockfile", "packages.lock.json",
        "openapi.json", "openapi.yaml", "openapi.yml", "swagger.json",
        "swagger.yaml", "swagger.yml", "asyncapi.json", "asyncapi.yaml",
        "asyncapi.yml",
        "dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "compose.yml", "compose.yaml", "makefile", "procfile", "justfile",
        "taskfile.yml", "taskfile.yaml", "vagrantfile", ".dockerignore",
        "jenkinsfile", "azure-pipelines.yml", "azure-pipelines.yaml",
        "buildspec.yml", "buildspec.yaml", "cloudbuild.yml", "cloudbuild.yaml",
        "tiltfile", "cmakelists.txt", "workspace", "workspace.bazel",
        "module.bazel", "build", "build.bazel", "meson.build",
        ".gitlab-ci.yml", ".gitlab-ci.yaml", ".travis.yml", ".travis.yaml",
        "appveyor.yml", "appveyor.yaml", "bitbucket-pipelines.yml",
        "bitbucket-pipelines.yaml",
    }
    dependency_text = (
        name.startswith(("requirements-", "constraints-")) and name.endswith(".txt")
    )
    container_or_ci_variant = name.startswith(
        (
            "dockerfile.", "docker-compose.", "compose.", "jenkinsfile.",
            "azure-pipelines.", "buildspec.", "cloudbuild.",
        )
    )
    if (
        sensitive_parts.intersection(parts)
        or name in sensitive_names
        or dependency_text
        or container_or_ci_variant
        or name.endswith(
            (
            ".lock", ".schema.json", ".proto", ".sql", ".graphql", ".gql",
                ".avsc", ".thrift", ".csproj", ".fsproj", ".vbproj",
                ".tf", ".tfvars", ".hcl", ".bazel", ".bzl", ".cmake",
                ".gradle", ".gradle.kts",
            )
        )
    ):
        raise WorkflowDataError(
            f"Observation continuation cannot classify product-contract path as internal: {path}."
        )
    if "tests" in parts or "test" in parts or name.startswith(("test_", "test-")):
        return "test"
    if "docs" in parts or name.endswith((".md", ".rst", ".txt")):
        return "documentation"
    return "internal_implementation"


def v4_architecture_sensitive_paths(changed_paths: Iterable[str]) -> list[str]:
    sensitive: list[str] = []
    for path in changed_paths:
        try:
            v4_continuation_path_class(path)
        except WorkflowDataError:
            sensitive.append(path)
    return sorted(sensitive)


def validate_v4_architecture_delivery(
    record: dict[str, Any], changed_paths: Iterable[str]
) -> None:
    """Require an accepted contract-bound architecture decision for structural paths."""

    sensitive = v4_architecture_sensitive_paths(changed_paths)
    if not sensitive:
        return
    contract = record.get("delivery_contract") or {}
    architecture = contract.get("architecture") or {}
    if architecture.get("declared_impact") != "changes_guardrail":
        raise WorkflowDataError(
            "V4 delivery changes architecture-sensitive paths without declared_impact=changes_guardrail: "
            + ", ".join(sensitive)
        )
    decisions = {
        item.get("id"): item
        for item in record.get("decision_log", [])
        if isinstance(item, dict)
    }
    for reference in contract.get("decision_refs", []):
        if not isinstance(reference, dict):
            continue
        decision = decisions.get(reference.get("decision_id"))
        if (
            isinstance(decision, dict)
            and decision.get("kind") == "architecture_decision"
            and decision.get("status") == "resolved"
            and (decision.get("resolution") or {}).get("selected_option_id")
            == reference.get("selected_option_id")
            and decision.get("requirements_baseline")
            == (record.get("source") or {}).get("requirements_baseline")
            and (decision.get("architecture_context") or {}).get("baseline")
            == architecture.get("baseline")
        ):
            return
    raise WorkflowDataError(
        "V4 architecture-sensitive delivery lacks a current resolved architecture decision in decision_refs."
    )


def _v4_continuation_is_current(
    record: dict[str, Any],
    decision: dict[str, Any],
    continuation: dict[str, Any],
) -> bool:
    verification = record.get("verification") or {}
    current_snapshot = verification.get("snapshot_id")
    if (
        continuation.get("source_decision_id") != decision.get("id")
        or continuation.get("source_decision_fingerprint")
        != decision.get("decision_fingerprint")
        or continuation.get("source_snapshot_id")
        != (decision.get("binding") or {}).get("snapshot_id")
        or continuation.get("source_observation_fingerprint")
        != decision.get("observation_fingerprint")
        or continuation.get("target_snapshot_id") != current_snapshot
        or continuation.get("target_snapshot_id") == continuation.get("source_snapshot_id")
    ):
        return False
    try:
        target_receipt = continuation.get("target_receipt")
        target_observation = validate_v4_observation_receipt(
            record,
            target_receipt,
            require_unexpired=False,
        )
        target_receipt_identity = observation_receipt_fingerprint(target_receipt)
    except WorkflowDataError:
        return False
    if (
        continuation.get("target_observation_fingerprint") != target_observation
        or continuation.get("target_receipt_fingerprint") != target_receipt_identity
        or target_observation != decision.get("observation_fingerprint")
    ):
        return False
    source_material = decision.get("contract_material")
    if not isinstance(source_material, dict):
        return False
    try:
        source_contract = contract_fingerprint_from_material(source_material)
        target_material = contract_fingerprint_material(record)
    except WorkflowDataError:
        return False
    changed_categories = sorted(
        field
        for field in target_material
        if source_material.get(field) != target_material.get(field)
    )
    if any(field not in {"planning", "risk"} for field in changed_categories):
        return False
    if continuation.get("contract_diff") != {
        "source_fingerprint": source_contract,
        "target_fingerprint": record.get("contract_fingerprint"),
        "changed_categories": changed_categories,
    }:
        return False
    if source_contract != (decision.get("binding") or {}).get("contract_fingerprint"):
        return False
    changed_paths = continuation.get("changed_paths")
    if not isinstance(changed_paths, list) or not changed_paths:
        return False
    try:
        expected_classes = [
            {"path": path, "classification": v4_continuation_path_class(path)}
            for path in changed_paths
            if isinstance(path, str)
        ]
    except WorkflowDataError:
        return False
    if len(expected_classes) != len(changed_paths) or continuation.get("path_classes") != expected_classes:
        return False
    if continuation.get("replay") != {
        "recipe_replayed": True,
        "normalized_result_matches": True,
    }:
        return False
    review = record.get("review") or {}
    reviewer = continuation.get("reviewer_receipt") or {}
    expected_reviewer = {
        "reviewer_id": review.get("agent_id"),
        "snapshot_id": current_snapshot,
        "assessment": "equivalent",
        "user_behavior": "unchanged",
        "data_contract": "unchanged",
        "public_interface": "unchanged",
        "dependencies": "unchanged",
        "architecture": "unchanged",
        "source": reviewer.get("source"),
    }
    try:
        validate_v4_external_source(
            reviewer.get("source"), label="Continuation Reviewer source"
        )
    except WorkflowDataError:
        return False
    if (
        review.get("status") != "pass"
        or review.get("snapshot_id") != current_snapshot
        or reviewer != expected_reviewer
    ):
        return False
    compact = {
        "continuation_version": 1,
        "source_decision_id": decision.get("id"),
        "target_snapshot_id": current_snapshot,
        "target_observation_fingerprint": target_observation,
        "target_receipt_fingerprint": target_receipt_identity,
        "assessment": "equivalent",
    }
    if review.get("observation_equivalence") != compact:
        return False
    try:
        _v4_parse_time(continuation.get("recorded_at"), label="Continuation recorded_at")
    except WorkflowDataError:
        return False
    return True


def _require_decision_current_snapshot_continuations(
    record: dict[str, Any],
    decision: dict[str, Any],
    current_snapshot: str,
) -> bool:
    """Return True when a current continuation exists; raise if one is tampered."""

    matched = False
    continuations = decision.get("continuations") or []
    if not isinstance(continuations, list):
        raise WorkflowDataError("V4 checkpoint continuations must be an array.")
    for continuation in continuations:
        if not isinstance(continuation, dict):
            raise WorkflowDataError("V4 observation continuation must be an object.")
        if continuation.get("target_snapshot_id") != current_snapshot:
            continue
        if not _v4_continuation_is_current(record, decision, continuation):
            raise WorkflowDataError(
                "V4 observation continuation bound to the current snapshot is stale or tampered."
            )
        matched = True
    return matched


def _v4_checkpoint_is_current(record: dict[str, Any], decision: dict[str, Any]) -> bool:
    verification = record.get("verification") or {}
    current_snapshot = verification.get("snapshot_id")
    current_contract = record.get("contract_fingerprint")
    baseline = (record.get("source") or {}).get("requirements_baseline")
    resolution = decision.get("resolution") or {}
    if decision.get("status") != "resolved" or resolution.get("outcome") != "accepted":
        return False
    if isinstance(current_snapshot, str):
        has_current_continuation = _require_decision_current_snapshot_continuations(
            record, decision, current_snapshot
        )
    else:
        has_current_continuation = False
    binding = decision.get("binding") or {}
    if (
        binding.get("snapshot_id") == current_snapshot
        and binding.get("contract_fingerprint") == current_contract
        and binding.get("requirements_baseline") == baseline
    ):
        try:
            actual = validate_v4_observation_receipt(
                record,
                decision.get("observation_receipt"),
                require_unexpired=False,
            )
        except WorkflowDataError:
            return False
        return actual == decision.get("observation_fingerprint")
    return has_current_continuation


def v4_action_blockers(record: dict[str, Any], action: str) -> list[str]:
    """Return deterministic V4 blockers for one lifecycle entrypoint."""

    blockers: list[str] = []
    decisions = record.get("decision_log") or []
    action_point = {
        "preflight": "before_implementation",
        "record-developer": "before_implementation",
        "record-review": "before_review",
        "complete-task": "before_dependency",
        "gate": "before_dependency",
        "dependency": "before_dependency",
        "prepare-integration": "before_integration",
    }.get(action)
    if action_point is None:
        raise WorkflowDataError(f"Unsupported V4 gate action: {action}.")
    validate_v4_decision_references(record)
    checkpoint_mode = (
        (record.get("delivery_contract") or {}).get("checkpoint") or {}
    ).get("mode")
    for decision in decisions:
        decision_id = str(decision.get("id"))
        resolution = decision.get("resolution") or {}
        expected_blocking = derive_v4_decision_blocking(record, decision)
        if (
            decision.get("status") == "open"
            and decision.get("blocking") is not expected_blocking
        ):
            blockers.append(f"decision {decision_id} has a stale blocking classification")
            continue
        effective_blocking = expected_blocking
        if decision.get("kind") == "product_checkpoint":
            if resolution.get("outcome") == "stopped":
                blockers.append(f"{decision_id} stopped this task")
            checkpoint_due = (
                checkpoint_mode == "required"
                and action in {
                    "record-review", "complete-task", "gate", "dependency",
                    "prepare-integration",
                }
            ) or (
                checkpoint_mode == "show_before_dependency"
                and action in {"dependency", "prepare-integration"}
            )
            if decision.get("status") == "open" and (
                effective_blocking or checkpoint_due
            ):
                blockers.append(f"open product checkpoint {decision_id}")
            # Checkpoint timing is derived from checkpoint.mode below. It does
            # not inherit the generic decision latest-point rule.
            continue
        latest = decision.get("latest_decision_point")
        due = (
            latest not in _V4_DECISION_POINT_RANK
            or _V4_DECISION_POINT_RANK[action_point] >= _V4_DECISION_POINT_RANK[latest]
        )
        decision_baseline = decision.get("requirements_baseline")
        current_baseline = (record.get("source") or {}).get("requirements_baseline")
        if (
            decision.get("status") == "resolved"
            and decision_baseline != current_baseline
            and (effective_blocking or due)
        ):
            blockers.append(f"resolved decision {decision_id} has a stale Requirements baseline")
            continue
        if decision.get("kind") == "risk_acceptance":
            expires_at = (decision.get("risk_context") or {}).get("expires_at")
            try:
                expired = expires_at is not None and _v4_parse_time(
                    expires_at, label=f"Risk acceptance {decision_id} expires_at"
                ) <= datetime.now(timezone.utc)
            except WorkflowDataError:
                expired = True
            if expired and (effective_blocking or due):
                blockers.append(f"risk acceptance {decision_id} is expired")
                continue
        if decision.get("status") != "open":
            continue
        if effective_blocking or due:
            blockers.append(f"open decision {decision_id}")
    mode = checkpoint_mode
    current_checkpoint = any(
        isinstance(item, dict)
        and item.get("kind") == "product_checkpoint"
        and _v4_checkpoint_is_current(record, item)
        for item in decisions
    )
    if mode == "required" and action in {
        "record-review", "complete-task", "gate", "dependency", "prepare-integration"
    } and not current_checkpoint:
        blockers.append("required product checkpoint is not accepted for the current snapshot")
    if mode == "show_before_dependency" and action in {
        "dependency", "prepare-integration"
    } and not current_checkpoint:
        blockers.append("show_before_dependency checkpoint is not accepted for the current snapshot")
    if action == "prepare-integration" and (
        record.get("delivery_contract") or {}
    ).get("execution_mode") == "exploratory":
        blockers.append("exploratory deliveries cannot prepare integration")
    return blockers


def require_v4_action(record: dict[str, Any], action: str) -> None:
    blockers = v4_action_blockers(record, action)
    if blockers:
        raise WorkflowDataError(f"V4 {action} blocked: " + "; ".join(blockers) + ".")


def _empty_v4_verification() -> dict[str, Any]:
    return {
        "status": "pending",
        "delivery_commit": None,
        "delivery_hash": None,
        "patch_hash": None,
        "snapshot_id": None,
        "changed_paths": [],
    }


def _empty_v4_developer() -> dict[str, Any]:
    return {
        "evidence_contract_version": None,
        "agent_id": None,
        "snapshot_id": None,
        "scopes": [],
        "commands": [],
        "claims": [],
        "handoff": None,
    }


def _empty_v4_review() -> dict[str, Any]:
    return {
        "evidence_contract_version": None,
        "agent_id": None,
        "snapshot_id": None,
        "status": "pending",
        "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
        "requirement_checklist": [],
        "accepted_findings": [],
        "claim_assessments": [],
        "summary": None,
        "observation_equivalence": None,
    }


def _empty_v4_integration() -> dict[str, Any]:
    return {
        "status": "not_ready",
        "mode": None,
        "policy_id": None,
        "source_ref": None,
        "target_ref": None,
        "target_parent": None,
        "pr_head_commit": None,
        "result_commit": None,
        "merge_strategy": None,
        "queue_id": None,
        "queued_at": None,
        "queue_priority": None,
        "closeout_commit": None,
        "closeout_state_fingerprint": None,
        "pr_url": None,
        "ci_checks": [],
        "evidence": [],
    }


def reset_v4_snapshot_evidence(
    record: dict[str, Any],
    *,
    allowed_integration_statuses: tuple[str, ...] = ("not_ready",),
) -> None:
    """Invalidate all snapshot-bound V4 evidence while retaining decision history."""

    if record.get("version") != 4:
        raise WorkflowDataError("The V4 reset helper only accepts V4 task records.")
    integration = record.get("integration")
    if (
        not isinstance(integration, dict)
        or integration.get("status") not in allowed_integration_statuses
    ):
        allowed = ", ".join(allowed_integration_statuses)
        raise WorkflowDataError(
            f"V4 snapshot reset requires integration.status in {{{allowed}}}."
        )
    acceptance = record.get("acceptance")
    if not isinstance(acceptance, list) or any(not isinstance(item, dict) for item in acceptance):
        raise WorkflowDataError("V4 task acceptance is invalid.")
    for item in acceptance:
        item["status"] = "pending"
        item["evidence"] = []
    record["status"] = "in_progress"
    record["phase"] = "developer"
    record["verification"] = _empty_v4_verification()
    record["developer"] = _empty_v4_developer()
    record["review"] = _empty_v4_review()
    record["human_approvals"] = []
    record["integration"] = _empty_v4_integration()
    record["process_retrospective"] = {
        "completed": False,
        "completed_by": None,
        "completed_at": None,
        "questions": {
            "repeated_problem_found": False,
            "guidance_gap_found": False,
            "deterministic_check_candidate_found": False,
        },
        "summary": None,
    }
    record["rule_proposals"] = []
    record["remaining_risks"] = []


def task_record_schema_name(record: dict[str, Any]) -> str:
    version = record.get("version")
    if version == 3:
        return "task-record-v3.schema.json"
    if version == 4:
        return "task-record-v4.schema.json"
    raise WorkflowDataError(f"Unsupported task record version: {version!r}.")


EVIDENCE_SCOPE_KINDS = frozenset(
    {
        "canonical_delivery_paths",
        "declared_path_call_graph",
        "explicit_command_set",
        "explicit_runtime_surfaces",
        "explicit_test_set",
        "repository_tree",
    }
)
_AGGREGATE_SCOPE_TARGETS = frozenset(
    {
        "all",
        "all_commands",
        "all_dry_runs",
        "all_paths",
        "all_tests",
        "entire_repository",
        "everything",
        "global",
        "repo_wide",
        "repository_wide",
        "whole_repository",
    }
)
_AGGREGATE_SCOPE_PREFIXES = (
    "all_",
    "entire_",
    "every_",
    "global_",
    "repo_wide",
    "repository_wide",
    "whole_",
)


def _evidence_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowDataError(f"{label} must be non-empty text.")
    return value.strip()


def _evidence_text_set(value: Any, *, label: str, non_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (non_empty and not value):
        requirement = "a non-empty array" if non_empty else "an array"
        raise WorkflowDataError(f"{label} must be {requirement} of text values.")
    normalized = [_evidence_text(item, label=f"{label} item") for item in value]
    if len(normalized) != len(set(normalized)):
        raise WorkflowDataError(f"{label} must not contain duplicates.")
    return sorted(normalized)


def _scope_target_token(value: str) -> str:
    return re.sub(r"[^a-z0-9*]+", "_", value.casefold()).strip("_")


def _normalize_evidence_scope(scope: Any) -> dict[str, Any]:
    if not isinstance(scope, dict):
        raise WorkflowDataError("Developer evidence scope must be an object.")
    scope_id = _evidence_text(scope.get("id"), label="Developer evidence scope id")
    kind = _evidence_text(scope.get("kind"), label=f"Developer evidence scope {scope_id} kind")
    if kind not in EVIDENCE_SCOPE_KINDS:
        raise WorkflowDataError(f"Developer evidence scope {scope_id} has unsupported kind {kind!r}.")
    targets = _evidence_text_set(
        scope.get("targets"),
        label=f"Developer evidence scope {scope_id} targets",
        non_empty=True,
    )
    for target in targets:
        token = _scope_target_token(target)
        if (
            "*" in target
            or token in _AGGREGATE_SCOPE_TARGETS
            or token.startswith(_AGGREGATE_SCOPE_PREFIXES)
        ):
            raise WorkflowDataError(
                f"Developer evidence scope {scope_id} target {target!r} is aggregate; enumerate exact targets."
            )
    if kind == "repository_tree":
        if targets != ["."]:
            raise WorkflowDataError("repository_tree evidence must use the exact target '.'.")
    elif "." in targets:
        raise WorkflowDataError("Only repository_tree evidence may use repository root target '.'.")
    observed_surfaces = _evidence_text_set(
        scope.get("observed_surfaces"),
        label=f"Developer evidence scope {scope_id} observed_surfaces",
    )
    if kind == "explicit_runtime_surfaces" and not observed_surfaces:
        raise WorkflowDataError(
            f"Developer evidence scope {scope_id} must enumerate observed runtime surfaces."
        )
    excluded_targets = _evidence_text_set(
        scope.get("excluded_targets"),
        label=f"Developer evidence scope {scope_id} excluded_targets",
    )
    if kind == "repository_tree" and "mutable_workflow_control" not in excluded_targets:
        raise WorkflowDataError(
            "repository_tree evidence must explicitly exclude mutable_workflow_control."
        )
    return {
        "id": scope_id,
        "kind": kind,
        "targets": targets,
        "observed_surfaces": observed_surfaces,
        "excluded_targets": excluded_targets,
    }


def _normalize_developer_evidence(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise WorkflowDataError("Developer evidence must be an object.")
    scopes = [_normalize_evidence_scope(item) for item in evidence.get("scopes", [])]
    scope_ids = [item["id"] for item in scopes]
    if len(scope_ids) != len(set(scope_ids)):
        raise WorkflowDataError("Developer evidence scope IDs must be unique.")
    scope_by_id = {item["id"]: item for item in scopes}

    commands: list[dict[str, Any]] = []
    for raw in evidence.get("commands", []):
        if not isinstance(raw, dict):
            raise WorkflowDataError("Developer evidence command must be an object.")
        command_id = _evidence_text(raw.get("id"), label="Developer evidence command id")
        cwd = _evidence_text(raw.get("cwd"), label=f"Developer evidence command {command_id} cwd")
        if cwd != ".":
            try:
                normalized_cwd = normalize_repo_path(cwd)
            except WorkflowPathError as exc:
                raise WorkflowDataError(
                    f"Developer evidence command {command_id} cwd is unsafe: {exc}"
                ) from exc
            if normalized_cwd != cwd:
                raise WorkflowDataError(
                    f"Developer evidence command {command_id} cwd must be normalized repository-relative text."
                )
        command_scope_ids = _evidence_text_set(
            raw.get("scope_ids"),
            label=f"Developer evidence command {command_id} scope_ids",
            non_empty=True,
        )
        missing_scopes = sorted(set(command_scope_ids) - set(scope_by_id))
        if missing_scopes:
            raise WorkflowDataError(
                f"Developer evidence command {command_id} references unknown scopes: "
                + ", ".join(missing_scopes)
            )
        if any(scope_by_id[item]["kind"] == "repository_tree" for item in command_scope_ids) and cwd != ".":
            raise WorkflowDataError(
                f"Developer evidence command {command_id} must run from cwd='.' for repository_tree scope."
            )
        exit_code = raw.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise WorkflowDataError(f"Developer evidence command {command_id} exit_code must be an integer.")
        expected_failure = raw.get("expected_failure")
        if not isinstance(expected_failure, bool):
            raise WorkflowDataError(
                f"Developer evidence command {command_id} expected_failure must be boolean."
            )
        if exit_code != 0 and expected_failure is not True:
            raise WorkflowDataError(
                f"Developer evidence command {command_id} failed without expected_failure=true."
            )
        commands.append(
            {
                "id": command_id,
                "command": _evidence_text(
                    raw.get("command"), label=f"Developer evidence command {command_id} command"
                ),
                "cwd": cwd,
                "exit_code": exit_code,
                "expected_failure": expected_failure,
                "result": _evidence_text(
                    raw.get("result"), label=f"Developer evidence command {command_id} result"
                ),
                "scope_ids": command_scope_ids,
            }
        )
    command_ids = [item["id"] for item in commands]
    if len(command_ids) != len(set(command_ids)):
        raise WorkflowDataError("Developer evidence command IDs must be unique.")
    command_by_id = {item["id"]: item for item in commands}

    claims: list[dict[str, Any]] = []
    for raw in evidence.get("claims", []):
        if not isinstance(raw, dict):
            raise WorkflowDataError("Developer evidence claim must be an object.")
        claim_id = _evidence_text(raw.get("id"), label="Developer evidence claim id")
        scope_id = _evidence_text(
            raw.get("scope_id"), label=f"Developer evidence claim {claim_id} scope_id"
        )
        if scope_id not in scope_by_id:
            raise WorkflowDataError(
                f"Developer evidence claim {claim_id} references unknown scope {scope_id!r}."
            )
        supporting_ids = _evidence_text_set(
            raw.get("supporting_command_ids"),
            label=f"Developer evidence claim {claim_id} supporting_command_ids",
            non_empty=True,
        )
        missing_commands = sorted(set(supporting_ids) - set(command_by_id))
        if missing_commands:
            raise WorkflowDataError(
                f"Developer evidence claim {claim_id} references unknown commands: "
                + ", ".join(missing_commands)
            )
        outside_scope = [
            command_id
            for command_id in supporting_ids
            if scope_id not in command_by_id[command_id]["scope_ids"]
        ]
        if outside_scope:
            raise WorkflowDataError(
                f"Developer evidence claim {claim_id} has commands outside scope {scope_id}: "
                + ", ".join(outside_scope)
            )
        claims.append(
            {
                "id": claim_id,
                "kind": _evidence_text(
                    raw.get("kind"), label=f"Developer evidence claim {claim_id} kind"
                ),
                "predicate": _evidence_text(
                    raw.get("predicate"), label=f"Developer evidence claim {claim_id} predicate"
                ),
                "scope_id": scope_id,
                "supporting_command_ids": supporting_ids,
            }
        )
    claim_ids = [item["id"] for item in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise WorkflowDataError("Developer evidence claim IDs must be unique.")

    handoff = evidence.get("handoff")
    if not isinstance(handoff, dict):
        raise WorkflowDataError("Developer evidence handoff must be an object.")
    handoff_claim_ids = _evidence_text_set(
        handoff.get("claim_ids"), label="Developer evidence handoff claim_ids", non_empty=True
    )
    if handoff_claim_ids != sorted(claim_ids):
        raise WorkflowDataError("Developer evidence handoff must reference every claim exactly once.")
    used_scopes = {item["scope_id"] for item in claims}
    if used_scopes != set(scope_ids):
        raise WorkflowDataError("Every Developer evidence scope must support at least one claim.")
    used_commands = {
        command_id for item in claims for command_id in item["supporting_command_ids"]
    }
    if used_commands != set(command_ids):
        raise WorkflowDataError("Every Developer evidence command must support at least one claim.")

    return {
        "evidence_contract_version": 1,
        "agent_id": _evidence_text(evidence.get("agent_id"), label="Developer evidence agent_id"),
        "scopes": sorted(scopes, key=lambda item: item["id"]),
        "commands": sorted(commands, key=lambda item: item["id"]),
        "claims": sorted(claims, key=lambda item: item["id"]),
        "handoff": {
            "claim_ids": handoff_claim_ids,
            "remaining_risks": _evidence_text_set(
                handoff.get("remaining_risks"), label="Developer evidence handoff remaining_risks"
            ),
            "review_focus": _evidence_text_set(
                handoff.get("review_focus"), label="Developer evidence handoff review_focus"
            ),
        },
    }


def evidence_claim_fingerprint(
    snapshot_id_value: str,
    claim: dict[str, Any],
    scope: dict[str, Any],
    supporting_commands: list[dict[str, Any]],
) -> str:
    """Bind one claim to its sealed snapshot and complete evidence closure."""

    return sha256_json(
        {
            "algorithm": "codex-evidence-claim-v1",
            "snapshot_id": snapshot_id_value,
            "claim": claim,
            "scope": scope,
            "supporting_commands": sorted(supporting_commands, key=lambda item: item["id"]),
        }
    )


def prepare_developer_evidence(
    paths: WorkflowPaths,
    evidence: Any,
    *,
    snapshot_id_value: str,
    delivery: dict[str, Any],
) -> dict[str, Any]:
    validate_workflow_schema(
        paths,
        "developer-evidence-v1.schema.json",
        evidence,
        label="Developer evidence",
    )
    normalized = _normalize_developer_evidence(evidence)
    for scope in normalized["scopes"]:
        if scope["kind"] == "canonical_delivery_paths" and scope["targets"] != delivery["changed_paths"]:
            raise WorkflowDataError(
                "canonical_delivery_paths evidence must exactly match the canonical delivery changed_paths."
            )
    scope_by_id = {item["id"]: item for item in normalized["scopes"]}
    command_by_id = {item["id"]: item for item in normalized["commands"]}
    bound_claims: list[dict[str, Any]] = []
    for claim in normalized["claims"]:
        fingerprint = evidence_claim_fingerprint(
            snapshot_id_value,
            claim,
            scope_by_id[claim["scope_id"]],
            [command_by_id[item] for item in claim["supporting_command_ids"]],
        )
        bound_claims.append({**claim, "evidence_fingerprint": fingerprint})
    return {**normalized, "snapshot_id": snapshot_id_value, "claims": bound_claims}


def validate_developer_evidence(
    paths: WorkflowPaths,
    developer: Any,
    *,
    snapshot_id_value: str,
    delivery: dict[str, Any],
) -> dict[str, str]:
    if not isinstance(developer, dict):
        raise WorkflowDataError("Recorded Developer evidence must be an object.")
    if developer.get("snapshot_id") != snapshot_id_value:
        raise WorkflowDataError("Developer evidence is not bound to the sealed snapshot.")
    raw_claims = developer.get("claims")
    projected_claims: Any = raw_claims
    if isinstance(raw_claims, list):
        projected_claims = []
        for item in raw_claims:
            if isinstance(item, dict):
                projected = dict(item)
                projected.pop("evidence_fingerprint", None)
                projected_claims.append(projected)
            else:
                projected_claims.append(item)
    projected = {
        key: developer.get(key)
        for key in (
            "evidence_contract_version",
            "agent_id",
            "scopes",
            "commands",
            "handoff",
        )
    }
    projected["claims"] = projected_claims
    rebound = prepare_developer_evidence(
        paths,
        projected,
        snapshot_id_value=snapshot_id_value,
        delivery=delivery,
    )
    recorded_by_id = {
        item.get("id"): item
        for item in raw_claims or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    fingerprints: dict[str, str] = {}
    for claim in rebound["claims"]:
        recorded = recorded_by_id.get(claim["id"])
        if not isinstance(recorded, dict) or recorded.get("evidence_fingerprint") != claim["evidence_fingerprint"]:
            raise WorkflowDataError(
                f"Developer evidence claim {claim['id']} fingerprint does not match its evidence closure."
            )
        fingerprints[claim["id"]] = claim["evidence_fingerprint"]
    return fingerprints


def prepare_review_evidence(
    paths: WorkflowPaths,
    review: Any,
    *,
    claim_fingerprints: dict[str, str],
) -> dict[str, Any]:
    validate_workflow_schema(
        paths,
        "review-evidence-v1.schema.json",
        review,
        label="Review evidence",
    )
    assert isinstance(review, dict)
    assessments: list[dict[str, Any]] = []
    for raw in review["claim_assessments"]:
        claim_id = _evidence_text(raw.get("claim_id"), label="Review claim assessment claim_id")
        assessments.append(
            {
                "claim_id": claim_id,
                "assessment": raw.get("assessment"),
                "evidence_fingerprint": raw.get("evidence_fingerprint"),
                "notes": _evidence_text(
                    raw.get("notes"), label=f"Review claim assessment {claim_id} notes"
                ),
            }
        )
    assessment_ids = [item["claim_id"] for item in assessments]
    if len(assessment_ids) != len(set(assessment_ids)):
        raise WorkflowDataError("Review claim assessment IDs must be unique.")
    if set(assessment_ids) != set(claim_fingerprints):
        raise WorkflowDataError("Review must assess every Developer evidence claim exactly once.")
    for assessment in assessments:
        if assessment["evidence_fingerprint"] != claim_fingerprints[assessment["claim_id"]]:
            raise WorkflowDataError(
                f"Review claim {assessment['claim_id']} fingerprint does not match Developer evidence."
            )
    all_confirmed = all(item["assessment"] == "confirmed" for item in assessments)
    if review.get("status") == "pass" and not all_confirmed:
        raise WorkflowDataError("Review status pass requires every claim assessment to be confirmed.")
    if not all_confirmed and review.get("status") != "changes_requested":
        raise WorkflowDataError(
            "A narrowed, rejected, or unverified claim requires review status changes_requested."
        )
    if review.get("status") == "pass":
        findings = review.get("findings") or {}
        if findings.get("p0") != 0 or findings.get("p1") != 0:
            raise WorkflowDataError("Review status pass cannot contain unresolved P0/P1 findings.")
        accepted = review.get("accepted_findings") or []
        accepted_ids = [item.get("id") for item in accepted if isinstance(item, dict)]
        if len(accepted_ids) != len(set(accepted_ids)):
            raise WorkflowDataError("Accepted Review finding IDs must be unique.")
        accepted_p2 = sum(
            1
            for item in accepted
            if isinstance(item, dict) and item.get("severity") == "p2"
        )
        if accepted_p2 != findings.get("p2"):
            raise WorkflowDataError(
                "Every P2 finding in a passing Review requires one explicit P2 acceptance."
            )
        accepted_p3 = sum(
            1
            for item in accepted
            if isinstance(item, dict) and item.get("severity") == "p3"
        )
        if accepted_p3 != findings.get("p3"):
            raise WorkflowDataError(
                "Every P3 finding in a passing Review requires one explicit P3 acceptance."
            )
    return {
        "evidence_contract_version": 1,
        "agent_id": review["agent_id"],
        "snapshot_id": review["snapshot_id"],
        "status": review["status"],
        "findings": review["findings"],
        "requirement_checklist": review["requirement_checklist"],
        "accepted_findings": review["accepted_findings"],
        "claim_assessments": sorted(assessments, key=lambda item: item["claim_id"]),
        "summary": review["summary"],
    }


_SUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "title",
        "description",
        "$ref",
        "oneOf",
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
        "additionalProperties",
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

    if "$defs" in schema:
        definitions = schema["$defs"]
        if not isinstance(definitions, dict) or any(
            not isinstance(key, str) for key in definitions
        ):
            raise WorkflowDataError(
                f"Invalid JSON Schema {schema_path.name} at {location}: $defs must be an object with string names."
            )
        for key, child in definitions.items():
            _validate_json_schema_definition(
                child,
                schema_path=schema_path,
                schema_root=schema_root,
                cache=cache,
                ref_chain=ref_chain,
                location=_schema_path(f"{location}.$defs", key),
            )
    if "oneOf" in schema:
        alternatives = schema["oneOf"]
        if not isinstance(alternatives, list) or not alternatives:
            raise WorkflowDataError(
                f"Invalid JSON Schema {schema_path.name} at {location}: oneOf must be a non-empty array."
            )
        for index, child in enumerate(alternatives):
            _validate_json_schema_definition(
                child,
                schema_path=schema_path,
                schema_root=schema_root,
                cache=cache,
                ref_chain=ref_chain,
                location=f"{location}.oneOf[{index}]",
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
    if "additionalProperties" in schema:
        additional = schema["additionalProperties"]
        if not isinstance(additional, (dict, bool)):
            raise WorkflowDataError(
                f"Invalid JSON Schema {schema_path.name} at {location}: additionalProperties must be an object or boolean."
            )
        _validate_json_schema_definition(
            additional,
            schema_path=schema_path,
            schema_root=schema_root,
            cache=cache,
            ref_chain=ref_chain,
            location=f"{location}.additionalProperties",
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

    if "oneOf" in schema:
        alternatives = schema["oneOf"]
        if not isinstance(alternatives, list) or not alternatives:
            errors.append(f"{path}: schema oneOf must be a non-empty array")
            return
        branch_errors: list[list[str]] = []
        matches = 0
        for alternative in alternatives:
            candidate_errors: list[str] = []
            _validate_json_schema_node(
                alternative,
                value,
                path=path,
                schema_path=schema_path,
                schema_root=schema_root,
                cache=cache,
                ref_chain=ref_chain,
                errors=candidate_errors,
            )
            branch_errors.append(candidate_errors)
            if not candidate_errors:
                matches += 1
        if matches != 1:
            errors.append(f"{path}: must match exactly one oneOf branch; matched {matches}")
            if matches == 0:
                errors.extend(min(branch_errors, key=len))

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
        if "additionalProperties" in schema:
            properties = schema.get("properties", {})
            if not isinstance(properties, dict):
                errors.append(f"{path}: schema properties must be an object")
                return
            additional = schema["additionalProperties"]
            for key in sorted(set(value) - set(properties)):
                child_path = _schema_path(path, key)
                if additional is False:
                    errors.append(f"{child_path}: additional property is not allowed")
                elif additional is not True:
                    _validate_json_schema_node(
                        additional,
                        value[key],
                        path=child_path,
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
    changed_paths = sorted({row["new_path"] for row in entries})
    return {
        "base_commit": base_oid,
        "target_commit": target_oid,
        "entries": entries,
        "changed_paths": changed_paths,
        "scope": {
            "kind": "base_to_target_product_delta",
            "base_commit": base_oid,
            "target_commit": target_oid,
            "included_paths": changed_paths,
            "excluded_path_class": "mutable_workflow_control",
        },
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


def _primary_worktree_paths(paths: WorkflowPaths) -> WorkflowPaths:
    """Resolve the primary/Coordinator worktree for linked-lane reads."""

    if resolve_path(paths.git_dir, label="current Git directory") == resolve_path(
        paths.common_dir, label="Git common directory"
    ):
        return paths
    try:
        output = git(paths, "worktree", "list", "--porcelain").stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise WorkflowDataError(
            "Unable to resolve the Coordinator primary worktree for live Requirements."
        ) from exc
    first = next(
        (line for line in output.splitlines() if line.startswith("worktree ")),
        None,
    )
    if first is None or not first[len("worktree ") :].strip():
        raise WorkflowDataError(
            "Git worktree metadata has no primary worktree for live Requirements."
        )
    primary_value = first[len("worktree ") :].strip()
    primary_root = Path(primary_value)
    if not primary_root.is_absolute():
        primary_root = paths.common_dir.parent / primary_root
    try:
        primary = WorkflowPaths.discover(primary_root)
    except (WorkflowDataError, WorkflowPathError, OSError) as exc:
        raise WorkflowDataError(
            "Unable to inspect the Coordinator primary worktree for live Requirements."
        ) from exc
    if resolve_path(primary.common_dir, label="Coordinator Git common directory") != resolve_path(
        paths.common_dir, label="Git common directory"
    ):
        raise WorkflowDataError(
            "Coordinator primary worktree does not share the current Git common directory."
        )
    return primary


def _validate_v4_architecture_in_worktree(
    paths: WorkflowPaths, record: dict[str, Any]
) -> str:
    architecture = ((record.get("delivery_contract") or {}).get("architecture") or {})
    reference = architecture.get("baseline") or {}
    source = reference.get("source")
    if not isinstance(source, str) or not source:
        raise WorkflowDataError("V4 architecture baseline source must be non-empty text.")
    candidate = resolve_path(paths.root / source, label="V4 architecture baseline")
    if paths.root not in candidate.parents or not candidate.is_file():
        raise WorkflowPathError(
            "V4 architecture baseline must be an existing project file."
        )
    baseline = read_embedded_json(
        candidate.read_text(encoding="utf-8"), "CODEX_ARCHITECTURE_BASELINE"
    )
    fingerprint = architecture_baseline_fingerprint(baseline)
    approval = baseline.get("approval") or {}
    if baseline.get("status") != "approved":
        raise WorkflowDataError(
            "V4 architecture baseline must be approved before task execution."
        )
    if approval.get("approved_fingerprint") != fingerprint:
        raise WorkflowDataError("V4 architecture baseline approval fingerprint is stale.")
    if reference.get("revision") != baseline.get("revision"):
        raise WorkflowDataError("V4 task architecture baseline revision is stale.")
    if reference.get("fingerprint") != fingerprint:
        raise WorkflowDataError("V4 task architecture baseline fingerprint is stale.")
    baseline_id = baseline.get("baseline_id")
    baseline_guardrails = baseline.get("guardrails")
    inline_guardrails = architecture.get("guardrails")
    if not isinstance(baseline_id, str) or not baseline_id:
        raise WorkflowDataError("V4 live architecture baseline ID is invalid.")
    if not isinstance(baseline_guardrails, list) or not isinstance(inline_guardrails, list):
        raise WorkflowDataError("V4 architecture guardrails must be arrays.")
    baseline_by_id: dict[str, dict[str, Any]] = {}
    for item in baseline_guardrails:
        guardrail = _v4_object(item, label="Live architecture guardrail")
        guardrail_id = guardrail.get("id")
        if not isinstance(guardrail_id, str) or guardrail_id in baseline_by_id:
            raise WorkflowDataError("Live architecture guardrail IDs must be unique text.")
        baseline_by_id[guardrail_id] = guardrail
    inline_ids: set[str] = set()
    acceptance_ids = {
        item.get("id")
        for item in record.get("acceptance") or []
        if isinstance(item, dict)
    }
    for item in inline_guardrails:
        guardrail = _v4_object(item, label="V4 task architecture guardrail")
        guardrail_id = guardrail.get("id")
        if not isinstance(guardrail_id, str) or guardrail_id in inline_ids:
            raise WorkflowDataError("V4 task architecture guardrail IDs must be unique text.")
        inline_ids.add(guardrail_id)
        live_guardrail = baseline_by_id.get(guardrail_id)
        if live_guardrail is None:
            raise WorkflowDataError(
                f"V4 task architecture guardrail {guardrail_id} is absent from the live baseline."
            )
        if guardrail.get("source") != f"decision:{baseline_id}":
            raise WorkflowDataError(
                f"V4 task architecture guardrail {guardrail_id} has an invalid baseline source."
            )
        if guardrail.get("acceptance_id") not in acceptance_ids:
            raise WorkflowDataError(
                f"V4 task architecture guardrail {guardrail_id} has no task acceptance binding."
            )
        if guardrail.get("statement") != live_guardrail.get("statement"):
            raise WorkflowDataError(
                f"V4 task architecture guardrail {guardrail_id} statement differs from the live baseline."
            )
        if sorted(
            guardrail.get("verification_refs") or [], key=canonical_v4_json_bytes
        ) != sorted(
            live_guardrail.get("verification_refs") or [], key=canonical_v4_json_bytes
        ):
            raise WorkflowDataError(
                f"V4 task architecture guardrail {guardrail_id} verification refs differ from the live baseline."
            )
    return fingerprint


def validate_v4_live_architecture_baseline(
    paths: WorkflowPaths, record: dict[str, Any]
) -> str:
    """Validate local and Coordinator architecture truth for every V4 action."""

    local_fingerprint = _validate_v4_architecture_in_worktree(paths, record)
    live_paths = _primary_worktree_paths(paths)
    if live_paths.root == paths.root:
        return local_fingerprint
    return _validate_v4_architecture_in_worktree(live_paths, record)


def _validate_v4_contract_references(
    paths: WorkflowPaths, record: dict[str, Any], brief_path: Path
) -> None:
    brief = read_requirements_brief(
        brief_path,
        schema_path=paths.tracked("schemas") / "requirements-v1.schema.json",
    )
    requirements = brief.metadata.get("requirements")
    if not isinstance(requirements, dict):
        raise WorkflowDataError("Approved Requirements Brief has no requirements object.")
    available_requirement_ids: set[str] = set()
    for section in (
        "users",
        "outcomes",
        "flows",
        "capabilities",
        "scenarios",
        "non_goals",
        "constraints",
        "open_questions",
    ):
        items = requirements.get(section)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                available_requirement_ids.add(item["id"])
    contract = _v4_object(
        _v4_required(record, "delivery_contract", label="V4 task record"),
        label="V4 delivery_contract",
    )
    requirement_ids = contract.get("requirement_ids")
    if not isinstance(requirement_ids, list) or any(
        not isinstance(item, str) for item in requirement_ids
    ):
        raise WorkflowDataError("V4 delivery_contract requirement_ids must be a text array.")
    missing_requirements = sorted(set(requirement_ids) - available_requirement_ids)
    if missing_requirements:
        raise WorkflowDataError(
            "V4 delivery_contract references Requirements IDs absent from the live approved Brief: "
            + ", ".join(missing_requirements)
            + "."
        )
    acceptance = record.get("acceptance")
    if not isinstance(acceptance, list):
        raise WorkflowDataError("V4 task acceptance must be an array.")
    available_acceptance_ids = {
        item.get("id") for item in acceptance if isinstance(item, dict)
    }
    acceptance_ids = contract.get("acceptance_ids")
    if not isinstance(acceptance_ids, list) or any(
        not isinstance(item, str) for item in acceptance_ids
    ):
        raise WorkflowDataError("V4 delivery_contract acceptance_ids must be a text array.")
    missing_acceptance = sorted(set(acceptance_ids) - available_acceptance_ids)
    if missing_acceptance:
        raise WorkflowDataError(
            "V4 delivery_contract references acceptance IDs absent from task acceptance: "
            + ", ".join(missing_acceptance)
            + "."
        )


def _require_live_requirements_gate(paths: WorkflowPaths, brief_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(paths.tracked("bin") / "workflow_check.py"),
            "requirements-gate",
            paths.relative(brief_path),
        ],
        cwd=paths.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise WorkflowDataError((result.stderr or result.stdout).strip())


def validate_v4_live_requirements_baseline(
    paths: WorkflowPaths, record: dict[str, Any]
) -> dict[str, Any]:
    """Bind every V4 action to the task, managed, and live approved baselines."""

    if record.get("version") != 4:
        raise WorkflowDataError("Live V4 Requirements validation requires a V4 task record.")
    task_baseline = _v4_requirements_baseline(record)
    local_managed, local_current, local_brief_path = current_requirements_baseline(paths)
    if task_baseline != local_managed:
        raise WorkflowDataError(
            "V4 task Requirements baseline is stale relative to PROJECT/Backlog."
        )
    if local_current is None or local_brief_path is None:
        raise WorkflowDataError("V4 task requires a configured live Requirements Brief.")
    if local_current != local_managed:
        raise WorkflowDataError(
            "V4 live Requirements Brief differs from the PROJECT/Backlog baseline: "
            + paths.relative(local_brief_path)
            + "."
        )
    live_paths = _primary_worktree_paths(paths)
    if live_paths.root != paths.root:
        managed, current, brief_path = current_requirements_baseline(live_paths)
        if task_baseline != managed:
            raise WorkflowDataError(
                "V4 task Requirements baseline is stale relative to Coordinator PROJECT/Backlog."
            )
        if current is None or brief_path is None:
            raise WorkflowDataError(
                "Coordinator worktree has no configured live Requirements Brief."
            )
        if current != managed:
            raise WorkflowDataError(
                "Coordinator live Requirements Brief differs from the PROJECT/Backlog baseline: "
                + live_paths.relative(brief_path)
                + "."
            )
    else:
        managed, brief_path = local_managed, local_brief_path
    _require_live_requirements_gate(live_paths, brief_path)
    _validate_v4_contract_references(live_paths, record, brief_path)
    return dict(managed)


def validate_v4_live_dependencies(
    paths: WorkflowPaths,
    record: dict[str, Any],
    *,
    _ancestors: frozenset[str] = frozenset(),
) -> None:
    """Recheck every V4 dependency at the same live boundary as its consumer."""

    contract = record.get("delivery_contract") or {}
    references = contract.get("dependency_refs") or []
    if not isinstance(references, list):
        raise WorkflowDataError("V4 delivery_contract dependency_refs must be an array.")
    task_id = record.get("task_id")
    current_ancestors = set(_ancestors)
    if isinstance(task_id, str):
        if task_id in current_ancestors:
            raise WorkflowDataError(f"V4 dependency cycle detected at {task_id}.")
        current_ancestors.add(task_id)
    for reference in references:
        if not isinstance(reference, dict):
            raise WorkflowDataError("V4 dependency reference must be an object.")
        dependency_id = reference.get("task_id")
        if not isinstance(dependency_id, str) or not re.fullmatch(
            r"[A-Za-z0-9._-]+", dependency_id
        ):
            raise WorkflowDataError("V4 dependency task_id must be a safe record identifier.")
        if dependency_id in current_ancestors:
            raise WorkflowDataError(f"V4 dependency cycle detected at {dependency_id}.")
        dependency_path = paths.tracked("runs") / f"{dependency_id}.json"
        if not dependency_path.is_file():
            raise WorkflowDataError(f"V4 dependency task record is missing: {dependency_id}.")
        _, dependency = load_record(paths, paths.relative(dependency_path))
        if dependency.get("version") != 4:
            continue
        validate_v4_live_requirements_baseline(paths, dependency)
        validate_v4_live_architecture_baseline(paths, dependency)
        validate_v4_live_focus_relationship(paths, dependency)
        validate_v4_current_observation_continuations(dependency)
        blockers = v4_action_blockers(dependency, "dependency")
        if blockers:
            raise WorkflowDataError(
                f"V4 dependency {dependency_id} blocked: " + "; ".join(blockers) + "."
            )
        validate_v4_live_dependencies(
            paths,
            dependency,
            _ancestors=frozenset(current_ancestors),
        )


def validate_v4_live_focus_relationship(
    paths: WorkflowPaths, record: dict[str, Any]
) -> None:
    """Bind every V4 task to one existing and current core slice."""

    contract = record.get("delivery_contract") or {}
    kind = contract.get("kind")
    focus_id = contract.get("focus_slice_id")
    task_id = record.get("task_id")
    if kind == "core_slice":
        if focus_id != task_id:
            raise WorkflowDataError(
                "V4 core slice focus_slice_id must equal its task_id."
            )
        return
    if kind not in {"supporting", "hardening", "governance"}:
        raise WorkflowDataError(f"Unsupported V4 focus relationship kind: {kind!r}.")
    if (
        not isinstance(focus_id, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]+", focus_id)
    ):
        raise WorkflowDataError(
            f"V4 {kind} task must identify one safe focus core slice."
        )
    supports_id = contract.get("supports_task_id")
    if kind == "supporting" and supports_id != focus_id:
        raise WorkflowDataError(
            "V4 supporting task must identify one matching focus core slice."
        )
    if kind in {"hardening", "governance"} and supports_id not in {None, focus_id}:
        raise WorkflowDataError(
            f"V4 {kind} supports_task_id must be null or match focus_slice_id."
        )
    focus_path = paths.tracked("runs") / f"{focus_id}.json"
    if not focus_path.is_file():
        raise WorkflowDataError(f"V4 {kind} focus task record is missing: {focus_id}.")
    _, focus = load_record(paths, paths.relative(focus_path))
    focus_contract = focus.get("delivery_contract") or {}
    if (
        focus.get("version") != 4
        or focus.get("task_id") != focus_id
        or focus_contract.get("kind") != "core_slice"
        or focus_contract.get("focus_slice_id") != focus_id
    ):
        raise WorkflowDataError(
            f"V4 {kind} focus {focus_id} is not its matching V4 core slice."
        )
    validate_v4_live_requirements_baseline(paths, focus)
    validate_v4_live_architecture_baseline(paths, focus)
    validate_v4_contract_identity(focus)
    if kind in {"hardening", "governance"} and supports_id is None:
        return
    supporting_id = task_id
    reciprocal = [
        reference
        for reference in focus_contract.get("dependency_refs") or []
        if isinstance(reference, dict) and reference.get("task_id") == supporting_id
    ]
    if len(reciprocal) != 1 or reciprocal[0].get("closeout_ref") != f"task:{supporting_id}":
        raise WorkflowDataError(
            f"V4 focus core slice {focus_id} does not reciprocally bind supporting task {supporting_id}."
        )


def v4_dependency_snapshot(
    paths: WorkflowPaths, record: dict[str, Any]
) -> list[dict[str, Any]]:
    """Capture the dependency identities bound to a queue or integration witness."""

    snapshot: dict[str, dict[str, Any]] = {}

    def visit(current: dict[str, Any], ancestors: frozenset[str]) -> None:
        references = ((current.get("delivery_contract") or {}).get("dependency_refs") or [])
        if not isinstance(references, list):
            raise WorkflowDataError("V4 delivery_contract dependency_refs must be an array.")
        for reference in references:
            if not isinstance(reference, dict):
                raise WorkflowDataError("V4 dependency reference must be an object.")
            dependency_id = reference.get("task_id")
            if not isinstance(dependency_id, str) or not re.fullmatch(
                r"[A-Za-z0-9._-]+", dependency_id
            ):
                raise WorkflowDataError("V4 dependency task_id must be a safe record identifier.")
            if dependency_id in ancestors:
                raise WorkflowDataError(f"V4 dependency cycle detected at {dependency_id}.")
            dependency_path = paths.tracked("runs") / f"{dependency_id}.json"
            if not dependency_path.is_file():
                raise WorkflowDataError(
                    f"V4 dependency task record is missing: {dependency_id}."
                )
            _, dependency = load_record(paths, paths.relative(dependency_path))
            identity = {
                "task_id": dependency_id,
                "version": dependency.get("version"),
                "generation": dependency.get("generation"),
                "contract_fingerprint": dependency.get("contract_fingerprint"),
                "requirements_baseline": (dependency.get("source") or {}).get(
                    "requirements_baseline"
                ),
            }
            previous = snapshot.get(dependency_id)
            if previous is not None and previous != identity:
                raise WorkflowDataError(
                    f"V4 dependency {dependency_id} changed while capturing its snapshot."
                )
            snapshot[dependency_id] = identity
            if dependency.get("version") == 4:
                visit(dependency, ancestors | {dependency_id})

    root_id = record.get("task_id")
    visit(record, frozenset({root_id}) if isinstance(root_id, str) else frozenset())
    return [snapshot[key] for key in sorted(snapshot)]


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
    material = {
        "task_id": record.get("task_id"),
        "lane_id": lane.get("lane_id"),
        "base_commit": record.get("base_commit") or lane.get("base_commit"),
        "branch": lane.get("branch"),
        "delivery_hash": delivery_hash,
    }
    if record.get("version") == 4:
        contract_identity = record.get("contract_fingerprint")
        if not isinstance(contract_identity, str) or not re.fullmatch(
            r"[0-9a-f]{64}", contract_identity
        ):
            raise WorkflowDataError(
                "V4 snapshot identity requires a valid contract_fingerprint."
            )
        material["contract_fingerprint"] = contract_identity
    return sha256_json(material)


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
    # V2 records remain readable for historical gate compatibility. V3 and V4
    # dispatch to their exact schemas before any caller can inspect them.
    if record.get("version") != 2:
        validate_workflow_schema(
            paths,
            task_record_schema_name(record),
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


def _v4_is_v4_record(record: dict[str, Any]) -> bool:
    return isinstance(record, dict) and record.get("version") == 4


def _v4_acceptance_result(record: dict[str, Any]) -> str:
    contract = record.get("delivery_contract") if isinstance(record.get("delivery_contract"), dict) else {}
    wanted = contract.get("acceptance_ids") if isinstance(contract.get("acceptance_ids"), list) else []
    by_id = {
        item.get("id"): item
        for item in record.get("acceptance") or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for acceptance_id in wanted:
        if not isinstance(acceptance_id, str):
            continue
        item = by_id.get(acceptance_id)
        criterion = item.get("criterion") if isinstance(item, dict) else None
        if isinstance(criterion, str) and criterion.strip():
            return criterion.strip()
    request = record.get("request")
    if isinstance(request, str) and request.strip():
        return request.strip()
    return "Current focus slice has no observable acceptance text."


def _v4_receipt_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    current_snapshot = (record.get("verification") or {}).get("snapshot_id")
    if not isinstance(current_snapshot, str) or not current_snapshot:
        return None
    matched = None
    for decision in record.get("decision_log") or []:
        if not isinstance(decision, dict) or decision.get("kind") != "product_checkpoint":
            continue
        receipt = decision.get("observation_receipt")
        if not isinstance(receipt, dict):
            continue
        if receipt.get("snapshot_id") == current_snapshot:
            matched = receipt
    return matched


def _v4_observation_view(record: dict[str, Any]) -> dict[str, Any]:
    contract = record.get("delivery_contract") if isinstance(record.get("delivery_contract"), dict) else {}
    recipe = contract.get("observation") if isinstance(contract.get("observation"), dict) else {}
    current_snapshot = (record.get("verification") or {}).get("snapshot_id")
    has_snapshot = isinstance(current_snapshot, str) and bool(current_snapshot)
    receipt = _v4_receipt_from_record(record)
    recipe_method = recipe.get("method") if isinstance(recipe.get("method"), str) else None
    entrypoint = recipe.get("entrypoint_ref")
    steps = recipe.get("steps") if isinstance(recipe.get("steps"), list) else []
    health = "unknown"
    real: list[str] = []
    temporary = (
        list(contract.get("known_placeholders") or [])
        if isinstance(contract.get("known_placeholders"), list)
        else []
    )
    material: list[str] = []
    assumptions: list[str] = []
    if isinstance(receipt, dict):
        entry = receipt.get("entrypoint") if isinstance(receipt.get("entrypoint"), dict) else {}
        if isinstance(entry.get("reference"), str):
            entrypoint = entry.get("reference")
        if isinstance(entry.get("status"), str):
            health = entry.get("status")
        for field, bucket in (
            ("real_components", real),
            ("temporary_components", temporary),
            ("material_changes", material),
            ("reversible_assumptions", assumptions),
        ):
            values = receipt.get(field)
            if isinstance(values, list):
                bucket.extend(item for item in values if isinstance(item, str) and item.strip())
        result = receipt.get("normalized_result") if isinstance(receipt.get("normalized_result"), dict) else {}
        for assertion in result.get("assertions") or []:
            if isinstance(assertion, dict) and assertion.get("status") == "passed":
                actual = assertion.get("actual")
                if isinstance(actual, str) and actual.strip():
                    real.append(actual.strip())
                    break
    elif has_snapshot:
        health = "no_current_receipt"
    changed_paths = (record.get("verification") or {}).get("changed_paths")
    if isinstance(changed_paths, list) and not material:
        classes: list[str] = []
        for path in changed_paths:
            if not isinstance(path, str):
                continue
            try:
                classes.append(v4_continuation_path_class(path))
            except WorkflowDataError:
                classes.append("product_or_architecture")
        unique: list[str] = []
        for item in classes:
            if item not in unique:
                unique.append(item)
        if unique:
            material = [f"Delivery path class: {item}" for item in unique]
    surface = recipe_method if recipe_method else "cli"
    return {
        "surface": surface,
        "surface_label": _V4_HUMAN_SURFACE_LABELS.get(surface, surface),
        "entrypoint_ref": entrypoint if isinstance(entrypoint, str) else None,
        "steps": [item for item in steps if isinstance(item, str) and item.strip()][:5],
        "health": health,
        "from_receipt": isinstance(receipt, dict),
        "missing_current_receipt": has_snapshot and not isinstance(receipt, dict),
        "real": real,
        "temporary": [item for item in temporary if isinstance(item, str) and item.strip()],
        "material_changes": material,
        "reversible_assumptions": assumptions,
    }


def _v4_blocking_open_decisions(record: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for decision in record.get("decision_log") or []:
        if not isinstance(decision, dict) or decision.get("status") != "open":
            continue
        decision_id = decision.get("id")
        if not isinstance(decision_id, str) or not decision_id:
            continue
        try:
            blocking = derive_v4_decision_blocking(record, decision)
        except WorkflowDataError:
            blocking = True
        if not blocking:
            continue
        options: list[dict[str, str]] = []
        for option in decision.get("options") or []:
            if not isinstance(option, dict):
                continue
            option_id = option.get("id")
            label = option.get("label")
            if isinstance(option_id, str) and isinstance(label, str):
                options.append({"id": option_id, "label": label})
            if len(options) >= 3:
                break
        items.append(
            {
                "id": decision_id,
                "kind": decision.get("kind"),
                "question": decision.get("question") if isinstance(decision.get("question"), str) else None,
                "options": options,
                "latest_decision_point": decision.get("latest_decision_point"),
            }
        )
    return items


def _v4_checkpoint_direction(record: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    contract = record.get("delivery_contract") if isinstance(record.get("delivery_contract"), dict) else {}
    checkpoint_policy = contract.get("checkpoint") if isinstance(contract.get("checkpoint"), dict) else {}
    mode = checkpoint_policy.get("mode")
    if mode == "not_required":
        return "not_required", None
    latest = None
    for decision in record.get("decision_log") or []:
        if isinstance(decision, dict) and decision.get("kind") == "product_checkpoint":
            latest = decision
    if latest is None:
        awaiting = mode in {"required", "show_before_dependency"}
        return ("awaiting_human" if awaiting else "not_required"), None
    if latest.get("status") == "open":
        deferrals = latest.get("deferrals") or []
        if isinstance(deferrals, list) and deferrals:
            return "deferred", latest
        return "awaiting_human", latest
    outcome = (latest.get("resolution") or {}).get("outcome") if isinstance(latest.get("resolution"), dict) else None
    if outcome == "accepted":
        try:
            current = _v4_checkpoint_is_current(record, latest)
        except WorkflowDataError:
            current = False
        if current:
            return "accepted", latest
        return "awaiting_human", latest
    if outcome == "changes_requested":
        if _v4_checkpoint_bound_to_current_snapshot(record, latest):
            return "changes_requested", latest
        return "awaiting_human", latest
    if outcome == "stopped":
        return "stopped", latest
    return "awaiting_human", latest


def _v4_checkpoint_bound_to_current_snapshot(
    record: dict[str, Any], decision: dict[str, Any]
) -> bool:
    current_snapshot = (record.get("verification") or {}).get("snapshot_id")
    if not isinstance(current_snapshot, str) or not current_snapshot:
        return False
    binding = decision.get("binding") if isinstance(decision.get("binding"), dict) else {}
    return binding.get("snapshot_id") == current_snapshot


def _v4_continuation_note(
    record: dict[str, Any], checkpoint: dict[str, Any] | None
) -> dict[str, Any] | None:
    if not isinstance(checkpoint, dict):
        return None
    current_snapshot = (record.get("verification") or {}).get("snapshot_id")
    if not isinstance(current_snapshot, str):
        return None
    binding = checkpoint.get("binding") if isinstance(checkpoint.get("binding"), dict) else {}
    if binding.get("snapshot_id") == current_snapshot:
        return None
    for continuation in checkpoint.get("continuations") or []:
        if not isinstance(continuation, dict):
            continue
        if continuation.get("target_snapshot_id") != current_snapshot:
            continue
        try:
            current = _v4_continuation_is_current(record, checkpoint, continuation)
        except (WorkflowDataError, TypeError, KeyError):
            return None
        if current:
            return {"source_decision_id": checkpoint.get("id"), "equivalent": True}
        return None
    return None


def _v4_next_action(record: dict[str, Any], summary_parts: dict[str, Any]) -> dict[str, Any]:
    blocking = summary_parts["blocking_decisions"]
    direction = summary_parts["product_direction"]
    observation = summary_parts["observation"]
    surface_label = observation.get("surface_label") or "observation"
    if blocking:
        decision_id = blocking[0]["id"]
        return {
            "kind": "wait_decision",
            "decision_id": decision_id,
            "text": f"等待人类决定 {decision_id}。不要批准、不要改状态、不要集成。",
        }
    snapshot_value = (record.get("verification") or {}).get("snapshot_id")
    mode = ((record.get("delivery_contract") or {}).get("checkpoint") or {}).get("mode")
    if isinstance(snapshot_value, str) and mode == "required" and direction in {"awaiting_human", "deferred"}:
        checkpoint = summary_parts.get("checkpoint")
        decision_id = checkpoint.get("id") if isinstance(checkpoint, dict) else None
        if isinstance(decision_id, str) and decision_id:
            decide_text = f"再决定 {decision_id} 的产品方向"
        else:
            decide_text = "再决定产品方向"
        return {
            "kind": "observe",
            "decision_id": decision_id if isinstance(decision_id, str) else None,
            "text": (
                f"先按 {surface_label} 入口观察当前结果，{decide_text}。"
                "不要批准、不要改状态、不要集成。"
            ),
        }
    if direction == "changes_requested":
        return {
            "kind": "return_developer",
            "decision_id": None,
            "text": "当前 snapshot 被 changes_requested，回到 Developer。不要报告 verified 或 done。",
        }
    if direction == "stopped":
        return {
            "kind": "stopped",
            "decision_id": None,
            "text": "当前切片已 stopped。不要报告 verified、done 或继续集成。",
        }
    return {"kind": "lifecycle", "decision_id": None, "text": None}


def derive_v4_product_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Pure product card for STATUS/Stop Hook. V3 records have no product card."""

    technical = {
        "status": record.get("status") if isinstance(record, dict) else None,
        "phase": record.get("phase") if isinstance(record, dict) else None,
        "verification": (
            (record.get("verification") or {}).get("status")
            if isinstance(record, dict) and isinstance(record.get("verification"), dict)
            else None
        ),
        "integration": (
            (record.get("integration") or {}).get("status")
            if isinstance(record, dict) and isinstance(record.get("integration"), dict)
            else None
        ),
    }
    empty_action = {"kind": "lifecycle", "decision_id": None, "text": None}
    if not _v4_is_v4_record(record):
        return redact_v4_human_value(
            {
                "has_product_card": False,
                "reason": "v3_technical_only",
                "core_result": None,
                "focus_slice_id": None,
                "kind": None,
                "supports_task_id": None,
                "supporting_cannot_claim_core_complete": False,
                "observation": None,
                "real_vs_temporary": None,
                "material_changes": [],
                "product_direction": None,
                "blocking_decisions": [],
                "architecture": None,
                "continuation": None,
                "next_action": empty_action,
                "technical": technical,
            }
        )
    contract = record.get("delivery_contract") if isinstance(record.get("delivery_contract"), dict) else {}
    kind = contract.get("kind")
    focus = contract.get("focus_slice_id")
    supports = contract.get("supports_task_id")
    supporting = kind == "supporting" or (
        isinstance(supports, str) and supports and supports != record.get("task_id")
    )
    observation = _v4_observation_view(record)
    direction, checkpoint = _v4_checkpoint_direction(record)
    blocking = _v4_blocking_open_decisions(record)
    architecture = contract.get("architecture") if isinstance(contract.get("architecture"), dict) else {}
    guardrails = architecture.get("guardrails") if isinstance(architecture.get("guardrails"), list) else []
    guardrail_ids = [
        item.get("id")
        for item in guardrails
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    execution_mode = contract.get("execution_mode")
    if execution_mode not in {"formal", "exploratory"}:
        execution_mode = None
    if supporting:
        focus_text = focus if isinstance(focus, str) else "unknown-focus"
        core_result = (
            f"本任务是 supporting，服务于 {focus_text}；supporting 完成不等于核心已完成。"
        )
    elif observation["from_receipt"] and observation["real"]:
        core_result = observation["real"][0]
    else:
        core_result = _v4_acceptance_result(record)
    next_action = _v4_next_action(
        record,
        {
            "blocking_decisions": blocking,
            "product_direction": direction,
            "observation": observation,
            "checkpoint": checkpoint,
        },
    )
    return redact_v4_human_value(
        {
            "has_product_card": True,
            "core_result": core_result,
            "focus_slice_id": focus if isinstance(focus, str) else None,
            "kind": kind if isinstance(kind, str) else None,
            "supports_task_id": supports if isinstance(supports, str) else None,
            "supporting_cannot_claim_core_complete": supporting,
            "observation": {
                "surface": observation["surface"],
                "surface_label": observation["surface_label"],
                "entrypoint_ref": observation["entrypoint_ref"],
                "steps": observation["steps"],
                "health": observation["health"],
                "from_receipt": observation["from_receipt"],
                "missing_current_receipt": observation["missing_current_receipt"],
            },
            "real_vs_temporary": {
                "real": observation["real"],
                "temporary": observation["temporary"],
                "execution_mode": execution_mode,
            },
            "material_changes": observation["material_changes"],
            "reversible_assumptions": observation["reversible_assumptions"],
            "product_direction": direction,
            "blocking_decisions": blocking,
            "architecture": {
                "declared_impact": architecture.get("declared_impact"),
                "guardrail_ids": guardrail_ids,
                "guardrail_count": len(guardrail_ids),
                "live_status": "not_checked",
            },
            "continuation": _v4_continuation_note(record, checkpoint),
            "next_action": next_action,
            "technical": technical,
        }
    )


def _v4_architecture_live_status(paths: WorkflowPaths, record: dict[str, Any]) -> str:
    """Read-only live baseline/guardrail check. Never apply or mutate state."""

    try:
        _validate_v4_architecture_in_worktree(paths, record)
        return "verified"
    except (WorkflowDataError, WorkflowPathError, OSError) as exc:
        if "stale" in str(exc).lower():
            return "stale"
        return "unverified"


def v4_stop_hook_next_action(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return one product next action, or None to keep V3 lifecycle messaging."""

    summary = derive_v4_product_summary(record)
    action = summary.get("next_action") if isinstance(summary, dict) else None
    if not isinstance(action, dict) or action.get("kind") == "lifecycle":
        return None
    text = action.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return {
        "kind": action.get("kind"),
        "decision_id": action.get("decision_id"),
        "text": text.strip(),
    }


def _product_card_lines(task_id: str, summary: dict[str, Any]) -> list[str]:
    if not summary.get("has_product_card"):
        technical = summary.get("technical") or {}
        return [
            f"- 任务 `{task_id}` 无 V4 产品卡片，仅技术状态："
            f"{technical.get('status')}/{technical.get('phase')}。"
        ]
    observation = summary.get("observation") or {}
    real_tmp = summary.get("real_vs_temporary") or {}
    blocking = summary.get("blocking_decisions") or []
    architecture = summary.get("architecture") or {}
    continuation = summary.get("continuation")
    direction = summary.get("product_direction")
    direction_text = str(direction)
    if isinstance(continuation, dict) and continuation.get("equivalent"):
        direction_text = (
            f"{direction}（方向沿用自 {continuation.get('source_decision_id')}，"
            "当前 snapshot 由等价检查覆盖，人类未重新观察该 snapshot）"
        )
    focus = summary.get("focus_slice_id") or task_id
    kind = summary.get("kind") or "core_slice"
    if summary.get("supporting_cannot_claim_core_complete"):
        focus_line = f"{focus} / supporting（不等于核心完成）"
    else:
        focus_line = f"{focus} / {kind}"
    entry = observation.get("entrypoint_ref") or "none"
    steps = observation.get("steps") or []
    step_text = " → ".join(str(item) for item in steps) if steps else "none"
    real = [str(item) for item in (real_tmp.get("real") or [])]
    temporary = [str(item) for item in (real_tmp.get("temporary") or [])]
    material = [str(item) for item in (summary.get("material_changes") or [])]
    assumptions = [str(item) for item in (summary.get("reversible_assumptions") or [])]
    execution_mode = real_tmp.get("execution_mode")
    if execution_mode == "exploratory":
        mode_text = "；执行模式：exploratory（非正式产品进度）"
    elif execution_mode == "formal":
        mode_text = "；执行模式：formal"
    else:
        mode_text = ""
    receipt_note = ""
    if observation.get("missing_current_receipt"):
        receipt_note = "；当前 snapshot 尚无观察收据"
    if blocking:
        cards = []
        for item in blocking:
            option_text = ", ".join(
                f"{opt.get('id')}:{opt.get('label')}"
                for opt in item.get("options") or []
                if isinstance(opt, dict)
            )
            extra = f"（{option_text}）" if option_text else ""
            cards.append(f"{item.get('id')}{extra}")
        blocking_text = "；".join(cards)
    else:
        blocking_text = "none"
    guardrail_ids = [
        str(item) for item in (architecture.get("guardrail_ids") or []) if item
    ]
    impact = architecture.get("declared_impact") or "unknown"
    live = architecture.get("live_status")
    if live not in {"verified", "unverified", "stale", "not_checked"}:
        live = "not_checked"
    guardrail_text = f"{impact}；live={live}"
    if guardrail_ids:
        guardrail_text += f"；guardrails {', '.join(guardrail_ids)}"
    next_action = (summary.get("next_action") or {}).get("text")
    lines = [
        f"- 当前核心结果：{summary.get('core_result') or _V4_REDACTED_DISPLAY}",
        f"- 当前焦点：{focus_line}",
        (
            f"- 可观察入口：{observation.get('surface_label')} `{entry}`"
            f"（health: {observation.get('health')}{receipt_note}）"
        ),
        f"- 观察步骤：{step_text}",
        (
            f"- 真实与临时：真实：{'; '.join(real) or 'none'}；"
            f"临时：{'; '.join(temporary) or 'none'}{mode_text}"
        ),
        f"- 本次实质变化：{'; '.join(material) or 'none'}",
        f"- AI 护栏内假设：{'; '.join(assumptions) or 'none'}",
        f"- 产品方向：{direction_text}",
        f"- 待决定：{blocking_text}",
        f"- 架构基线与护栏：{guardrail_text}",
    ]
    if isinstance(next_action, str) and next_action.strip():
        lines.append(f"- 下一动作：{next_action.strip()}")
    return lines


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
            "product": derive_v4_product_summary(record),
        }
        product = entry["product"]
        if isinstance(product, dict) and product.get("has_product_card"):
            architecture = dict(product.get("architecture") or {})
            architecture["live_status"] = _v4_architecture_live_status(paths, record)
            entry["product"] = {**product, "architecture": architecture}
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
    task_records = snapshot["task_records"]
    product_cards: list[str] = []
    core_cards: list[str] = []
    other_cards: list[str] = []
    for entry in task_records:
        summary = entry.get("product") if isinstance(entry.get("product"), dict) else {}
        card = _product_card_lines(str(entry.get("task_id")), summary)
        if summary.get("has_product_card") and not summary.get("supporting_cannot_claim_core_complete"):
            core_cards.extend(card)
            core_cards.append("")
        else:
            other_cards.extend(card)
            other_cards.append("")
    if core_cards or any(
        isinstance(entry.get("product"), dict) and entry["product"].get("has_product_card")
        for entry in task_records
    ):
        product_cards = ["当前存在 V4 产品卡片。", ""]
        product_cards.extend(core_cards)
        product_cards.extend(other_cards)
    elif other_cards:
        product_cards = ["当前没有 V4 产品卡片。以下为技术状态。", ""]
        product_cards.extend(other_cards)
    else:
        product_cards = ["当前没有 V4 产品卡片。以下为技术状态。"]
    while product_cards and product_cards[-1] == "":
        product_cards.pop()
    lines = [
        "# Workflow 状态快照",
        "",
        "> 此文件由工作流脚本生成；机器可读状态以 JSON 区块为准，不手工编辑。",
        "",
        f"> 更新时间：{snapshot['generated_at']}",
        "",
        "## 产品状态",
        "",
        *product_cards,
        "",
        "## 技术交付",
        "",
        "<details>",
        "<summary>Requirements、Backlog、task status/phase/verification/integration</summary>",
        "",
        f"- 状态指纹：`{snapshot['status_fingerprint']}`",
        f"- Requirements：`{baseline.get('brief_id')}` revision `{baseline.get('revision')}`。",
        f"- Requirements contract：{contract.get('status')}。",
        "- Backlog：" + "，".join(f"{key}={counts[key]}" for key in ("draft", "blocked", "ready", "done", "removed")) + "。",
        f"- Task records：{len(task_records)}；Requirements impact reports：{len(impacts)}。",
        *[
            (
                f"- `{entry.get('task_id')}`：status={entry.get('status')}；"
                f"phase={entry.get('phase')}；verification={entry.get('verification')}；"
                f"integration={entry.get('integration')}"
            )
            for entry in task_records
        ],
        "",
        "</details>",
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
