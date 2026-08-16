"""A2AMesh — symmetric A2A Agent Mesh over NATS."""

from .config_slots import (
    BASE_REQUIRED_SLOT_TYPES,
    RequiredSlotError,
    StableSlot,
    required_slot_projection,
    required_slot_set,
)

__version__ = "0.1.0"

__all__ = [
    "BASE_REQUIRED_SLOT_TYPES",
    "RequiredSlotError",
    "StableSlot",
    "required_slot_projection",
    "required_slot_set",
]
