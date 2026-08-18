from __future__ import annotations

import pytest

from a2amesh import state_contracts
from a2amesh.state_contracts.outbox import (
    OutboxContractError,
    OutboxEvent,
    OutboxState,
    append_event,
    claim_event,
    create_outbox_event,
    mark_published,
    next_publishable,
)

DIGEST_A = "11" * 32
DIGEST_B = "22" * 32
CLAIM_ID = "claim-a"
WRONG_CLAIM_ID = "wrong-claim"


def event(seq: int, *, digest: str = DIGEST_A) -> OutboxEvent:
    return create_outbox_event(
        task_id="task-01",
        event_seq=seq,
        task_version=seq,
        event_type="TaskStatusChanged",
        payload_digest=digest,
    )


def test_outbox_facade_exports_same_contract_types() -> None:
    assert state_contracts.OutboxEvent is OutboxEvent
    assert state_contracts.append_event is append_event


def test_event_identity_and_digest_are_stable() -> None:
    first = event(1)
    second = event(1)
    assert first.event_id == "task-01:1"
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.event_digest == second.event_digest
    assert first.state is OutboxState.PENDING


def test_append_requires_contiguous_sequence_and_rejects_duplicate() -> None:
    with pytest.raises(OutboxContractError, match="contiguous"):
        append_event((), event(2))
    first = event(1)
    events = append_event((), first)
    assert events == (first,)
    second = event(2, digest=DIGEST_B)
    events = append_event(events, second)
    assert [item.event_seq for item in events] == [1, 2]

    with pytest.raises(OutboxContractError, match="contiguous"):
        append_event(events, event(4))
    with pytest.raises(OutboxContractError, match="duplicate"):
        append_event(events, event(2, digest=DIGEST_B))

    corrupt = event(1)
    object.__setattr__(corrupt, "payload_digest", DIGEST_B)
    with pytest.raises(OutboxContractError, match="snapshot"):
        append_event((corrupt,), event(2, digest=DIGEST_B))


def test_head_of_line_never_returns_sequence_n_plus_one() -> None:
    events = append_event((), event(1))
    events = append_event(events, event(2, digest=DIGEST_B))
    assert next_publishable(events, task_id="task-01", published_seq=0) is events[0]
    claimed = claim_event(
        events[0],
        owner_instance_id="relay-01",
        fencing_token=7,
        claim_token=CLAIM_ID,
        lease_until_ms=2_000,
        now_ms=1_000,
    )
    blocked = (claimed, events[1])
    assert next_publishable(blocked, task_id="task-01", published_seq=0) is None
    with pytest.raises(OutboxContractError, match="watermark"):
        next_publishable(blocked, task_id="task-01", published_seq=1)
    published = mark_published(
        claimed,
        owner_instance_id="relay-01",
        fencing_token=7,
        claim_token=CLAIM_ID,
        now_ms=1_500,
    )
    assert next_publishable((published, events[1]), task_id="task-01", published_seq=1) is events[1]


def test_claim_and_publish_require_owner_and_fence_and_are_idempotent() -> None:
    pending = event(1)
    claimed = claim_event(
        pending,
        owner_instance_id="relay-01",
        fencing_token=7,
        claim_token=CLAIM_ID,
        lease_until_ms=2_000,
        now_ms=1_000,
    )
    with pytest.raises(OutboxContractError, match="owner"):
        mark_published(
            claimed,
            owner_instance_id="other",
            fencing_token=7,
            claim_token=CLAIM_ID,
            now_ms=1_500,
        )
    published = mark_published(
        claimed,
        owner_instance_id="relay-01",
        fencing_token=7,
        claim_token=CLAIM_ID,
        now_ms=1_500,
    )
    assert published.state is OutboxState.PUBLISHED
    assert mark_published(
        published,
        owner_instance_id="relay-01",
        fencing_token=7,
        claim_token=CLAIM_ID,
        now_ms=9_999,
    ) is published
    with pytest.raises(OutboxContractError, match="token"):
        mark_published(
            published,
            owner_instance_id="relay-01",
            fencing_token=7,
            claim_token=WRONG_CLAIM_ID,
            now_ms=9_999,
        )


def test_wrong_runtime_objects_fail_with_outbox_contract_error() -> None:
    with pytest.raises(OutboxContractError):
        next_publishable((object(),), task_id="task-01", published_seq=0)
    with pytest.raises(OutboxContractError):
        claim_event(
            object(),  # type: ignore[arg-type]
            owner_instance_id="relay-01",
            fencing_token=7,
            claim_token=CLAIM_ID,
            lease_until_ms=2_000,
            now_ms=1_000,
        )


def test_event_rejects_bool_float_and_invalid_digest() -> None:
    with pytest.raises(OutboxContractError):
        create_outbox_event(
            task_id="task-01",
            event_seq=True,
            task_version=1,
            event_type="TaskStatusChanged",
            payload_digest=DIGEST_A,
        )
    with pytest.raises(OutboxContractError):
        create_outbox_event(
            task_id="task-01",
            event_seq=1,
            task_version=1.0,
            event_type="TaskStatusChanged",
            payload_digest=DIGEST_A,
        )
    with pytest.raises(OutboxContractError):
        create_outbox_event(
            task_id="task-01",
            event_seq=1,
            task_version=1,
            event_type="TaskStatusChanged",
            payload_digest="not-a-digest",
        )


class ExplodingText(str):
    def __format__(self, spec: str) -> str:
        del spec
        raise RuntimeError("format must not run")


class ExplodingInt(int):
    def __format__(self, spec: str) -> str:
        del spec
        raise RuntimeError("format must not run")


def test_factory_gates_types_before_event_id_formatting() -> None:
    with pytest.raises(OutboxContractError):
        create_outbox_event(
            task_id=ExplodingText("task-01"),  # type: ignore[arg-type]
            event_seq=1,
            task_version=1,
            event_type="TaskStatusChanged",
            payload_digest=DIGEST_A,
        )
    with pytest.raises(OutboxContractError):
        create_outbox_event(
            task_id="task-01",
            event_seq=ExplodingInt(1),  # type: ignore[arg-type]
            task_version=1,
            event_type="TaskStatusChanged",
            payload_digest=DIGEST_A,
        )
