from __future__ import annotations

from dataclasses import replace

import pytest

from a2amesh import state_contracts
from a2amesh.state_contracts.lease import (
    LeaseContractError,
    LeaseGrant,
    renew_lease,
    validate_lease_write,
)

BASE = {
    "lease_id": "lease-01",
    "owner_principal_id": "agent:caller",
    "owner_instance_id": "instance-01",
    "fencing_token": 7,
    "attempt": 1,
    "issued_at_ms": 1_000,
    "lease_until_ms": 2_000,
    "config_generation": 3,
    "request_digest": "11" * 32,
}


class ExplodingOwner(str):
    def __ne__(self, other: object) -> bool:
        del other
        raise RuntimeError("owner comparison must not run")


class ExplodingBool:
    def __bool__(self) -> bool:
        raise RuntimeError("lease snapshot truthiness must not run")


def grant(**changes: object) -> LeaseGrant:
    values = BASE | changes
    return LeaseGrant(**values)


def test_lease_facade_exports_same_contract_types() -> None:
    assert state_contracts.LeaseGrant is LeaseGrant
    assert state_contracts.renew_lease is renew_lease
    assert state_contracts.validate_lease_write is validate_lease_write


def test_lease_grant_is_deterministic_and_exactly_typed() -> None:
    first = grant()
    second = grant()
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.lease_digest == second.lease_digest

    with pytest.raises(LeaseContractError):
        grant(fencing_token=True)
    with pytest.raises(LeaseContractError):
        grant(owner_instance_id=" ")


def test_renew_requires_same_owner_and_fence_and_only_extends() -> None:
    current = grant()
    renewed = renew_lease(
        current,
        owner_principal_id="agent:caller",
        owner_instance_id="instance-01",
        fencing_token=7,
        lease_until_ms=3_000,
    )
    assert renewed.lease_until_ms == 3_000
    assert renewed.attempt == current.attempt
    assert renewed.fencing_token == current.fencing_token

    with pytest.raises(LeaseContractError, match="owner"):
        renew_lease(
            current,
            owner_principal_id="agent:other",
            owner_instance_id="instance-01",
            fencing_token=7,
            lease_until_ms=3_000,
        )
    with pytest.raises(LeaseContractError, match="fence"):
        renew_lease(
            current,
            owner_principal_id="agent:caller",
            owner_instance_id="instance-01",
            fencing_token=6,
            lease_until_ms=3_000,
        )
    with pytest.raises(LeaseContractError, match="extend"):
        renew_lease(
            current,
            owner_principal_id="agent:caller",
            owner_instance_id="instance-01",
            fencing_token=7,
            lease_until_ms=1_500,
        )

    with pytest.raises(LeaseContractError, match="owner_principal_id"):
        renew_lease(
            current,
            owner_principal_id=ExplodingOwner("agent:other"),  # type: ignore[arg-type]
            owner_instance_id="instance-01",
            fencing_token=7,
            lease_until_ms=3_000,
        )


def test_write_authority_rejects_old_owner_fence_attempt_and_generation() -> None:
    current = grant()
    assert validate_lease_write(
        current,
        owner_principal_id="agent:caller",
        owner_instance_id="instance-01",
        fencing_token=7,
        attempt=1,
        config_generation=3,
        now_ms=1_500,
    ) is None

    cases = [
        ("owner", {"owner_instance_id": "instance-old"}),
        ("fence", {"fencing_token": 6}),
        ("attempt", {"attempt": 0}),
        ("generation", {"config_generation": 2}),
        ("expired", {"now_ms": 2_000}),
    ]
    for label, changes in cases:
        kwargs = {
            "owner_principal_id": "agent:caller",
            "owner_instance_id": "instance-01",
            "fencing_token": 7,
            "attempt": 1,
            "config_generation": 3,
            "now_ms": 1_500,
        }
        kwargs.update(changes)
        with pytest.raises(LeaseContractError, match=label):
            validate_lease_write(current, **kwargs)


def test_new_fence_is_a_new_lease_and_old_grant_cannot_be_mutated() -> None:
    old = grant()
    new = grant(lease_id="lease-02", fencing_token=8, attempt=2)
    assert new.fencing_token > old.fencing_token
    assert old.lease_digest != new.lease_digest
    with pytest.raises(LeaseContractError):
        replace(old, lease_until_ms=0).assert_integrity()


def test_lease_semantics_are_not_bypassable_by_self_rehashed_mutation() -> None:
    bad_generation = grant()
    object.__setattr__(bad_generation, "config_generation", 0)
    object.__setattr__(bad_generation, "_snapshot_digest", bad_generation._compute_digest())
    with pytest.raises(LeaseContractError, match="config_generation"):
        bad_generation.assert_integrity()
    with pytest.raises(LeaseContractError, match="config_generation"):
        validate_lease_write(
            bad_generation,
            owner_principal_id="agent:caller",
            owner_instance_id="instance-01",
            fencing_token=7,
            attempt=1,
            config_generation=0,
            now_ms=1_500,
        )

    bad_request = grant()
    object.__setattr__(bad_request, "request_digest", "not-a-digest")
    object.__setattr__(bad_request, "_snapshot_digest", bad_request._compute_digest())
    with pytest.raises(LeaseContractError, match="request_digest"):
        bad_request.assert_integrity()


def test_lease_text_unicode_and_snapshot_sentinel_are_bounded() -> None:
    with pytest.raises(LeaseContractError, match="UTF-8"):
        grant(owner_instance_id="\ud800")
    with pytest.raises(LeaseContractError, match="snapshot digest"):
        LeaseGrant(**BASE, _snapshot_digest=ExplodingBool())  # type: ignore[arg-type]
