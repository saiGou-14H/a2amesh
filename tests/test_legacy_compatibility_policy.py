"""Default-off policy contract for deprecated private NATS RPC compatibility."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from a2amesh.a2anats import (
    LEGACY_CARD_SUBJECT_PREFIX,
    LEGACY_PRIVATE_RPC_METHODS,
    LEGACY_RPC_SUBJECT_PREFIX,
    LegacyCompatibilityDisabledError,
    LegacyCompatibilityPolicy,
)
from a2amesh.config import Config
from a2amesh.core import Operation


def config(**extra: object) -> Config:
    data: dict[str, object] = {"agent": {"name": "worker"}}
    data.update(extra)
    return Config.model_validate(data)


def test_legacy_private_rpc_is_default_off_in_policy_and_runtime_config() -> None:
    policy = LegacyCompatibilityPolicy()
    runtime_config = config()

    assert policy.enabled is False
    assert runtime_config.compatibility.legacy_private_rpc_enabled is False
    with pytest.raises(LegacyCompatibilityDisabledError, match="explicitly opt in"):
        policy.require_enabled("publish a request")


def test_legacy_compatibility_requires_an_explicit_strict_boolean_opt_in() -> None:
    enabled = config(
        compatibility={"legacy_private_rpc_enabled": True},
    )
    assert enabled.compatibility.legacy_private_rpc_enabled is True
    LegacyCompatibilityPolicy(enabled=True).require_enabled("run compatibility adapter")

    for value in (1, 0, "true", "false", None):
        with pytest.raises(ValidationError):
            config(compatibility={"legacy_private_rpc_enabled": value})
    with pytest.raises(ValidationError):
        config(compatibility={"unknown_legacy_switch": True})
    with pytest.raises(TypeError, match="must be boolean"):
        LegacyCompatibilityPolicy(enabled=1)  # type: ignore[arg-type]


def test_legacy_method_set_is_closed_and_cannot_be_mistaken_for_v1_operations() -> None:
    assert LEGACY_PRIVATE_RPC_METHODS == {
        "message/send",
        "message/stream",
        "tasks/get",
        "tasks/cancel",
        "tools/call",
    }
    assert LEGACY_RPC_SUBJECT_PREFIX == "a2a.rpc."
    assert LEGACY_CARD_SUBJECT_PREFIX == "a2a.cards."
    official_operations = {operation.value for operation in Operation}
    assert all(method not in official_operations for method in LEGACY_PRIVATE_RPC_METHODS)
    assert all(not method.startswith("a2a.v1") for method in LEGACY_PRIVATE_RPC_METHODS)


def test_legacy_subject_builder_rejects_wildcards_and_is_disjoint_from_v1() -> None:
    assert LegacyCompatibilityPolicy.rpc_subject("windows-a") == "a2a.rpc.windows-a"
    assert LegacyCompatibilityPolicy.card_subject("windows-a") == "a2a.cards.windows-a"
    assert LegacyCompatibilityPolicy.rpc_subject("windows-a") != "a2a.v1.rpc.windows-a"

    for value in ("", "bad.name", "bad*", "bad>", "has space", "x" * 64):
        with pytest.raises(ValueError, match="safe NATS subject token"):
            LegacyCompatibilityPolicy.rpc_subject(value)
