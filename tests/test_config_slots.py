"""Contract tests for the deterministic RequiredSlotSetV1 algorithm."""

from __future__ import annotations

from copy import deepcopy

import pytest

from a2amesh.config_slots import (
    RequiredSlotError,
    StableSlot,
    required_slot_projection,
    required_slot_set,
)

BASE_SLOTS = [
    {"componentType": "AUDIT_SINK", "componentPrincipal": "base-audit", "nodeId": "node-1"},
    {"componentType": "STATE_SERVICE", "componentPrincipal": "base-state", "nodeId": "node-1"},
    {"componentType": "CONFIG_CONTROLLER", "componentPrincipal": "base-config", "nodeId": "node-1"},
    {"componentType": "OBJECT_STORE", "componentPrincipal": "base-object", "nodeId": "node-1"},
    {"componentType": "GATEWAY", "componentPrincipal": "base-gateway", "nodeId": "node-1"},
    {"componentType": "NATS_JETSTREAM", "componentPrincipal": "base-nats", "nodeId": "node-1"},
    {"componentType": "ARTIFACT_BROKER", "componentPrincipal": "base-artifact", "nodeId": "node-1"},
]


def descriptor() -> dict:
    return {"fixedBaseSlots": deepcopy(BASE_SLOTS)}


def bundle() -> dict:
    return {
        "components": [
            {
                "componentType": "application-core",
                "componentPrincipal": "core-principal",
                "nodeId": "node-2",
                "requiredForProfiles": ["CORE", "INTEROP"],
                "selector": "core-key",
            },
            {
                "componentType": "peer-binding",
                "componentPrincipal": "peer-principal",
                "nodeId": "node-2",
                "requiredForProfiles": ["CORE"],
            },
            {
                "componentType": "stream-session-controller",
                "componentPrincipal": "stream-principal",
                "nodeId": "node-2",
                "requiredForProfiles": [],
            },
            {
                "componentType": "orchestrator",
                "componentPrincipal": "orchestrator-principal",
                "nodeId": "node-2",
                "requiredForProfiles": ["EXTENDED"],
            },
        ],
        "deliveryProfile": {
            "explicitlyRequiredOperationalSlots": [
                {
                    "componentType": "stream-session-controller",
                    "componentPrincipal": "stream-principal",
                    "nodeId": "node-2",
                }
            ]
        },
    }


def test_required_slot_set_is_profile_exact_sorted_and_does_not_mutate_inputs() -> None:
    descriptor_value = descriptor()
    bundle_value = bundle()
    before_descriptor = deepcopy(descriptor_value)
    before_bundle = deepcopy(bundle_value)

    slots = required_slot_set("CORE", bundle_value, descriptor_value)

    assert slots == tuple(sorted(slots, key=StableSlot.sort_key))
    assert {slot.component_type for slot in slots} >= {
        "CONFIG_CONTROLLER",
        "STATE_SERVICE",
        "GATEWAY",
        "NATS_JETSTREAM",
        "OBJECT_STORE",
        "ARTIFACT_BROKER",
        "AUDIT_SINK",
        "application-core",
        "peer-binding",
        "stream-session-controller",
    }
    assert "orchestrator" not in {slot.component_type for slot in slots}
    assert descriptor_value == before_descriptor
    assert bundle_value == before_bundle


def test_profile_name_is_case_sensitive_and_does_not_match_substrings() -> None:
    value = bundle()
    assert "application-core" in {
        slot.component_type for slot in required_slot_set("CORE", value, descriptor())
    }
    assert "application-core" not in {
        slot.component_type for slot in required_slot_set("core", value, descriptor())
    }
    assert "orchestrator" in {
        slot.component_type for slot in required_slot_set("EXTENDED", value, descriptor())
    }


def test_explicit_slot_must_resolve_to_signed_component_or_base_slot() -> None:
    value = bundle()
    value["deliveryProfile"]["explicitlyRequiredOperationalSlots"].append(
        {
            "componentType": "unknown",
            "componentPrincipal": "not-signed",
            "nodeId": "node-9",
        }
    )
    with pytest.raises(RequiredSlotError, match="does not resolve"):
        required_slot_set("CORE", value, descriptor())


def test_existing_required_slots_projection_must_match_exactly() -> None:
    value = bundle()
    value["deliveryProfile"]["requiredSlots"] = required_slot_projection(
        "CORE", bundle(), descriptor()
    )
    assert required_slot_set("CORE", value, descriptor())

    value["deliveryProfile"]["requiredSlots"] = value["deliveryProfile"]["requiredSlots"][1:]
    with pytest.raises(RequiredSlotError, match="does not equal"):
        required_slot_set("CORE", value, descriptor())


def test_recovery_projection_must_have_same_slots_and_lowercase_sha256() -> None:
    value = bundle()
    generated = required_slot_projection("CORE", value, descriptor())
    value["recoveryPolicy"] = {
        "requiredComponents": [
            {
                **slot,
                "verificationMethod": "state-attestation",
                "expectedDigest": "a" * 64,
            }
            for slot in generated
        ]
    }
    assert required_slot_set("CORE", value, descriptor())

    value["recoveryPolicy"]["requiredComponents"][0]["expectedDigest"] = "A" * 64
    with pytest.raises(RequiredSlotError, match="lowercase SHA-256"):
        required_slot_set("CORE", value, descriptor())


def test_missing_base_type_is_rejected() -> None:
    value = descriptor()
    value["fixedBaseSlots"] = value["fixedBaseSlots"][:-1]
    with pytest.raises(RequiredSlotError, match="missing required types"):
        required_slot_set("CORE", bundle(), value)


def test_duplicate_base_or_component_slot_is_rejected() -> None:
    value = descriptor()
    value["fixedBaseSlots"].append(deepcopy(value["fixedBaseSlots"][0]))
    with pytest.raises(RequiredSlotError, match="duplicate"):
        required_slot_set("CORE", bundle(), value)

    value = bundle()
    value["components"].append(deepcopy(value["components"][0]))
    with pytest.raises(RequiredSlotError, match="duplicate"):
        required_slot_set("CORE", value, descriptor())


def test_merge_broker_and_dynamic_instance_identifiers_are_not_slots() -> None:
    value = bundle()
    value["components"].append(
        {
            "componentType": "merge-broker",
            "componentPrincipal": "merge-principal",
            "nodeId": "node-2",
            "instanceId": "runtime-uuid",
            "requiredForProfiles": ["CORE"],
        }
    )
    with pytest.raises(RequiredSlotError, match="Merge Broker"):
        required_slot_set("CORE", value, descriptor())


def test_explicit_duplicate_and_extra_fields_are_rejected() -> None:
    value = bundle()
    slot = value["deliveryProfile"]["explicitlyRequiredOperationalSlots"][0]
    value["deliveryProfile"]["explicitlyRequiredOperationalSlots"].append(deepcopy(slot))
    with pytest.raises(RequiredSlotError, match="duplicate"):
        required_slot_set("CORE", value, descriptor())

    value = bundle()
    value["deliveryProfile"]["explicitlyRequiredOperationalSlots"][0]["instanceId"] = "uuid"
    with pytest.raises(RequiredSlotError, match="missing or extra"):
        required_slot_set("CORE", value, descriptor())


def test_empty_or_control_character_stable_identity_is_rejected() -> None:
    value = bundle()
    value["components"][0]["nodeId"] = "node\n2"
    with pytest.raises(RequiredSlotError, match="control"):
        required_slot_set("CORE", value, descriptor())


def test_projection_is_json_friendly_and_has_no_dynamic_fields() -> None:
    projection = required_slot_projection("CORE", bundle(), descriptor())
    assert projection
    assert all(
        set(item) == {"componentType", "componentPrincipal", "nodeId"}
        for item in projection
    )
    assert all("instanceId" not in item for item in projection)
