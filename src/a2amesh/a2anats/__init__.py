"""A2A-over-NATS transport helpers and explicit legacy compatibility policy."""

from .compatibility_policy import (
    LEGACY_CARD_SUBJECT_PREFIX,
    LEGACY_PRIVATE_RPC_METHODS,
    LEGACY_RPC_SUBJECT_PREFIX,
    LegacyCompatibilityDisabledError,
    LegacyCompatibilityPolicy,
)

__all__ = [
    "LEGACY_CARD_SUBJECT_PREFIX",
    "LEGACY_PRIVATE_RPC_METHODS",
    "LEGACY_RPC_SUBJECT_PREFIX",
    "LegacyCompatibilityDisabledError",
    "LegacyCompatibilityPolicy",
]
