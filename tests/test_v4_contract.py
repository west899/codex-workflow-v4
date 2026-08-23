from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from support import approved_requirements, PACKAGE_ROOT, install_project, run, workflow_command


BIN = PACKAGE_ROOT / "payload/.codex-workflow/bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

from workflow_common import (  # noqa: E402
    V4_DECISION_FIELD_INVALIDATION_V1,
    V4_FINGERPRINT_INVALIDATION_MATRIX_V1,
    WorkflowDataError,
    canonical_v4_json_bytes,
    contract_fingerprint,
    decision_fingerprint,
    decision_fingerprint_material,
    decision_state_fingerprint,
    observation_fingerprint,
    validate_v4_observation_receipt,
    validate_json_schema,
)


SCHEMA = PACKAGE_ROOT / "payload/.codex-workflow/schemas/task-record-v4.schema.json"
REFERENCES = PACKAGE_ROOT / "payload/.agents/skills/orchestrate-project-task/references"
CORE_TEMPLATE = REFERENCES / "task-record-v4-core-template.json"
SUPPORTING_TEMPLATE = REFERENCES / "task-record-v4-supporting-template.json"
DECISION_EXAMPLES = REFERENCES / "decision-log-v4-examples.json"

CORE_CONTRACT_FINGERPRINT = "49209c9a7f9a1a99bbd1c8e8593b8ec6265aa8bfc6794d4d33228843468a11b3"
SUPPORTING_CONTRACT_FINGERPRINT = "b9d329ab383e521f5823323543dbe707a4be6ba9f1051fa3adb0e66e04c48170"
OBSERVATION_FINGERPRINT = "bfa1f699df4fe7c959e8ce7093c27aad4714233035ee79671a7010e821c0e315"
DECISION_FINGERPRINTS = {
    "product_checkpoint": "a6567c91e87005ea62afe6e1eb0fda3b52138ac8cbad18052592d6c235c7524c",
    "product_decision": "c72fa6e3725853827e9b2e7785128ac3c01d1ec5898d442526c78dd16dd73b86",
    "architecture_decision": "4f5508a7279a08d1a29c67d06d6f8ea7d37f50e71e4555f0c86b703e24f4319f",
    "risk_acceptance": "72563482d5521161038f35b05772d16d570a482460e26db8c9a7cd39cd05e1af",
}
DECISION_STATE_FINGERPRINTS = {
    "product_checkpoint": "2701d96084c9da1c6286ee09117d0cd2c85edc1aeaaf45938fdd637c56a15d41",
    "product_decision": "02bb1b75895dbaf726672bba087cf2bd56547132857d68e5c110a6df4b5fc3ab",
    "architecture_decision": "976ecf78e0ebcb3dca6a76bc458cc203f4e8865ff7ac70b6b2afbd1630d9bb59",
    "risk_acceptance": "39031ab60d2c1913292c1876885a33fe7b21c3578642416f8ceaca0a5772a6c0",
}


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def reverse_object_fields(value):
    if isinstance(value, dict):
        return {
            key: reverse_object_fields(item)
            for key, item in reversed(list(value.items()))
        }
    if isinstance(value, list):
        return [reverse_object_fields(item) for item in value]
    return value


class V4TaskContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.core = load_json(CORE_TEMPLATE)
        self.supporting = load_json(SUPPORTING_TEMPLATE)
        self.decisions = load_json(DECISION_EXAMPLES)

    def validate(self, record: dict, *, label: str = "V4 record") -> None:
        validate_json_schema(SCHEMA, record, label=label)

    def test_templates_are_strictly_valid_and_fingerprints_are_current(self) -> None:
        expectations = (
            (self.core, CORE_CONTRACT_FINGERPRINT),
            (self.supporting, SUPPORTING_CONTRACT_FINGERPRINT),
        )
        for record, expected in expectations:
            with self.subTest(task_id=record["task_id"]):
                self.validate(record)
                self.assertEqual(contract_fingerprint(record), expected)
                self.assertEqual(record["contract_fingerprint"], expected)

        self.assertEqual(self.core["delivery_contract"]["focus_slice_id"], self.core["task_id"])
        self.assertIsNone(self.core["delivery_contract"]["supports_task_id"])
        self.assertEqual(
            self.supporting["delivery_contract"]["supports_task_id"],
            self.supporting["delivery_contract"]["focus_slice_id"],
        )
        self.assertTrue(self.supporting["delivery_contract"]["supporting"]["minimal_boundary"])
        self.assertEqual(
            self.core["delivery_contract"]["dependency_refs"][0]["task_id"],
            self.supporting["task_id"],
        )
        self.assertEqual(self.supporting["delivery_contract"]["dependency_refs"], [])

    def test_v3_schema_bytes_remain_frozen(self) -> None:
        v3_schema = PACKAGE_ROOT / "payload/.codex-workflow/schemas/task-record-v3.schema.json"
        self.assertEqual(
            hashlib.sha256(v3_schema.read_bytes()).hexdigest(),
            "3167949c1b2f299391685bdd990b04d0331553ecc13908217dad80fca1651db2",
        )

    def test_schema_rejects_missing_core_contract_fields_and_unknown_fields(self) -> None:
        def remove(record: dict, *path: str) -> None:
            parent = record
            for key in path[:-1]:
                parent = parent[key]
            del parent[path[-1]]

        cases = {
            "contract fingerprint": lambda record: remove(record, "contract_fingerprint"),
            "requirements baseline": lambda record: remove(record, "source", "requirements_baseline"),
            "focus": lambda record: remove(record, "delivery_contract", "focus_slice_id"),
            "requirements refs": lambda record: remove(record, "delivery_contract", "requirement_ids"),
            "acceptance refs": lambda record: remove(record, "delivery_contract", "acceptance_ids"),
            "checkpoint": lambda record: remove(record, "delivery_contract", "checkpoint"),
            "observation recipe": lambda record: remove(record, "delivery_contract", "observation"),
            "architecture baseline": lambda record: remove(
                record, "delivery_contract", "architecture", "baseline"
            ),
            "root unknown": lambda record: record.update({"untracked_state": "unsafe"}),
            "contract unknown": lambda record: record["delivery_contract"].update(
                {"blocks": ["gate"]}
            ),
            "recipe unknown": lambda record: record["delivery_contract"]["observation"].update(
                {"preview_url": "https://mutable.invalid"}
            ),
            "invalid requirement ref": lambda record: record["delivery_contract"].update(
                {"requirement_ids": ["not-a-requirement"]}
            ),
            "empty guardrail verification": lambda record: record["delivery_contract"][
                "architecture"
            ]["guardrails"][0].update({"verification_refs": []}),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                record = copy.deepcopy(self.core)
                mutate(record)
                with self.assertRaises(WorkflowDataError):
                    self.validate(record, label=name)

        missing_support = copy.deepcopy(self.supporting)
        del missing_support["delivery_contract"]["supports_task_id"]
        with self.assertRaises(WorkflowDataError):
            self.validate(missing_support, label="supporting relation")

    def test_decision_log_is_a_strict_four_kind_discriminated_union(self) -> None:
        self.assertEqual(
            set(self.decisions),
            {"product_checkpoint", "product_decision", "architecture_decision", "risk_acceptance"},
        )
        for name, decision in self.decisions.items():
            with self.subTest(kind=name):
                record = copy.deepcopy(self.core)
                record["decision_log"] = [decision]
                self.validate(record, label=name)
                self.assertEqual(decision_fingerprint(decision), DECISION_FINGERPRINTS[name])
                self.assertEqual(decision["decision_fingerprint"], DECISION_FINGERPRINTS[name])
                self.assertEqual(
                    decision_state_fingerprint(decision),
                    DECISION_STATE_FINGERPRINTS[name],
                )
                self.assertEqual(
                    decision["decision_state_fingerprint"],
                    DECISION_STATE_FINGERPRINTS[name],
                )

        checkpoint = self.decisions["product_checkpoint"]
        self.assertEqual(
            observation_fingerprint(self.core, checkpoint["observation_receipt"]),
            OBSERVATION_FINGERPRINT,
        )
        self.assertEqual(checkpoint["observation_fingerprint"], OBSERVATION_FINGERPRINT)

        shipped = copy.deepcopy(self.core)
        shipped["verification"] = {
            "status": "pending",
            "delivery_commit": "b" * 40,
            "delivery_hash": "c" * 64,
            "patch_hash": "d" * 64,
            "snapshot_id": "a" * 64,
            "changed_paths": [],
        }
        self.assertEqual(
            validate_v4_observation_receipt(
                shipped,
                checkpoint["observation_receipt"],
            ),
            OBSERVATION_FINGERPRINT,
        )
        historical_receipt = copy.deepcopy(checkpoint["observation_receipt"])
        historical_receipt["entrypoint"]["checked_at"] = "1999-12-31T23:00:00Z"
        historical_receipt["healthcheck"]["checked_at"] = "1999-12-31T23:00:00Z"
        historical_receipt["observed_at"] = "1999-12-31T23:30:00Z"
        historical_receipt["environment"]["expires_at"] = "2000-01-01T00:00:00Z"
        with self.assertRaisesRegex(WorkflowDataError, "environment has expired"):
            validate_v4_observation_receipt(shipped, historical_receipt)
        self.assertEqual(
            validate_v4_observation_receipt(
                shipped,
                historical_receipt,
                require_unexpired=False,
            ),
            OBSERVATION_FINGERPRINT,
        )
        sensitive = copy.deepcopy(shipped)
        sensitive["risk"]["sensitive_data"] = True
        sensitive["contract_fingerprint"] = contract_fingerprint(sensitive)
        sensitive_receipt = copy.deepcopy(checkpoint["observation_receipt"])
        sensitive_receipt["contract_fingerprint"] = sensitive["contract_fingerprint"]
        with self.assertRaisesRegex(WorkflowDataError, "requires applied redaction"):
            validate_v4_observation_receipt(sensitive, sensitive_receipt)

        crossed = copy.deepcopy(self.decisions["product_decision"])
        crossed["architecture_context"] = copy.deepcopy(
            self.decisions["architecture_decision"]["architecture_context"]
        )
        del crossed["product_context"]
        record = copy.deepcopy(self.core)
        record["decision_log"] = [crossed]
        with self.assertRaises(WorkflowDataError):
            self.validate(record, label="cross-kind decision")

        untracked_blocks = copy.deepcopy(self.decisions["product_decision"])
        untracked_blocks["blocks"] = ["record-review"]
        record["decision_log"] = [untracked_blocks]
        with self.assertRaises(WorkflowDataError):
            self.validate(record, label="untracked blocking override")

        wrong_resolution = copy.deepcopy(checkpoint)
        wrong_resolution["resolution"] = copy.deepcopy(
            self.decisions["architecture_decision"]["resolution"]
        )
        record["decision_log"] = [wrong_resolution]
        with self.assertRaises(WorkflowDataError):
            self.validate(record, label="wrong checkpoint resolution")

        selected_resolution = copy.deepcopy(
            self.decisions["architecture_decision"]["resolution"]
        )
        checkpoint_resolution = {
            "outcome": "accepted",
            "decided_by": "human-owner",
            "decided_at": "2026-07-20T01:00:00Z",
            "source": "external-receipt:checkpoint",
            "rationale": "The observed direction is accepted.",
        }
        for name, decision in self.decisions.items():
            with self.subTest(kind=name, mismatch="resolved without resolution"):
                mismatched = copy.deepcopy(decision)
                mismatched["status"] = "resolved"
                mismatched["resolution"] = None
                record["decision_log"] = [mismatched]
                with self.assertRaises(WorkflowDataError):
                    self.validate(record, label=f"{name} resolved without resolution")
            with self.subTest(kind=name, mismatch="open with resolution"):
                mismatched = copy.deepcopy(decision)
                mismatched["status"] = "open"
                mismatched["resolution"] = (
                    checkpoint_resolution if name == "product_checkpoint" else selected_resolution
                )
                record["decision_log"] = [mismatched]
                with self.assertRaises(WorkflowDataError):
                    self.validate(record, label=f"{name} open with resolution")

    def test_object_field_order_and_unicode_form_do_not_change_fingerprints(self) -> None:
        checkpoint = self.decisions["product_checkpoint"]
        reversed_record = reverse_object_fields(self.core)
        reversed_checkpoint = reverse_object_fields(checkpoint)
        self.assertEqual(contract_fingerprint(reversed_record), contract_fingerprint(self.core))
        self.assertEqual(decision_fingerprint(reversed_checkpoint), decision_fingerprint(checkpoint))
        self.assertEqual(
            observation_fingerprint(reversed_record, reversed_checkpoint["observation_receipt"]),
            observation_fingerprint(self.core, checkpoint["observation_receipt"]),
        )
        self.assertEqual(
            canonical_v4_json_bytes({"label": "caf\u00e9"}),
            canonical_v4_json_bytes({"label": "cafe\u0301"}),
        )
        nfd_option = copy.deepcopy(self.decisions["product_decision"])
        nfd_option["options"][0]["id"] = "cafe\u0301"
        nfd_option["recommendation"]["option_id"] = "cafe\u0301"
        nfc_option = copy.deepcopy(nfd_option)
        nfc_option["options"][0]["id"] = "caf\u00e9"
        nfc_option["recommendation"]["option_id"] = "caf\u00e9"
        self.assertEqual(
            decision_fingerprint(nfd_option),
            decision_fingerprint(nfc_option),
        )

        reordered_record = copy.deepcopy(self.core)
        reordered_record["scope"]["allowed_paths"].reverse()
        reordered_record["scope"]["resource_keys"].reverse()
        reordered_record["delivery_contract"]["requirement_ids"].reverse()
        reordered_record["delivery_contract"]["acceptance_ids"].reverse()
        self.assertEqual(contract_fingerprint(reordered_record), contract_fingerprint(self.core))

        reordered_checkpoint = copy.deepcopy(checkpoint)
        reordered_checkpoint["affected_scope"].reverse()
        receipt = reordered_checkpoint["observation_receipt"]
        receipt["requirement_ids"].reverse()
        receipt["acceptance_ids"].reverse()
        receipt["normalized_result"]["stable_output"].reverse()
        self.assertEqual(
            observation_fingerprint(self.core, receipt),
            observation_fingerprint(self.core, checkpoint["observation_receipt"]),
        )
        self.assertEqual(
            decision_fingerprint(reordered_checkpoint), decision_fingerprint(checkpoint)
        )

        reordered_options = copy.deepcopy(self.decisions["product_decision"])
        reordered_options["options"].reverse()
        self.assertNotEqual(
            decision_fingerprint(reordered_options),
            decision_fingerprint(self.decisions["product_decision"]),
        )

        reordered_steps = copy.deepcopy(self.core)
        reordered_steps["planning"]["steps"].reverse()
        self.assertNotEqual(contract_fingerprint(reordered_steps), contract_fingerprint(self.core))
        reordered_recipe = copy.deepcopy(self.core)
        reordered_recipe["delivery_contract"]["observation"]["steps"].reverse()
        self.assertNotEqual(contract_fingerprint(reordered_recipe), contract_fingerprint(self.core))

    def test_canonicalizer_rejects_ambiguous_or_unbounded_values(self) -> None:
        canonical_v4_json_bytes({"value": (10**640) - 1})
        with self.assertRaisesRegex(WorkflowDataError, "640-digit"):
            canonical_v4_json_bytes({"value": 10**640})
        with self.assertRaisesRegex(WorkflowDataError, "floating-point"):
            canonical_v4_json_bytes({"value": 0.1})
        with self.assertRaisesRegex(WorkflowDataError, "collide"):
            canonical_v4_json_bytes({"caf\u00e9": 1, "cafe\u0301": 2})
        cyclic: list = []
        cyclic.append(cyclic)
        with self.assertRaisesRegex(WorkflowDataError, "cyclic"):
            canonical_v4_json_bytes(cyclic)
        nested = 0
        for _ in range(256):
            nested = {"value": nested}
        canonical_v4_json_bytes(nested)
        with self.assertRaisesRegex(WorkflowDataError, "256-level"):
            canonical_v4_json_bytes({"value": nested})
        duplicate_refs = copy.deepcopy(self.core)
        duplicate_refs["delivery_contract"]["requirement_ids"].append(
            duplicate_refs["delivery_contract"]["requirement_ids"][0]
        )
        with self.assertRaisesRegex(WorkflowDataError, "duplicate set identity"):
            contract_fingerprint(duplicate_refs)

        duplicate_guardrail = copy.deepcopy(self.core)
        second_guardrail = copy.deepcopy(
            duplicate_guardrail["delivery_contract"]["architecture"]["guardrails"][0]
        )
        second_guardrail["statement"] = "A conflicting statement under the same guardrail ID."
        duplicate_guardrail["delivery_contract"]["architecture"]["guardrails"].append(
            second_guardrail
        )
        with self.assertRaisesRegex(WorkflowDataError, "duplicate set identity 'id'"):
            contract_fingerprint(duplicate_guardrail)

        duplicate_decision_ref = copy.deepcopy(self.core)
        duplicate_decision_ref["delivery_contract"]["decision_refs"] = [
            {"decision_id": "HD-010", "selected_option_id": "A"},
            {"decision_id": "HD-010", "selected_option_id": "B"},
        ]
        with self.assertRaisesRegex(WorkflowDataError, "duplicate set identity 'decision_id'"):
            contract_fingerprint(duplicate_decision_ref)

        duplicate_dependency = copy.deepcopy(self.core)
        second_dependency = copy.deepcopy(
            duplicate_dependency["delivery_contract"]["dependency_refs"][0]
        )
        second_dependency["closeout_fingerprint"] = "7" * 64
        duplicate_dependency["delivery_contract"]["dependency_refs"].append(
            second_dependency
        )
        with self.assertRaisesRegex(WorkflowDataError, "duplicate set identity 'task_id'"):
            contract_fingerprint(duplicate_dependency)

        duplicate_options = copy.deepcopy(self.decisions["product_decision"])
        second_option = copy.deepcopy(duplicate_options["options"][0])
        second_option["impact"] = "A conflicting impact under the same option ID."
        duplicate_options["options"].append(second_option)
        with self.assertRaisesRegex(WorkflowDataError, "unique id values"):
            decision_fingerprint(duplicate_options)

        duplicate_output_receipt = copy.deepcopy(
            self.decisions["product_checkpoint"]["observation_receipt"]
        )
        duplicate_output_receipt["normalized_result"]["stable_output"].append(
            {"name": "result", "value": "CONFLICTING_RESULT"}
        )
        with self.assertRaisesRegex(WorkflowDataError, "duplicate set identity 'name'"):
            observation_fingerprint(self.core, duplicate_output_receipt)

    def test_contract_invalidation_matrix_covers_every_material_contract_change(self) -> None:
        baseline = contract_fingerprint(self.core)

        def dependency_change(record: dict) -> None:
            record["delivery_contract"]["dependency_refs"].append(
                {
                    "task_id": "MVP-DEP-001",
                    "closeout_ref": "task:MVP-DEP-001",
                    "closeout_fingerprint": "9999999999999999999999999999999999999999999999999999999999999999",
                }
            )

        changes = {
            "request": lambda record: record.update({"request": record["request"] + " Updated."}),
            "requirements": lambda record: record["source"]["requirements_baseline"].update(
                {"revision": 2}
            ),
            "scope": lambda record: record["scope"]["in"].append("A new product behavior."),
            "planning": lambda record: record["planning"]["steps"].append("Run a broader check."),
            "risk": lambda record: record["risk"].update({"sensitive_data": True}),
            "acceptance": lambda record: record["acceptance"][0].update(
                {"criterion": record["acceptance"][0]["criterion"] + " With a boundary case."}
            ),
            "recipe": lambda record: record["delivery_contract"]["observation"]["steps"].append(
                "Check the failure result."
            ),
            "fixture": lambda record: record["delivery_contract"]["observation"].update(
                {"fixture_ref": "fixture:core-slice-v2"}
            ),
            "dependency": dependency_change,
            "guardrail": lambda record: record["delivery_contract"]["architecture"]["guardrails"][0].update(
                {"statement": "The slice must preserve both dependency and data ownership directions."}
            ),
        }
        for name, mutate in changes.items():
            with self.subTest(change=name):
                record = copy.deepcopy(self.core)
                mutate(record)
                self.assertNotEqual(contract_fingerprint(record), baseline)

        lifecycle_only = copy.deepcopy(self.core)
        lifecycle_only["generation"] = 7
        lifecycle_only["phase"] = "review"
        lifecycle_only["status"] = "in_progress"
        lifecycle_only["acceptance"][0]["status"] = "passed"
        lifecycle_only["acceptance"][0]["evidence"] = ["evidence:AC-001"]
        lifecycle_only["verification"]["snapshot_id"] = "8" * 64
        self.assertEqual(contract_fingerprint(lifecycle_only), baseline)

    def _observation_after(self, record_mutation=None, receipt_mutation=None) -> str:
        record = copy.deepcopy(self.core)
        receipt = copy.deepcopy(self.decisions["product_checkpoint"]["observation_receipt"])
        if record_mutation:
            record_mutation(record)
        record["contract_fingerprint"] = contract_fingerprint(record)
        receipt["contract_fingerprint"] = record["contract_fingerprint"]
        if receipt_mutation:
            receipt_mutation(receipt)
        return observation_fingerprint(record, receipt)

    def test_observation_invalidation_matrix_separates_product_semantics_from_receipt_identity(self) -> None:
        baseline = self._observation_after()
        product_changes = {
            "request": (
                lambda record: record.update({"request": record["request"] + " Updated."}),
                None,
            ),
            "requirements": (
                lambda record: record["source"]["requirements_baseline"].update({"revision": 2}),
                None,
            ),
            "scope": (lambda record: record["scope"]["out"].append("Another non-goal."), None),
            "acceptance": (
                lambda record: record["acceptance"][0].update({"criterion": "A changed product result."}),
                None,
            ),
            "recipe": (
                lambda record: record["delivery_contract"]["observation"]["steps"].append(
                    "Observe an additional behavior."
                ),
                None,
            ),
            "fixture": (
                None,
                lambda receipt: receipt["fixture"].update({"digest": "7" * 64}),
            ),
            "public API": (
                None,
                lambda receipt: receipt["normalized_result"]["contracts"]["public_api"][0].update(
                    {"digest": "6" * 64}
                ),
            ),
            "environment configuration": (
                None,
                lambda receipt: receipt["environment"].update(
                    {"configuration_fingerprint": "4" * 64}
                ),
            ),
            "visible result": (
                None,
                lambda receipt: receipt["normalized_result"]["stable_output"][1].update(
                    {"value": "CORE_CHANGED"}
                ),
            ),
            "architecture": (
                lambda record: record["delivery_contract"]["architecture"]["baseline"].update(
                    {"revision": 2}
                ),
                None,
            ),
        }
        for name, (record_mutation, receipt_mutation) in product_changes.items():
            with self.subTest(change=name):
                self.assertNotEqual(
                    self._observation_after(record_mutation, receipt_mutation),
                    baseline,
                )

        non_product_changes = {
            "planning": (
                lambda record: record["planning"]["steps"].append("Run one more technical check."),
                None,
            ),
            "risk metadata": (
                lambda record: record["risk"].update({"production_release": True}),
                None,
            ),
            "artifact identity": (
                None,
                lambda receipt: receipt["artifact"].update({"digest": "5" * 64}),
            ),
            "observation time": (
                None,
                lambda receipt: receipt.update({"observed_at": "2026-07-21T00:00:00Z"}),
            ),
            "temporary environment reference": (
                None,
                lambda receipt: receipt["environment"].update(
                    {"reference": "http://127.0.0.1:49152/preview"}
                ),
            ),
            "evidence reference": (
                None,
                lambda receipt: receipt["evidence_refs"].append("evidence:second-transcript"),
            ),
            "method evidence references": (
                None,
                lambda receipt: receipt["normalized_result"]["method_evidence"].update(
                    {
                        "stdout_ref": "evidence:second-run-stdout",
                        "stderr_ref": "evidence:second-run-stderr",
                    }
                ),
            ),
        }
        for name, (record_mutation, receipt_mutation) in non_product_changes.items():
            with self.subTest(change=name):
                self.assertEqual(
                    self._observation_after(record_mutation, receipt_mutation),
                    baseline,
                )

        stale = copy.deepcopy(self.core)
        stale["scope"]["in"].append("Changed without rebinding the stored fingerprint.")
        receipt = self.decisions["product_checkpoint"]["observation_receipt"]
        with self.assertRaisesRegex(WorkflowDataError, "stale"):
            observation_fingerprint(stale, receipt)

    def test_decision_fingerprint_changes_with_request_and_binding_but_not_resolution(self) -> None:
        product = copy.deepcopy(self.decisions["product_decision"])
        baseline = decision_fingerprint(product)
        for name, mutate in {
            "question": lambda item: item.update({"question": item["question"] + " Updated."}),
            "option": lambda item: item["options"][0].update({"impact": "Changed option impact."}),
            "scope": lambda item: item["affected_scope"].append("public_api"),
            "requirements": lambda item: item["requirements_baseline"].update({"revision": 2}),
            "context": lambda item: item["product_context"].update({"data_contract": "changes"}),
        }.items():
            with self.subTest(change=name):
                changed = copy.deepcopy(product)
                mutate(changed)
                self.assertNotEqual(decision_fingerprint(changed), baseline)

        resolved = copy.deepcopy(product)
        resolved["status"] = "resolved"
        resolved["resolution"] = {
            "selected_option_id": "A",
            "decided_by": "human-owner",
            "decided_at": "2026-07-20T01:00:00Z",
            "source": "external-receipt:product-choice",
            "rationale": "Select the recommended option.",
        }
        resolved["deferrals"].append(
            {
                "deferred_by": "human-owner",
                "deferred_at": "2026-07-20T00:30:00Z",
                "source": "external-receipt:temporary-deferral",
                "reason": "Observe one more case.",
                "latest_observation_point": "Before implementation.",
            }
        )
        self.assertEqual(decision_fingerprint(resolved), baseline)

        checkpoint = copy.deepcopy(self.decisions["product_checkpoint"])
        checkpoint_baseline = decision_fingerprint(checkpoint)
        checkpoint["binding"]["snapshot_id"] = "4" * 64
        checkpoint["observation_receipt"]["snapshot_id"] = "4" * 64
        self.assertNotEqual(decision_fingerprint(checkpoint), checkpoint_baseline)

        kind_changes = {
            "product_checkpoint": lambda item: item["binding"].update(
                {"snapshot_id": "3" * 64}
            ),
            "product_decision": lambda item: item["product_context"].update(
                {"dependencies": "changes"}
            ),
            "architecture_decision": lambda item: item["architecture_context"][
                "baseline"
            ].update({"revision": 2}),
            "risk_acceptance": lambda item: item["risk_context"].update(
                {"consequence": "A changed bounded consequence."}
            ),
        }
        for name, mutate in kind_changes.items():
            with self.subTest(kind=name, change="kind-specific material"):
                original = self.decisions[name]
                changed = copy.deepcopy(original)
                mutate(changed)
                self.assertNotEqual(decision_fingerprint(changed), decision_fingerprint(original))
            with self.subTest(kind=name, change="independence classification"):
                original = self.decisions[name]
                changed = copy.deepcopy(original)
                changed["current_delivery_independent"] = not changed[
                    "current_delivery_independent"
                ]
                self.assertNotEqual(decision_fingerprint(changed), decision_fingerprint(original))

        common_field_changes = {
            "blocking": lambda item: item.update({"blocking": not item["blocking"]}),
            "latest_decision_point": lambda item: item.update(
                {
                    "latest_decision_point": (
                        "before_review"
                        if item["latest_decision_point"] == "before_integration"
                        else "before_integration"
                    )
                }
            ),
            "question": lambda item: item.update({"question": item["question"] + " Updated."}),
        }
        for name, original in self.decisions.items():
            for field, mutate in common_field_changes.items():
                with self.subTest(kind=name, change=field):
                    changed = copy.deepcopy(original)
                    mutate(changed)
                    self.assertNotEqual(
                        decision_fingerprint(changed), decision_fingerprint(original)
                    )
            if name != "product_checkpoint":
                with self.subTest(kind=name, change="recommendation"):
                    changed = copy.deepcopy(original)
                    changed["recommendation"]["reason"] += " Updated."
                    self.assertNotEqual(
                        decision_fingerprint(changed), decision_fingerprint(original)
                    )

        lifecycle_resolutions = {
            "product_checkpoint": {
                "outcome": "accepted",
                "decided_by": "human-owner",
                "decided_at": "2026-07-20T02:00:00Z",
                "source": "external-receipt:checkpoint",
                "rationale": "Accept the observed direction.",
            },
            "product_decision": copy.deepcopy(
                self.decisions["architecture_decision"]["resolution"]
            ),
            "architecture_decision": None,
            "risk_acceptance": copy.deepcopy(
                self.decisions["architecture_decision"]["resolution"]
            ),
        }
        for name, resolution in lifecycle_resolutions.items():
            with self.subTest(kind=name, change="resolution lifecycle"):
                original = self.decisions[name]
                changed = copy.deepcopy(original)
                changed["status"] = "open" if original["status"] == "resolved" else "resolved"
                changed["resolution"] = resolution
                self.assertEqual(decision_fingerprint(changed), decision_fingerprint(original))

    def test_invalidation_matrix_is_frozen_for_later_gate_work(self) -> None:
        self.assertEqual(
            V4_FINGERPRINT_INVALIDATION_MATRIX_V1,
            {
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
                "acceptance": (
                    "contract",
                    "decision:product_checkpoint",
                    "observation",
                ),
                "observation_recipe": (
                    "contract",
                    "decision:product_checkpoint",
                    "observation",
                ),
                "fixture": (
                    "contract",
                    "decision:product_checkpoint",
                    "observation",
                ),
                "public_api_or_schema": (
                    "decision:product_checkpoint",
                    "observation",
                ),
                "dependencies": (
                    "contract",
                    "decision:product_checkpoint",
                    "observation",
                ),
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
                "decision_checkpoint_binding_or_observation": (
                    "decision:product_checkpoint",
                ),
                "decision_product_context": ("decision:product_decision",),
                "decision_risk_context": ("decision:risk_acceptance",),
                "resolution_or_deferral": (),
                "delivery_snapshot_or_evidence": ("decision:product_checkpoint",),
            },
        )
        self.assertEqual(set(V4_DECISION_FIELD_INVALIDATION_V1), set(self.decisions))
        for kind, decision in self.decisions.items():
            with self.subTest(kind=kind, check="field closure"):
                material_fields = set(decision_fingerprint_material(decision))
                field_map = V4_DECISION_FIELD_INVALIDATION_V1[kind]
                self.assertEqual(set(field_map), material_fields)
                for field, category in field_map.items():
                    self.assertIn(category, V4_FINGERPRINT_INVALIDATION_MATRIX_V1, field)
                    self.assertIn(
                        f"decision:{kind}",
                        V4_FINGERPRINT_INVALIDATION_MATRIX_V1[category],
                        field,
                    )

    def test_decisions_template_freezes_an_unconfigured_architecture_baseline_shape(self) -> None:
        path = PACKAGE_ROOT / "payload/.codex-workflow/governance/DECISIONS.md"
        text = path.read_text(encoding="utf-8")
        start = "<!-- CODEX_ARCHITECTURE_BASELINE_START -->"
        end = "<!-- CODEX_ARCHITECTURE_BASELINE_END -->"
        self.assertEqual(text.count(start), 1)
        self.assertEqual(text.count(end), 1)
        baseline = json.loads(text.split(start, 1)[1].split(end, 1)[0])
        self.assertEqual(
            set(baseline),
            {"schema_version", "baseline_id", "revision", "status", "guardrails", "approval"},
        )
        self.assertEqual(baseline["baseline_id"], "ARCH-BASELINE-001")
        self.assertEqual(baseline["status"], "not_configured")
        self.assertEqual(baseline["guardrails"], [])
        self.assertIsNone(baseline["approval"]["approved_fingerprint"])
        architecture = self.core["delivery_contract"]["architecture"]
        self.assertEqual(architecture["baseline"]["source"], ".codex-workflow/governance/DECISIONS.md")
        self.assertEqual(architecture["baseline"]["revision"], baseline["revision"])
        self.assertEqual(architecture["guardrails"][0]["source"], "decision:ARCH-BASELINE-001")

    def test_v4_records_dispatch_to_v4_preflight_at_m2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "project"
            installed = install_project(target)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            _, requirements_fingerprint = approved_requirements(target)
            installed_schema = target / ".codex-workflow/schemas/task-record-v4.schema.json"
            installed_template = target / (
                ".agents/skills/orchestrate-project-task/references/"
                "task-record-v4-core-template.json"
            )
            self.assertTrue(installed_schema.is_file())
            self.assertTrue(installed_template.is_file())
            validate_json_schema(
                installed_schema,
                json.loads(installed_template.read_text(encoding="utf-8")),
                label="Installed V4 core template",
            )
            record_path = target / ".codex-workflow/state/runs/MVP-001.json"
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record = copy.deepcopy(self.core)
            record["source"]["requirements_baseline"] = {
                "brief_id": "REQ-001",
                "revision": 1,
                "approval_fingerprint": requirements_fingerprint,
            }
            record["contract_fingerprint"] = contract_fingerprint(record)
            record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            result = run(
                workflow_command(
                    target,
                    "workflow_check.py",
                    "preflight",
                    ".codex-workflow/state/runs/MVP-001.json",
                ),
                cwd=target,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("task-record-v3.schema.json", result.stderr)
            self.assertIn("V4 architecture baseline", result.stderr)
            self.assertIn("explicit implementation authorization", result.stderr)


if __name__ == "__main__":
    unittest.main()
