"""C1 contracts for the official A2A TaskState transition table."""

from __future__ import annotations

import pytest
from a2a.utils.errors import InvalidParamsError

from a2amesh import protocol
from a2amesh.protocol.state_machine import (
    TERMINAL_TASK_STATES,
    is_terminal_task_state,
    legal_task_state_transitions,
    validate_task_state_transition,
)


@pytest.mark.parametrize(
    "state",
    [
        protocol.TaskState.TASK_STATE_COMPLETED,
        protocol.TaskState.TASK_STATE_FAILED,
        protocol.TaskState.TASK_STATE_CANCELED,
        protocol.TaskState.TASK_STATE_REJECTED,
    ],
)
def test_official_terminal_states_are_closed_set(state: int) -> None:
    assert protocol.is_terminal_task_state is is_terminal_task_state
    assert is_terminal_task_state(state)
    assert state in TERMINAL_TASK_STATES


@pytest.mark.parametrize(
    "state",
    [
        protocol.TaskState.TASK_STATE_UNSPECIFIED,
        protocol.TaskState.TASK_STATE_SUBMITTED,
        protocol.TaskState.TASK_STATE_WORKING,
        protocol.TaskState.TASK_STATE_INPUT_REQUIRED,
        protocol.TaskState.TASK_STATE_AUTH_REQUIRED,
    ],
)
def test_nonterminal_official_states_are_not_terminal(state: int) -> None:
    assert not is_terminal_task_state(state)
    assert state not in TERMINAL_TASK_STATES


@pytest.mark.parametrize("value", [True, 3.0, 99, "TASK_STATE_COMPLETED"])
def test_terminal_predicate_rejects_non_enum_values(value: object) -> None:
    assert not is_terminal_task_state(value)  # type: ignore[arg-type]


def test_official_transition_matrix_matches_design() -> None:
    s = protocol.TaskState
    assert legal_task_state_transitions(s.TASK_STATE_SUBMITTED) == frozenset(
        {
            s.TASK_STATE_WORKING,
            s.TASK_STATE_CANCELED,
            s.TASK_STATE_FAILED,
            s.TASK_STATE_REJECTED,
        }
    )
    assert legal_task_state_transitions(s.TASK_STATE_WORKING) == frozenset(
        {
            s.TASK_STATE_INPUT_REQUIRED,
            s.TASK_STATE_AUTH_REQUIRED,
            s.TASK_STATE_COMPLETED,
            s.TASK_STATE_FAILED,
            s.TASK_STATE_CANCELED,
            s.TASK_STATE_REJECTED,
        }
    )
    for waiting in (s.TASK_STATE_INPUT_REQUIRED, s.TASK_STATE_AUTH_REQUIRED):
        assert legal_task_state_transitions(waiting) == frozenset(
            {
                s.TASK_STATE_WORKING,
                s.TASK_STATE_COMPLETED,
                s.TASK_STATE_FAILED,
                s.TASK_STATE_CANCELED,
                s.TASK_STATE_REJECTED,
            }
        )
    for terminal in TERMINAL_TASK_STATES:
        assert legal_task_state_transitions(terminal) == frozenset()


@pytest.mark.parametrize(
    "current, target",
    [
        (protocol.TaskState.TASK_STATE_SUBMITTED, protocol.TaskState.TASK_STATE_COMPLETED),
        (protocol.TaskState.TASK_STATE_WORKING, protocol.TaskState.TASK_STATE_SUBMITTED),
        (protocol.TaskState.TASK_STATE_COMPLETED, protocol.TaskState.TASK_STATE_WORKING),
        (protocol.TaskState.TASK_STATE_FAILED, protocol.TaskState.TASK_STATE_FAILED),
        (protocol.TaskState.TASK_STATE_UNSPECIFIED, protocol.TaskState.TASK_STATE_WORKING),
    ],
)
def test_illegal_or_unspecified_transition_fails_closed(current: int, target: int) -> None:
    with pytest.raises(InvalidParamsError, match="TaskState"):
        validate_task_state_transition(current, target)
