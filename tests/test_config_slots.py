"""Contract tests for the deterministic RequiredSlotSetV1 algorithm."""

from __future__ import annotations

from copy import deepcopy

import pytest

import a2amesh
from a2amesh.config_slots import (
    RequiredSlotError,
    StableSlot,
    required_recovery_projection,
    required_slot_projection,
    required_slot_set,
)


def _base_slot(
    component_type: str,
    component_principal: str,
    probe_suffix: str,
) -> dict:
    return {
        "componentType": component_type,
        "componentPrincipal": component_principal,
        "nodeId": "node-1",
        "readyReporterPrincipal": f"ready-probe-{probe_suffix}",
        "probeNkeySelector": f"ready-probe-{probe_suffix}-key",
        "verificationMethod": "state-attestation",
        "expectedDigest": "a" * 64,
    }


BASE_SLOTS = [
    _base_slot("AUDIT_SINK", "base-audit", "audit"),
    _base_slot("STATE_SERVICE", "base-state", "state"),
    _base_slot("CONFIG_CONTROLLER", "base-config", "config"),
    _base_slot("OBJECT_STORE", "base-object", "object"),
    _base_slot("GATEWAY", "base-gateway", "gateway"),
    _base_slot("NATS_JETSTREAM", "base-nats", "nats"),
    _base_slot("ARTIFACT_BROKER", "base-artifact", "artifact"),
]


def descriptor() -> dict:
    return {"fixedBaseSlots": deepcopy(BASE_SLOTS)}


def _expected_projection(profile_name: str, value: dict) -> list[dict[str, str]]:
    slots = [
        {
            "componentType": item["componentType"],
            "componentPrincipal": item["componentPrincipal"],
            "nodeId": item["nodeId"],
        }
        for item in BASE_SLOTS
    ]
    slots.extend(
        {
            "componentType": item["componentType"],
            "componentPrincipal": item["componentPrincipal"],
            "nodeId": item["nodeId"],
        }
        for item in value["components"]
        if profile_name in item["requiredForProfiles"]
    )
    slots.extend(value["deliveryProfile"]["explicitlyRequiredOperationalSlots"])
    unique = {
        (item["componentType"], item["componentPrincipal"], item["nodeId"]): item
        for item in slots
    }
    return [
        unique[key]
        for key in sorted(
            unique,
            key=lambda values: tuple(value.encode("utf-8") for value in values),
        )
    ]


def _expected_recovery_projection(
    profile_name: str, value: dict
) -> list[dict[str, str]]:
    proof_by_slot = {
        (
            item["componentType"],
            item["componentPrincipal"],
            item["nodeId"],
        ): (item["verificationMethod"], item["expectedDigest"])
        for item in [*BASE_SLOTS, *value["components"]]
    }
    result: list[dict[str, str]] = []
    for slot in _expected_projection(profile_name, value):
        key = (
            slot["componentType"],
            slot["componentPrincipal"],
            slot["nodeId"],
        )
        verification_method, expected_digest = proof_by_slot[key]
        result.append(
            {
                **slot,
                "verificationMethod": verification_method,
                "expectedDigest": expected_digest,
            }
        )
    return result


def bundle(profile_name: str = "CORE") -> dict:
    value = {
        "components": [
            {
                "componentType": "application-core",
                "componentPrincipal": "core-principal",
                "nodeId": "node-2",
                "requiredForProfiles": ["CORE", "INTEROP"],
                "nkeySelector": "core-key",
            },
            {
                "componentType": "peer-binding",
                "componentPrincipal": "peer-principal",
                "nodeId": "node-2",
                "requiredForProfiles": ["CORE"],
                "nkeySelector": "peer-key",
            },
            {
                "componentType": "artifact-adapter",
                "componentPrincipal": "artifact-adapter-principal",
                "nodeId": "node-1",
                "requiredForProfiles": ["CORE"],
                "nkeySelector": "artifact-adapter-key",
            },
            {
                "componentType": "artifact-hold-reaper",
                "componentPrincipal": "artifact-hold-reaper-principal",
                "nodeId": "node-1",
                "requiredForProfiles": ["CORE"],
                "nkeySelector": "artifact-hold-reaper-key",
            },
            {
                "componentType": "artifact-delete-worker",
                "componentPrincipal": "artifact-delete-worker-principal",
                "nodeId": "node-1",
                "requiredForProfiles": ["CORE"],
                "nkeySelector": "artifact-delete-worker-key",
            },
            {
                "componentType": "stream-session-controller",
                "componentPrincipal": "stream-principal",
                "nodeId": "node-2",
                "requiredForProfiles": [],
                "nkeySelector": "stream-key",
            },
            {
                "componentType": "orchestrator",
                "componentPrincipal": "orchestrator-principal",
                "nodeId": "node-2",
                "requiredForProfiles": ["EXTENDED"],
                "nkeySelector": "orchestrator-key",
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
    for component in value["components"]:
        component["verificationMethod"] = "component-attestation"
        component["expectedDigest"] = "b" * 64
    projection = _expected_projection(profile_name, value)
    value["deliveryProfile"]["requiredSlots"] = deepcopy(projection)
    value["recoveryPolicy"] = {
        "requiredComponents": _expected_recovery_projection(profile_name, value)
    }
    return value


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
        "artifact-adapter",
        "artifact-delete-worker",
        "artifact-hold-reaper",
        "peer-binding",
        "stream-session-controller",
    }
    assert "orchestrator" not in {slot.component_type for slot in slots}
    assert descriptor_value == before_descriptor
    assert bundle_value == before_bundle


def test_profile_name_is_closed_and_case_sensitive() -> None:
    assert "application-core" in {
        slot.component_type
        for slot in required_slot_set("CORE", bundle("CORE"), descriptor())
    }
    assert "application-core" in {
        slot.component_type
        for slot in required_slot_set("INTEROP", bundle("INTEROP"), descriptor())
    }
    assert "orchestrator" in {
        slot.component_type
        for slot in required_slot_set("EXTENDED", bundle("EXTENDED"), descriptor())
    }
    for unknown in ("core", "BETA", "CORE "):
        with pytest.raises(RequiredSlotError, match="unsupported profile"):
            required_slot_set(unknown, bundle(), descriptor())


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


def test_recovery_projection_must_match_authoritative_proof_metadata() -> None:
    value = bundle()
    assert value["recoveryPolicy"]["requiredComponents"] == (
        required_recovery_projection("CORE", value, descriptor())
    )
    assert required_slot_set("CORE", value, descriptor())

    wrong_digest = bundle()
    wrong_digest["recoveryPolicy"]["requiredComponents"][0][
        "expectedDigest"
    ] = "c" * 64
    with pytest.raises(RequiredSlotError, match="proof metadata"):
        required_slot_set("CORE", wrong_digest, descriptor())

    wrong_method = bundle()
    wrong_method["recoveryPolicy"]["requiredComponents"][0][
        "verificationMethod"
    ] = "other-valid-method"
    with pytest.raises(RequiredSlotError, match="proof metadata"):
        required_slot_set("CORE", wrong_method, descriptor())

    invalid_digest = bundle()
    invalid_digest["recoveryPolicy"]["requiredComponents"][0][
        "expectedDigest"
    ] = "A" * 64
    with pytest.raises(RequiredSlotError, match="lowercase SHA-256"):
        required_slot_set("CORE", invalid_digest, descriptor())


def test_component_recovery_authority_is_required() -> None:
    missing_method = bundle()
    del missing_method["components"][0]["verificationMethod"]
    with pytest.raises(RequiredSlotError, match="verificationMethod"):
        required_slot_set("CORE", missing_method, descriptor())

    missing_digest = bundle()
    del missing_digest["components"][0]["expectedDigest"]
    with pytest.raises(RequiredSlotError, match="expectedDigest"):
        required_slot_set("CORE", missing_digest, descriptor())


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


def test_component_principals_and_nkey_selectors_are_globally_unique() -> None:
    shared_principal = bundle()
    shared_principal["components"][3]["componentPrincipal"] = shared_principal[
        "components"
    ][2]["componentPrincipal"]
    with pytest.raises(RequiredSlotError, match="componentPrincipal"):
        required_slot_set("CORE", shared_principal, descriptor())

    shared_nkey = bundle()
    shared_nkey["components"][3]["nkeySelector"] = shared_nkey["components"][2][
        "nkeySelector"
    ]
    with pytest.raises(RequiredSlotError, match="nkeySelector"):
        required_slot_set("CORE", shared_nkey, descriptor())

    missing_nkey = bundle()
    del missing_nkey["components"][3]["nkeySelector"]
    with pytest.raises(RequiredSlotError, match="nkeySelector"):
        required_slot_set("CORE", missing_nkey, descriptor())


def test_projection_is_json_friendly_and_has_no_dynamic_fields() -> None:
    projection = required_slot_projection("CORE", bundle(), descriptor())
    assert projection
    assert all(
        set(item) == {"componentType", "componentPrincipal", "nodeId"}
        for item in projection
    )
    assert all("instanceId" not in item for item in projection)


def test_validator_requires_both_signed_projections_but_generator_does_not() -> None:
    unsigned = bundle()
    expected = unsigned["deliveryProfile"].pop("requiredSlots")
    expected_recovery = unsigned["recoveryPolicy"]["requiredComponents"]
    del unsigned["recoveryPolicy"]
    assert required_slot_projection("CORE", unsigned, descriptor()) == expected
    assert (
        required_recovery_projection("CORE", unsigned, descriptor())
        == expected_recovery
    )
    with pytest.raises(RequiredSlotError, match="requiredSlots.*required"):
        required_slot_set("CORE", unsigned, descriptor())

    missing_recovery = bundle()
    del missing_recovery["recoveryPolicy"]
    with pytest.raises(RequiredSlotError, match="recoveryPolicy.*required"):
        required_slot_set("CORE", missing_recovery, descriptor())

    missing_components = bundle()
    del missing_components["recoveryPolicy"]["requiredComponents"]
    with pytest.raises(RequiredSlotError, match="requiredComponents.*required"):
        required_slot_set("CORE", missing_components, descriptor())


def test_fixed_base_slots_require_independent_ready_authorities() -> None:
    missing_reporter = descriptor()
    del missing_reporter["fixedBaseSlots"][0]["readyReporterPrincipal"]
    with pytest.raises(RequiredSlotError, match="readyReporterPrincipal"):
        required_slot_set("CORE", bundle(), missing_reporter)

    missing_probe = descriptor()
    del missing_probe["fixedBaseSlots"][0]["probeNkeySelector"]
    with pytest.raises(RequiredSlotError, match="probeNkeySelector"):
        required_slot_set("CORE", bundle(), missing_probe)

    bad_digest = descriptor()
    bad_digest["fixedBaseSlots"][0]["expectedDigest"] = "A" * 64
    with pytest.raises(RequiredSlotError, match="lowercase SHA-256"):
        required_slot_set("CORE", bundle(), bad_digest)


def test_principals_and_nkey_selectors_are_global_across_base_and_components() -> None:
    duplicate_base_reporter = descriptor()
    duplicate_base_reporter["fixedBaseSlots"][1]["readyReporterPrincipal"] = (
        duplicate_base_reporter["fixedBaseSlots"][0]["readyReporterPrincipal"]
    )
    with pytest.raises(RequiredSlotError, match="duplicate.*Principal"):
        required_slot_set("CORE", bundle(), duplicate_base_reporter)

    reporter_reuses_slot = descriptor()
    reporter_reuses_slot["fixedBaseSlots"][0]["readyReporterPrincipal"] = (
        reporter_reuses_slot["fixedBaseSlots"][1]["componentPrincipal"]
    )
    with pytest.raises(RequiredSlotError, match="duplicate.*Principal"):
        required_slot_set("CORE", bundle(), reporter_reuses_slot)

    component_reuses_base = bundle()
    component_reuses_base["components"][0]["componentPrincipal"] = BASE_SLOTS[0][
        "componentPrincipal"
    ]
    with pytest.raises(RequiredSlotError, match="duplicate.*Principal"):
        required_slot_set("CORE", component_reuses_base, descriptor())

    component_reuses_probe = bundle()
    component_reuses_probe["components"][0]["nkeySelector"] = BASE_SLOTS[0][
        "probeNkeySelector"
    ]
    with pytest.raises(RequiredSlotError, match="duplicate.*nkeySelector"):
        required_slot_set("CORE", component_reuses_probe, descriptor())


@pytest.mark.parametrize(
    ("collision_kind", "mutate"),
    (
        (
            "component nkey reuses another component principal",
            lambda bundle_value, _descriptor: bundle_value["components"][1].__setitem__(
                "nkeySelector",
                bundle_value["components"][0]["componentPrincipal"],
            ),
        ),
        (
            "base probe nkey reuses component principal",
            lambda bundle_value, descriptor_value: descriptor_value["fixedBaseSlots"][
                0
            ].__setitem__(
                "probeNkeySelector",
                bundle_value["components"][0]["componentPrincipal"],
            ),
        ),
        (
            "base reporter principal reuses component nkey",
            lambda bundle_value, descriptor_value: descriptor_value["fixedBaseSlots"][
                0
            ].__setitem__(
                "readyReporterPrincipal",
                bundle_value["components"][0]["nkeySelector"],
            ),
        ),
        (
            "one component reuses its principal as nkey",
            lambda bundle_value, _descriptor: bundle_value["components"][0].__setitem__(
                "nkeySelector",
                bundle_value["components"][0]["componentPrincipal"],
            ),
        ),
    ),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_principal_and_nkey_values_share_one_global_identity_namespace(
    collision_kind: str,
    mutate,
) -> None:
    bundle_value = bundle()
    descriptor_value = descriptor()
    mutate(bundle_value, descriptor_value)

    with pytest.raises(RequiredSlotError, match="duplicate global identity"):
        required_slot_set("CORE", bundle_value, descriptor_value)


def test_required_recovery_projection_is_exported_from_package_root() -> None:
    assert a2amesh.required_recovery_projection is required_recovery_projection
    assert "required_recovery_projection" in a2amesh.__all__


def test_component_required_profiles_are_closed() -> None:
    value = bundle()
    value["components"][0]["requiredForProfiles"].append("BETA")
    with pytest.raises(RequiredSlotError, match="unsupported profile"):
        required_slot_set("CORE", value, descriptor())
