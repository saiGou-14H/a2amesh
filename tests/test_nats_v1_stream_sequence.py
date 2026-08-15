"""Stateful sequence gates for committed NATS stream-session frames."""

from __future__ import annotations

from dataclasses import replace

import pytest
from a2a import types as official

from a2amesh import protocol
from a2amesh.bindings.nats_v1 import (
    StreamFrameCursorV1,
    StreamFrameDisposition,
    StreamSessionFrameV1,
)
from a2amesh.bindings.nats_v1.envelope import BindingValidationError

SESSION_ID = "session-01H-sequence"
OPEN_ID = "stream-open-01H-sequence"
TASK_ID = "task-01H-sequence"
CONTEXT_ID = "ctx-01H-sequence"


def status_frame(*, sequence: int, event_seq: int, state: int, final: bool) -> StreamSessionFrameV1:
    response = protocol.StreamResponse(
        status_update=protocol.TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=protocol.TaskStatus(state=state),
        )
    )
    return StreamSessionFrameV1.create(
        stream_session_id=SESSION_ID,
        stream_open_id=OPEN_ID,
        sequence=sequence,
        event_seq=event_seq,
        final=final,
        canonical_stream_response=response,
    )


def artifact_frame(*, sequence: int, event_seq: int, final: bool = False) -> StreamSessionFrameV1:
    response = protocol.StreamResponse(
        artifact_update=protocol.TaskArtifactUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            artifact=official.Artifact(artifact_id="artifact-01H-sequence"),
            append=False,
            last_chunk=True,
        )
    )
    return StreamSessionFrameV1.create(
        stream_session_id=SESSION_ID,
        stream_open_id=OPEN_ID,
        sequence=sequence,
        event_seq=event_seq,
        final=final,
        canonical_stream_response=response,
    )


def new_cursor() -> StreamFrameCursorV1:
    return StreamFrameCursorV1(
        stream_session_id=SESSION_ID,
        stream_open_id=OPEN_ID,
        task_id=TASK_ID,
        snapshot_event_seq=7,
    )


def test_live_frames_advance_strictly_and_current_frame_redelivers_idempotently() -> None:
    cursor = new_cursor()
    assert cursor.last_sequence == 0
    assert cursor.last_event_seq == 7

    first = status_frame(
        sequence=1,
        event_seq=8,
        state=protocol.TaskState.TASK_STATE_WORKING,
        final=False,
    )
    cursor, disposition = cursor.accept(first)
    assert disposition is StreamFrameDisposition.NEW
    assert cursor.last_sequence == 1
    assert cursor.last_event_seq == 8

    same_cursor, disposition = cursor.accept(first)
    assert disposition is StreamFrameDisposition.REDELIVERY
    assert same_cursor is cursor

    second = artifact_frame(sequence=2, event_seq=10)
    cursor, disposition = cursor.accept(second)
    assert disposition is StreamFrameDisposition.NEW
    assert cursor.last_sequence == 2
    assert cursor.last_event_seq == 10


def test_final_frame_allows_only_exact_redelivery_and_seals_sequence() -> None:
    cursor, _ = new_cursor().accept(
        status_frame(
            sequence=1,
            event_seq=8,
            state=protocol.TaskState.TASK_STATE_WORKING,
            final=False,
        )
    )
    final_frame = status_frame(
        sequence=2,
        event_seq=9,
        state=protocol.TaskState.TASK_STATE_COMPLETED,
        final=True,
    )
    cursor, disposition = cursor.accept(final_frame)
    assert disposition is StreamFrameDisposition.NEW
    assert cursor.final is True

    same_cursor, disposition = cursor.accept(final_frame)
    assert disposition is StreamFrameDisposition.REDELIVERY
    assert same_cursor is cursor

    with pytest.raises(BindingValidationError, match="after final"):
        cursor.accept(
            status_frame(
                sequence=3,
                event_seq=10,
                state=protocol.TaskState.TASK_STATE_COMPLETED,
                final=True,
            )
        )


def test_sequence_event_and_session_identity_fail_closed() -> None:
    cursor = new_cursor()
    valid = status_frame(
        sequence=1,
        event_seq=8,
        state=protocol.TaskState.TASK_STATE_WORKING,
        final=False,
    )

    with pytest.raises(BindingValidationError, match="exactly one"):
        cursor.accept(
            status_frame(
                sequence=2,
                event_seq=8,
                state=protocol.TaskState.TASK_STATE_WORKING,
                final=False,
            )
        )
    with pytest.raises(BindingValidationError, match="snapshotEventSeq"):
        cursor.accept(
            status_frame(
                sequence=1,
                event_seq=7,
                state=protocol.TaskState.TASK_STATE_WORKING,
                final=False,
            )
        )
    with pytest.raises(BindingValidationError, match="streamSessionId mismatch"):
        cursor.accept(
            StreamSessionFrameV1.create(
                stream_session_id="other-session",
                stream_open_id=OPEN_ID,
                sequence=1,
                event_seq=8,
                final=False,
                canonical_stream_response=valid.canonical_stream_response,
            )
        )
    with pytest.raises(BindingValidationError, match="streamOpenId mismatch"):
        cursor.accept(
            StreamSessionFrameV1.create(
                stream_session_id=SESSION_ID,
                stream_open_id="other-open",
                sequence=1,
                event_seq=8,
                final=False,
                canonical_stream_response=valid.canonical_stream_response,
            )
        )


def test_event_seq_must_increase_after_each_accepted_frame() -> None:
    cursor, _ = new_cursor().accept(
        status_frame(
            sequence=1,
            event_seq=10,
            state=protocol.TaskState.TASK_STATE_WORKING,
            final=False,
        )
    )
    with pytest.raises(BindingValidationError, match="strictly increase"):
        cursor.accept(artifact_frame(sequence=2, event_seq=9))
    with pytest.raises(BindingValidationError, match="strictly increase"):
        cursor.accept(artifact_frame(sequence=2, event_seq=10))


def test_live_payload_kind_task_identity_and_final_state_are_enforced() -> None:
    cursor = new_cursor()
    with pytest.raises(BindingValidationError, match="statusUpdate or artifactUpdate"):
        cursor.accept(
            StreamSessionFrameV1.create(
                stream_session_id=SESSION_ID,
                stream_open_id=OPEN_ID,
                sequence=1,
                event_seq=8,
                final=False,
                canonical_stream_response=protocol.StreamResponse(
                    task=protocol.Task(
                        id=TASK_ID,
                        context_id=CONTEXT_ID,
                        status=protocol.TaskStatus(
                            state=protocol.TaskState.TASK_STATE_WORKING
                        ),
                    )
                ),
            )
        )

    wrong_task_response = protocol.StreamResponse(
        status_update=protocol.TaskStatusUpdateEvent(
            task_id="other-task",
            context_id=CONTEXT_ID,
            status=protocol.TaskStatus(state=protocol.TaskState.TASK_STATE_WORKING),
        )
    )
    with pytest.raises(BindingValidationError, match="Task ID mismatch"):
        cursor.accept(
            StreamSessionFrameV1.create(
                stream_session_id=SESSION_ID,
                stream_open_id=OPEN_ID,
                sequence=1,
                event_seq=8,
                final=False,
                canonical_stream_response=wrong_task_response,
            )
        )

    with pytest.raises(BindingValidationError, match="final does not match"):
        cursor.accept(
            status_frame(
                sequence=1,
                event_seq=8,
                state=protocol.TaskState.TASK_STATE_WORKING,
                final=True,
            )
        )
    with pytest.raises(BindingValidationError, match="final does not match"):
        cursor.accept(
            status_frame(
                sequence=1,
                event_seq=8,
                state=protocol.TaskState.TASK_STATE_COMPLETED,
                final=False,
            )
        )
    with pytest.raises(BindingValidationError, match="final does not match"):
        cursor.accept(artifact_frame(sequence=1, event_seq=8, final=True))


def test_cursor_invariants_reject_partial_or_boolean_progress() -> None:
    with pytest.raises(BindingValidationError, match="snapshotEventSeq"):
        replace(new_cursor(), snapshot_event_seq=False)
    with pytest.raises(BindingValidationError, match="lastSequence"):
        replace(new_cursor(), last_sequence=False)
    with pytest.raises(BindingValidationError, match="payload digest"):
        replace(new_cursor(), last_sequence=1, last_event_seq=8)
    with pytest.raises(BindingValidationError, match="empty cursor"):
        replace(new_cursor(), final=True)
