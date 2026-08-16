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
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MISSING = object()


class RequiredSlotError(ValueError):
    """Raised when a signed bundle cannot produce a closed stable slot set."""


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
    """Compute the exact stable slots required by one signed profile.

    The function is deliberately pure and does not trust a caller-provided
    ``requiredSlots`` projection.  If that projection is present in the bundle,
    it is compared byte-for-byte (field values and order) with the result.
    """

    _stable_text(profile_name, "profile_name")
    base_slots = _read_base_slots(deployment_descriptor)
    all_component_slots, profile_component_slots = _read_component_slots(
        bundle, profile_name
    )
    known_slots = base_slots + all_component_slots
    _reject_duplicate_slots(known_slots, "base/component slots")

    delivery_profile = _mapping(bundle.get("deliveryProfile"), "deliveryProfile")
    explicit_value = delivery_profile.get("explicitlyRequiredOperationalSlots", [])
    explicit_slots = _read_explicit_slots(explicit_value, known_slots)

    generated = _sort_slots(_unique_slots(base_slots + profile_component_slots + explicit_slots))
    _validate_required_slots_projection(delivery_profile, generated)
    _validate_recovery_projection(bundle, generated)
    return generated


def required_slot_projection(
    profile_name: str,
    bundle: Mapping[str, Any],
    deployment_descriptor: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Return the canonical JSON-friendly ``requiredSlots`` projection."""

    return [
        slot.as_dict()
        for slot in required_slot_set(profile_name, bundle, deployment_descriptor)
    ]


def _read_base_slots(descriptor: Mapping[str, Any]) -> tuple[StableSlot, ...]:
    values = _sequence(descriptor.get("fixedBaseSlots", _MISSING), "fixedBaseSlots")
    slots = tuple(
        _slot_from_mapping(value, f"fixedBaseSlots[{index}]")
        for index, value in enumerate(values)
    )
    missing = _REQUIRED_SLOT_TYPE_SET - {slot.component_type for slot in slots}
    if missing:
        raise RequiredSlotError(
            "fixedBaseSlots is missing required types: " + ", ".join(sorted(missing))
        )
    unknown = {slot.component_type for slot in slots} - _REQUIRED_SLOT_TYPE_SET
    if unknown:
        raise RequiredSlotError(
            "fixedBaseSlots contains unknown types: " + ", ".join(sorted(unknown))
        )
    return slots


def _read_component_slots(
    bundle: Mapping[str, Any], profile_name: str
) -> tuple[tuple[StableSlot, ...], tuple[StableSlot, ...]]:
    values = _sequence(bundle.get("components", _MISSING), "components")
    all_slots: list[StableSlot] = []
    profile_slots: list[StableSlot] = []
    for index, value in enumerate(values):
        item = _mapping(value, f"components[{index}]")
        slot = _slot_from_mapping(item, f"components[{index}]")
        if slot.component_type == "merge-broker":
            raise RequiredSlotError(
                "application-core owns Merge Broker; merge-broker is not a slot"
            )
        profiles = _sequence(
            item.get("requiredForProfiles", _MISSING),
            f"components[{index}].requiredForProfiles",
        )
        profile_values = tuple(
            _stable_text(profile, f"components[{index}].requiredForProfiles[{pindex}]")
            for pindex, profile in enumerate(profiles)
        )
        if len(set(profile_values)) != len(profile_values):
            raise RequiredSlotError(
                f"components[{index}].requiredForProfiles contains duplicates"
            )
        all_slots.append(slot)
        if profile_name in profile_values:
            profile_slots.append(slot)
    return tuple(all_slots), tuple(profile_slots)


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
        return
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
    bundle: Mapping[str, Any], generated: Sequence[StableSlot]
) -> None:
    recovery = bundle.get("recoveryPolicy", _MISSING)
    if recovery is _MISSING:
        return
    mapping = _mapping(recovery, "recoveryPolicy")
    projection = _sequence(
        mapping.get("requiredComponents", _MISSING),
        "recoveryPolicy.requiredComponents",
    )
    parsed: list[StableSlot] = []
    for index, value in enumerate(projection):
        item = _mapping(value, f"recoveryPolicy.requiredComponents[{index}]")
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
        slot = _slot_from_mapping(item, f"recoveryPolicy.requiredComponents[{index}]")
        _stable_text(
            item["verificationMethod"],
            f"recoveryPolicy.requiredComponents[{index}].verificationMethod",
        )
        digest = _stable_text(
            item["expectedDigest"],
            f"recoveryPolicy.requiredComponents[{index}].expectedDigest",
        )
        if not _SHA256_RE.fullmatch(digest):
            raise RequiredSlotError(
                f"recoveryPolicy.requiredComponents[{index}].expectedDigest "
                "must be lowercase SHA-256 hex"
            )
        if slot in parsed:
            raise RequiredSlotError("recovery required components contain a duplicate")
        parsed.append(slot)
    if tuple(parsed) != tuple(generated):
        raise RequiredSlotError(
            "recoveryPolicy.requiredComponents does not equal RequiredSlotSetV1"
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
