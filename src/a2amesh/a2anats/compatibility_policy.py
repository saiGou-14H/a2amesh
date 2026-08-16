"""Fail-closed policy boundary for the deprecated private NATS RPC binding."""

from __future__ import annotations

from dataclasses import dataclass

LEGACY_RPC_SUBJECT_PREFIX = "a2a.rpc."
LEGACY_CARD_SUBJECT_PREFIX = "a2a.cards."
LEGACY_PRIVATE_RPC_METHODS = frozenset(
    {
        "message/send",
        "message/stream",
        "tasks/get",
        "tasks/cancel",
        "tools/call",
    }
)


class LegacyCompatibilityDisabledError(RuntimeError):
    """Raised before I/O when deprecated private RPC compatibility is disabled."""


@dataclass(frozen=True, slots=True)
class LegacyCompatibilityPolicy:
    """An explicit, default-off opt-in for the deprecated private RPC binding."""

    enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("legacy compatibility enabled flag must be boolean")

    def require_enabled(self, action: str) -> None:
        if not self.enabled:
            raise LegacyCompatibilityDisabledError(
                "legacy private NATS RPC compatibility is disabled; "
                f"cannot {action}; use the a2a.v1 binding or explicitly opt in"
            )

    @staticmethod
    def rpc_subject(agent_id: str) -> str:
        return f"{LEGACY_RPC_SUBJECT_PREFIX}{_safe_agent_token(agent_id)}"

    @staticmethod
    def card_subject(agent_id: str) -> str:
        return f"{LEGACY_CARD_SUBJECT_PREFIX}{_safe_agent_token(agent_id)}"


def _safe_agent_token(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 63
        or any(character in value for character in ".*> \t\r\n")
    ):
        raise ValueError("legacy agent ID must be one safe NATS subject token")
    return value
