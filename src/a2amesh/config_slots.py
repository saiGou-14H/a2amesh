"""Deterministic RequiredSlotSetV1 projection for signed configuration bundles."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

BASE_REQUIRED_SLOT_TYPES = (
    "CONFIG_CONTROLLER",
    "STATE_SERVICE",
    "GATEWAY",
    "NATS_JETSTREAM",
    "OBJECT_STORE",
    "ARTIFACT_BROKER",
    "AUDIT_SINK",
)
_REQUIRED_SLOT_TYPE_SET = frozenset(BASE_REQUIRED_SLOT_TYPES)
_DELIVERY_PROFILE_SET = frozenset({"CORE", "INTEROP", "EXTENDED"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MISSING = object()


class RequiredSlotError(ValueError):
    """Raised when a signed bundle cannot produce a closed stable slot set."""


@dataclass(frozen=True, slots=True)
class _RecoveryAuthority:
    verification_method: str
    expected_digest: str


@dataclass(frozen=True, slots=True)
class StableSlot:
    component_type: str
    component_principal: str
    node_id: str

    def sort_key(self) -> tuple[bytes, bytes, bytes]:
        return tuple(
            value.encode("utf-8")
            for value in (
                self.component_type,
                self.component_principal,
                self.node_id,
            )
        )  # type: ignore[return-value]

    def as_dict(self) -> dict[str, str]:
        return {
            "componentType": self.component_type,
            "componentPrincipal": self.component_principal,
            "nodeId": self.node_id,
        }


def required_slot_set(
    profile_name: str,
    bundle: Mapping[str, Any],
    deployment_descriptor: Mapping[str, Any],
) -> tuple[StableSlot, ...]:
    """Validate and return the exact signed stable slots for one profile.

    Both signed projections are mandatory and must equal the independently
    recomputed ``RequiredSlotSetV1`` in exact order.
    """

    slots, _ = _compute_required_slots(
        profile_name,
        bundle,
        deployment_descriptor,
        validate_projections=True,
    )
    return slots


def required_slot_projection(
    profile_name: str,
    bundle: Mapping[str, Any],
    deployment_descriptor: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Generate the canonical stable-slot projection before bundle signing."""

    slots, _ = _compute_required_slots(
        profile_name,
        bundle,
        deployment_descriptor,
        validate_projections=False,
    )
    return [slot.as_dict() for slot in slots]


def required_recovery_projection(
    profile_name: str,
    bundle: Mapping[str, Any],
    deployment_descriptor: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Generate the canonical proof-bound recovery projection before signing."""

    slots, authorities = _compute_required_slots(
        profile_name,
        bundle,
        deployment_descriptor,
        validate_projections=False,
    )
    return _render_recovery_projection(slots, authorities)


def _compute_required_slots(
    profile_name: str,
    bundle: Mapping[str, Any],
    deployment_descriptor: Mapping[str, Any],
    *,
    validate_projections: bool,
) -> tuple[tuple[StableSlot, ...], dict[StableSlot, _RecoveryAuthority]]:
    _validate_profile_name(profile_name, "profile_name")
    (
        base_slots,
        identity_owners,
        authorities,
    ) = _read_base_slots(deployment_descriptor)
    (
        all_component_slots,
        profile_component_slots,
        authorities,
    ) = _read_component_slots(
        bundle,
        profile_name,
        identity_owners=identity_owners,
        authorities=authorities,
    )
    known_slots = base_slots + all_component_slots
    _reject_duplicate_slots(known_slots, "base/component slots")

    delivery_profile = _mapping(bundle.get("deliveryProfile"), "deliveryProfile")
    explicit_value = delivery_profile.get("explicitlyRequiredOperationalSlots", [])
    explicit_slots = _read_explicit_slots(explicit_value, known_slots)
    generated = _sort_slots(
        _unique_slots(base_slots + profile_component_slots + explicit_slots)
    )
    if validate_projections:
        _validate_required_slots_projection(delivery_profile, generated)
        _validate_recovery_projection(bundle, generated, authorities)
    return generated, authorities


def _read_base_slots(
    descriptor: Mapping[str, Any],
) -> tuple[
    tuple[StableSlot, ...],
    dict[str, str],
    dict[StableSlot, _RecoveryAuthority],
]:
    values = _sequence(descriptor.get("fixedBaseSlots", _MISSING), "fixedBaseSlots")
    slots: list[StableSlot] = []
    identity_owners: dict[str, str] = {}
    authorities: dict[StableSlot, _RecoveryAuthority] = {}
    for index, value in enumerate(values):
        label = f"fixedBaseSlots[{index}]"
        item = _mapping(value, label)
        slot = _slot_from_mapping(item, label)
        ready_reporter = _stable_text(
            item.get("readyReporterPrincipal", _MISSING),
            f"{label}.readyReporterPrincipal",
        )
        probe_nkey = _stable_text(
            item.get("probeNkeySelector", _MISSING),
            f"{label}.probeNkeySelector",
        )
        authority = _read_recovery_authority(item, label)
        _claim_unique_identity(
            identity_owners,
            slot.component_principal,
            "Principal",
            f"{label}.componentPrincipal",
        )
        _claim_unique_identity(
            identity_owners,
            ready_reporter,
            "Principal",
            f"{label}.readyReporterPrincipal",
        )
        _claim_unique_identity(
            identity_owners,
            probe_nkey,
            "nkeySelector",
            f"{label}.probeNkeySelector",
        )
        authorities[slot] = authority
        slots.append(slot)
    slot_tuple = tuple(slots)
    missing = _REQUIRED_SLOT_TYPE_SET - {
        slot.component_type for slot in slot_tuple
    }
    if missing:
        raise RequiredSlotError(
            "fixedBaseSlots is missing required types: " + ", ".join(sorted(missing))
        )
    unknown = {
        slot.component_type for slot in slot_tuple
    } - _REQUIRED_SLOT_TYPE_SET
    if unknown:
        raise RequiredSlotError(
            "fixedBaseSlots contains unknown types: " + ", ".join(sorted(unknown))
        )
    return slot_tuple, identity_owners, authorities


def _read_component_slots(
    bundle: Mapping[str, Any],
    profile_name: str,
    *,
    identity_owners: dict[str, str],
    authorities: dict[StableSlot, _RecoveryAuthority],
) -> tuple[
    tuple[StableSlot, ...],
    tuple[StableSlot, ...],
    dict[StableSlot, _RecoveryAuthority],
]:
    values = _sequence(bundle.get("components", _MISSING), "components")
    all_slots: list[StableSlot] = []
    profile_slots: list[StableSlot] = []
    identity_claims = dict(identity_owners)
    authority_claims = dict(authorities)
    for index, value in enumerate(values):
        label = f"components[{index}]"
        item = _mapping(value, label)
        slot = _slot_from_mapping(item, label)
        if slot.component_type == "merge-broker":
            raise RequiredSlotError(
                "application-core owns Merge Broker; merge-broker is not a slot"
            )
        nkey_selector = _stable_text(
            item.get("nkeySelector", _MISSING),
            f"{label}.nkeySelector",
        )
        _claim_unique_identity(
            identity_claims,
            slot.component_principal,
            "Principal",
            f"{label}.componentPrincipal",
        )
        _claim_unique_identity(
            identity_claims,
            nkey_selector,
            "nkeySelector",
            f"{label}.nkeySelector",
        )
        authority_claims[slot] = _read_recovery_authority(item, label)
        profiles = _sequence(
            item.get("requiredForProfiles", _MISSING),
            f"{label}.requiredForProfiles",
        )
        profile_values = tuple(
            _validate_profile_name(
                profile,
                f"{label}.requiredForProfiles[{pindex}]",
            )
            for pindex, profile in enumerate(profiles)
        )
        if len(set(profile_values)) != len(profile_values):
            raise RequiredSlotError(
                f"{label}.requiredForProfiles contains duplicates"
            )
        all_slots.append(slot)
        if profile_name in profile_values:
            profile_slots.append(slot)
    return tuple(all_slots), tuple(profile_slots), authority_claims


def _read_explicit_slots(value: Any, known: Sequence[StableSlot]) -> tuple[StableSlot, ...]:
    values = _sequence(value, "deliveryProfile.explicitlyRequiredOperationalSlots")
    known_set = set(known)
    result: list[StableSlot] = []
    for index, item in enumerate(values):
        mapping = _mapping(
            item, f"deliveryProfile.explicitlyRequiredOperationalSlots[{index}]"
        )
        expected = {"componentType", "componentPrincipal", "nodeId"}
        if set(mapping) != expected:
            raise RequiredSlotError(
                "explicit operational slot has missing or extra fields at "
                f"index {index}"
            )
        slot = _slot_from_mapping(
            mapping, f"deliveryProfile.explicitlyRequiredOperationalSlots[{index}]"
        )
        if slot.component_type == "merge-broker":
            raise RequiredSlotError("merge-broker cannot be an operational slot")
        if slot not in known_set:
            raise RequiredSlotError(
                "explicit operational slot does not resolve to a signed stable slot: "
                + repr(slot.as_dict())
            )
        if slot in result:
            raise RequiredSlotError("explicit operational slots contain a duplicate")
        result.append(slot)
    return tuple(result)


def _validate_required_slots_projection(
    delivery_profile: Mapping[str, Any], generated: Sequence[StableSlot]
) -> None:
    projection = delivery_profile.get("requiredSlots", _MISSING)
    if projection is _MISSING:
        raise RequiredSlotError("deliveryProfile.requiredSlots is required")
    values = _sequence(projection, "deliveryProfile.requiredSlots")
    parsed = tuple(
        _slot_from_exact_mapping(value, f"deliveryProfile.requiredSlots[{index}]")
        for index, value in enumerate(values)
    )
    if parsed != tuple(generated):
        raise RequiredSlotError(
            "deliveryProfile.requiredSlots does not equal RequiredSlotSetV1"
        )


def _validate_recovery_projection(
    bundle: Mapping[str, Any],
    generated: Sequence[StableSlot],
    authorities: Mapping[StableSlot, _RecoveryAuthority],
) -> None:
    recovery = bundle.get("recoveryPolicy", _MISSING)
    if recovery is _MISSING:
        raise RequiredSlotError("recoveryPolicy is required")
    mapping = _mapping(recovery, "recoveryPolicy")
    projection = mapping.get("requiredComponents", _MISSING)
    if projection is _MISSING:
        raise RequiredSlotError("recoveryPolicy.requiredComponents is required")
    values = _sequence(
        projection,
        "recoveryPolicy.requiredComponents",
    )
    parsed: list[tuple[StableSlot, _RecoveryAuthority]] = []
    parsed_slots: set[StableSlot] = set()
    for index, value in enumerate(values):
        label = f"recoveryPolicy.requiredComponents[{index}]"
        item = _mapping(value, label)
        expected = {
            "componentType",
            "componentPrincipal",
            "nodeId",
            "verificationMethod",
            "expectedDigest",
        }
        if set(item) != expected:
            raise RequiredSlotError(
                "recovery required component has missing or extra fields at "
                f"index {index}"
            )
        slot = _slot_from_mapping(item, label)
        authority = _read_recovery_authority(item, label)
        if slot in parsed_slots:
            raise RequiredSlotError("recovery required components contain a duplicate")
        parsed_slots.add(slot)
        parsed.append((slot, authority))
    expected_projection = tuple(
        (slot, _authority_for(slot, authorities)) for slot in generated
    )
    if tuple(parsed) != expected_projection:
        raise RequiredSlotError(
            "recoveryPolicy.requiredComponents does not equal "
            "RequiredSlotSetV1 proof metadata"
        )


def _render_recovery_projection(
    slots: Sequence[StableSlot],
    authorities: Mapping[StableSlot, _RecoveryAuthority],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for slot in slots:
        authority = _authority_for(slot, authorities)
        result.append(
            slot.as_dict()
            | {
                "verificationMethod": authority.verification_method,
                "expectedDigest": authority.expected_digest,
            }
        )
    return result


def _authority_for(
    slot: StableSlot,
    authorities: Mapping[StableSlot, _RecoveryAuthority],
) -> _RecoveryAuthority:
    authority = authorities.get(slot)
    if authority is None:
        raise RequiredSlotError(
            "required stable slot has no authoritative recovery proof metadata: "
            + repr(slot.as_dict())
        )
    return authority


def _read_recovery_authority(
    value: Mapping[str, Any], label: str
) -> _RecoveryAuthority:
    verification_method = _stable_text(
        value.get("verificationMethod", _MISSING),
        f"{label}.verificationMethod",
    )
    expected_digest = _stable_text(
        value.get("expectedDigest", _MISSING),
        f"{label}.expectedDigest",
    )
    if not _SHA256_RE.fullmatch(expected_digest):
        raise RequiredSlotError(
            f"{label}.expectedDigest must be lowercase SHA-256 hex"
        )
    return _RecoveryAuthority(
        verification_method=verification_method,
        expected_digest=expected_digest,
    )


def _slot_from_exact_mapping(value: Any, label: str) -> StableSlot:
    mapping = _mapping(value, label)
    expected = {"componentType", "componentPrincipal", "nodeId"}
    if set(mapping) != expected:
        raise RequiredSlotError(f"{label} has missing or extra fields")
    return _slot_from_mapping(mapping, label)


def _slot_from_mapping(value: Mapping[str, Any], label: str) -> StableSlot:
    return StableSlot(
        component_type=_stable_text(value.get("componentType", _MISSING), f"{label}.componentType"),
        component_principal=_stable_text(
            value.get("componentPrincipal", _MISSING), f"{label}.componentPrincipal"
        ),
        node_id=_stable_text(value.get("nodeId", _MISSING), f"{label}.nodeId"),
    )


def _reject_duplicate_slots(slots: Sequence[StableSlot], label: str) -> None:
    if len(set(slots)) != len(slots):
        raise RequiredSlotError(f"{label} contains duplicate stable slots")


def _unique_slots(slots: Sequence[StableSlot]) -> tuple[StableSlot, ...]:
    unique: dict[StableSlot, None] = {}
    for slot in slots:
        unique.setdefault(slot, None)
    return tuple(unique)


def _sort_slots(slots: Sequence[StableSlot]) -> tuple[StableSlot, ...]:
    return tuple(sorted(slots, key=StableSlot.sort_key))


def _validate_profile_name(value: Any, label: str) -> str:
    if not isinstance(value, str) or value not in _DELIVERY_PROFILE_SET:
        raise RequiredSlotError(
            f"{label} has unsupported profile {value!r}; "
            "expected CORE, INTEROP, or EXTENDED"
        )
    return _stable_text(value, label)


def _claim_unique_identity(
    owners: dict[str, str],
    value: str,
    identity_type: str,
    owner: str,
) -> None:
    previous = owners.get(value)
    if previous is not None:
        raise RequiredSlotError(
            "duplicate global identity claimed by "
            f"{previous} and {identity_type} {owner}"
        )
    owners[value] = f"{identity_type} {owner}"


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RequiredSlotError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if value is _MISSING or isinstance(value, (str, bytes, bytearray)):
        raise RequiredSlotError(f"{label} must be an array")
    if not isinstance(value, Sequence):
        raise RequiredSlotError(f"{label} must be an array")
    return value


def _stable_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RequiredSlotError(f"{label} must be a non-empty trimmed string")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise RequiredSlotError(f"{label} contains a control character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RequiredSlotError(f"{label} is not valid UTF-8") from exc
    return value
