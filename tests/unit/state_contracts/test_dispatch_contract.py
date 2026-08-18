from __future__ import annotations

import pytest

from a2amesh import state_contracts
from a2amesh.state_contracts.dispatch import (
    DispatchContractError,
    DispatchIntent,
    DispatchIntentState,
    accept_dispatch,
    claim_dispatch,
    create_dispatch_intent,
    mark_dispatch_sent,
    reclaim_dispatch,
)

CLAIM_ID_A = "claim-token-01"
CLAIM_ID_B = "claim-token-02"

BASE = {
    "dispatch_id": "dispatch-01",
    "task_id": "task-01",
    "target_agent_id": "worker-a",
    "command_digest": "11" * 32,
    "task_version": 1,
    "config_generation": 3,
}


def intent(**changes: object) -> DispatchIntent:
    return create_dispatch_intent(**(BASE | changes))


def test_dispatch_facade_exports_same_contract_types() -> None:
    assert state_contracts.DispatchIntent is DispatchIntent
    assert state_contracts.create_dispatch_intent is create_dispatch_intent


def test_new_intent_is_pending_and_has_stable_identity() -> None:
    first = intent()
    second = intent()
    assert first.state is DispatchIntentState.PENDING
    assert first.attempt == 1
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.intent_digest == second.intent_digest


def test_claim_send_accept_is_strict_and_idempotent() -> None:
    pending = intent()
    claimed = claim_dispatch(
        pending,
        owner_instance_id="dispatcher-01",
        fencing_token=7,
        claim_token=CLAIM_ID_A,
        lease_until_ms=2_000,
        now_ms=1_000,
    )
    assert claimed.state is DispatchIntentState.CLAIMED
    sent = mark_dispatch_sent(
        claimed,
        owner_instance_id="dispatcher-01",
        fencing_token=7,
        claim_token=CLAIM_ID_A,
        attempt=1,
        now_ms=1_500,
    )
    assert sent.state is DispatchIntentState.SENT
    accepted = accept_dispatch(
        sent,
        owner_instance_id="dispatcher-01",
        fencing_token=7,
        claim_token=CLAIM_ID_A,
        attempt=1,
        now_ms=1_500,
    )
    assert accepted.state is DispatchIntentState.ACCEPTED
    assert accept_dispatch(
        accepted,
        owner_instance_id="dispatcher-01",
        fencing_token=7,
        claim_token=CLAIM_ID_A,
        attempt=1,
        now_ms=1_500,
    ) is accepted

    with pytest.raises(DispatchContractError, match="state"):
        mark_dispatch_sent(
            pending,
            owner_instance_id="dispatcher-01",
            fencing_token=7,
            claim_token=CLAIM_ID_A,
            attempt=1,
            now_ms=1_500,
        )
    with pytest.raises(DispatchContractError, match="owner"):
        accept_dispatch(
            sent,
            owner_instance_id="other",
            fencing_token=7,
            claim_token=CLAIM_ID_A,
            attempt=1,
            now_ms=1_500,
        )
    with pytest.raises(DispatchContractError, match="token"):
        accept_dispatch(
            sent,
            owner_instance_id="dispatcher-01",
            fencing_token=7,
            claim_token=CLAIM_ID_B,
            attempt=1,
            now_ms=1_500,
        )


def test_reclaim_requires_expiry_and_monotonically_invalidates_old_claim() -> None:
    claimed = claim_dispatch(
        intent(),
        owner_instance_id="dispatcher-01",
        fencing_token=7,
        claim_token=CLAIM_ID_A,
        lease_until_ms=2_000,
        now_ms=1_000,
    )
    with pytest.raises(DispatchContractError, match="expired"):
        reclaim_dispatch(
            claimed,
            new_owner_instance_id="dispatcher-02",
            new_fencing_token=8,
            new_claim_token=CLAIM_ID_B,
            new_lease_until_ms=3_000,
            now_ms=1_999,
        )

    reclaimed = reclaim_dispatch(
        claimed,
        new_owner_instance_id="dispatcher-02",
        new_fencing_token=8,
        new_claim_token=CLAIM_ID_B,
        new_lease_until_ms=3_000,
        now_ms=2_000,
    )
    assert reclaimed.state is DispatchIntentState.CLAIMED
    assert reclaimed.attempt == 2
    assert reclaimed.fencing_token == 8
    assert reclaimed.owner_instance_id == "dispatcher-02"
    with pytest.raises(DispatchContractError, match="fence"):
        mark_dispatch_sent(
            reclaimed,
            owner_instance_id="dispatcher-01",
            fencing_token=7,
            claim_token=CLAIM_ID_A,
            attempt=1,
            now_ms=2_500,
        )
    with pytest.raises(DispatchContractError, match="attempt"):
        mark_dispatch_sent(
            reclaimed,
            owner_instance_id="dispatcher-02",
            fencing_token=8,
            claim_token=CLAIM_ID_B,
            attempt=1,
            now_ms=2_500,
        )


def test_dispatch_rejects_bool_float_and_digest_mutation() -> None:
    with pytest.raises(DispatchContractError):
        intent(task_version=True)
    with pytest.raises(DispatchContractError):
        intent(config_generation=1.0)
    with pytest.raises(DispatchContractError):
        intent(command_digest="not-a-digest")
