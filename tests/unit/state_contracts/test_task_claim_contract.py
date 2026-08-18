from __future__ import annotations

from dataclasses import replace

import pytest

from a2amesh import protocol, state_contracts
from a2amesh.state_contracts.task import (
    TaskAggregate,
    TaskClaimKey,
    TaskClaimOutcome,
    TaskContractError,
    evaluate_claim,
)

COMMAND_DIGEST = "11" * 32
REQUEST_DIGEST = "22" * 32


def submitted_task(task_id: str = "task-01") -> protocol.Task:
    return protocol.Task(
        id=task_id,
        context_id="ctx-01",
        status=protocol.TaskStatus(state=protocol.TaskState.TASK_STATE_SUBMITTED),
    )


def claim_key(request_digest: str = REQUEST_DIGEST) -> TaskClaimKey:
    return TaskClaimKey(
        principal_id="agent:caller",
        target_agent_id="worker-a",
        message_id="message-01",
        request_digest=request_digest,
    )


def aggregate(
    *, task: protocol.Task | None = None, key: TaskClaimKey | None = None
) -> TaskAggregate:
    return TaskAggregate.create(
        task=task or submitted_task(),
        claim_key=key or claim_key(),
        command_digest=COMMAND_DIGEST,
    )


def test_invalid_task_states_are_rejected_at_aggregate_boundary() -> None:
    for state in (0, 99):
        invalid = submitted_task()
        invalid.status.state = state
        with pytest.raises(TaskContractError, match="TaskState"):
            TaskAggregate.create(
                task=invalid,
                claim_key=claim_key(),
                command_digest=COMMAND_DIGEST,
            )


def test_task_contract_facade_exports_the_same_types() -> None:
    assert state_contracts.TaskAggregate is TaskAggregate
    assert state_contracts.TaskClaimKey is TaskClaimKey
    assert state_contracts.evaluate_claim is evaluate_claim


def test_claim_key_is_deterministic_and_rejects_bool_or_subclass_inputs() -> None:
    first = claim_key()
    second = claim_key()
    assert first.idempotency_digest == second.idempotency_digest
    assert first.canonical_bytes() == second.canonical_bytes()

    with pytest.raises(TaskContractError):
        TaskClaimKey(
            principal_id="agent:caller",
            target_agent_id="worker-a",
            message_id="message-01",
            request_digest=True,  # type: ignore[arg-type]
        )


class MutableText(str):
    pass


class ExplodingBool:
    def __bool__(self) -> bool:
        raise RuntimeError("snapshot truthiness must not run")


def test_aggregate_owns_a_task_snapshot_and_stable_digest() -> None:
    source = submitted_task()
    value = aggregate(task=source)
    original = value.task.id
    source.id = "mutated-source"
    assert value.task.id == original
    assert value.aggregate_digest() == value.aggregate_digest()

    value.task.id = "mutated-owned"
    with pytest.raises(TaskContractError, match="snapshot"):
        value.assert_integrity()

    with pytest.raises(TaskContractError):
        TaskClaimKey(
            principal_id=MutableText("agent:caller"),
            target_agent_id="worker-a",
            message_id="message-01",
            request_digest=REQUEST_DIGEST,
        )


def test_task_semantics_are_not_bypassable_by_self_rehashed_mutation() -> None:
    bad_task = aggregate()
    bad_task.task.id = ""
    object.__setattr__(bad_task, "_snapshot_digest", bad_task._compute_snapshot_digest())
    with pytest.raises(TaskContractError, match="task.id"):
        bad_task.assert_integrity()
    with pytest.raises(TaskContractError, match="task.id"):
        evaluate_claim(None, bad_task)

    bad_key = aggregate()
    object.__setattr__(bad_key.claim_key, "request_digest", "not-a-digest")
    object.__setattr__(bad_key, "_snapshot_digest", bad_key._compute_snapshot_digest())
    with pytest.raises(TaskContractError, match="request_digest"):
        bad_key.assert_integrity()


def test_same_claim_and_same_command_is_idempotent_but_digest_conflict_is_rejected() -> None:
    existing = aggregate()
    same = aggregate()
    decision = evaluate_claim(existing, same)
    assert decision.outcome is TaskClaimOutcome.REPLAY
    assert decision.aggregate is existing

    different_request = aggregate(key=claim_key("33" * 32))
    conflict = evaluate_claim(existing, different_request)
    assert conflict.outcome is TaskClaimOutcome.CONFLICT
    assert conflict.aggregate is existing


def test_created_claim_requires_submitted_state_and_initial_counters() -> None:
    working = aggregate().transition(protocol.TaskState.TASK_STATE_WORKING)
    with pytest.raises(TaskContractError, match="CREATED"):
        evaluate_claim(None, working)
    with pytest.raises(TaskContractError, match="CREATED"):
        evaluate_claim(None, replace(aggregate(), task_version=2, _snapshot_digest=""))


def test_task_text_and_snapshot_sentinel_are_bounded() -> None:
    with pytest.raises(TaskContractError, match="UTF-8"):
        claim_key(request_digest="\ud800" + "1" * 63)
    with pytest.raises(TaskContractError, match="snapshot digest"):
        TaskAggregate(
            task=submitted_task(),
            claim_key=claim_key(),
            command_digest=COMMAND_DIGEST,
            _snapshot_digest=ExplodingBool(),  # type: ignore[arg-type]
        )


def test_empty_claim_creates_and_transition_advances_version_and_event() -> None:
    requested = aggregate()
    decision = evaluate_claim(None, requested)
    assert decision.outcome is TaskClaimOutcome.CREATED
    assert decision.aggregate is requested
    assert requested.task_version == 1
    assert requested.event_seq == 0

    advanced = requested.transition(protocol.TaskState.TASK_STATE_WORKING)
    assert advanced.task.status.state == protocol.TaskState.TASK_STATE_WORKING
    assert advanced.task_version == 2
    assert advanced.event_seq == 1
    assert requested.task.status.state == protocol.TaskState.TASK_STATE_SUBMITTED


def test_terminal_task_cannot_transition_back_or_skip_version() -> None:
    working = aggregate().transition(protocol.TaskState.TASK_STATE_WORKING)
    completed = working.transition(protocol.TaskState.TASK_STATE_COMPLETED)
    with pytest.raises(TaskContractError, match="transition"):
        completed.transition(protocol.TaskState.TASK_STATE_WORKING)
    with pytest.raises(TaskContractError, match="version"):
        replace(completed, task_version=0).assert_integrity()


class ExplodingDigest:
    def __ne__(self, other: object) -> bool:
        del other
        raise RuntimeError("digest comparison must be bounded")


def test_corrupt_snapshot_digest_comparison_is_bounded() -> None:
    corrupt = aggregate()
    object.__setattr__(corrupt, "_snapshot_digest", ExplodingDigest())
    with pytest.raises(TaskContractError, match="snapshot digest"):
        evaluate_claim(None, corrupt)


def test_task_type_hints_resolve_lazy_protocol_annotations() -> None:
    from typing import get_type_hints

    hints = get_type_hints(TaskAggregate)
    assert hints["task"] is protocol.Task
