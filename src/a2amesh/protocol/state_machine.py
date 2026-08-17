"""Pure official A2A TaskState transition contracts.

This module contains only protocol semantics. Version, fencing, persistence and
outbox atomicity remain State Service responsibilities in C2.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from a2amesh.protocol.errors import InvalidParamsError
from a2amesh.protocol.types import TaskState

TERMINAL_TASK_STATES: Final[frozenset[int]] = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)

_KNOWN_TASK_STATES: Final[frozenset[int]] = frozenset(
    {
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    }
)

TASK_STATE_TRANSITIONS: Final[Mapping[int, frozenset[int]]] = MappingProxyType(
    {
        TaskState.TASK_STATE_SUBMITTED: frozenset(
            {
                TaskState.TASK_STATE_WORKING,
                TaskState.TASK_STATE_CANCELED,
                TaskState.TASK_STATE_FAILED,
                TaskState.TASK_STATE_REJECTED,
            }
        ),
        TaskState.TASK_STATE_WORKING: frozenset(
            {
                TaskState.TASK_STATE_INPUT_REQUIRED,
                TaskState.TASK_STATE_AUTH_REQUIRED,
                TaskState.TASK_STATE_COMPLETED,
                TaskState.TASK_STATE_FAILED,
                TaskState.TASK_STATE_CANCELED,
                TaskState.TASK_STATE_REJECTED,
            }
        ),
        TaskState.TASK_STATE_INPUT_REQUIRED: frozenset(
            {
                TaskState.TASK_STATE_WORKING,
                TaskState.TASK_STATE_COMPLETED,
                TaskState.TASK_STATE_FAILED,
                TaskState.TASK_STATE_CANCELED,
                TaskState.TASK_STATE_REJECTED,
            }
        ),
        TaskState.TASK_STATE_AUTH_REQUIRED: frozenset(
            {
                TaskState.TASK_STATE_WORKING,
                TaskState.TASK_STATE_COMPLETED,
                TaskState.TASK_STATE_FAILED,
                TaskState.TASK_STATE_CANCELED,
                TaskState.TASK_STATE_REJECTED,
            }
        ),
        TaskState.TASK_STATE_COMPLETED: frozenset(),
        TaskState.TASK_STATE_FAILED: frozenset(),
        TaskState.TASK_STATE_CANCELED: frozenset(),
        TaskState.TASK_STATE_REJECTED: frozenset(),
    }
)


def is_terminal_task_state(state: int) -> bool:
    """Return whether an official TaskState is one of the four terminal states."""
    return type(state) is int and state in TERMINAL_TASK_STATES


def _validate_known_state(state: int, label: str) -> None:
    if type(state) is not int or state not in _KNOWN_TASK_STATES:
        raise InvalidParamsError(message=f"{label} TaskState is invalid or unspecified")


def legal_task_state_transitions(state: int) -> frozenset[int]:
    """Return the immutable set of legal next states for an official state."""
    _validate_known_state(state, "current")
    return TASK_STATE_TRANSITIONS[state]


def validate_task_state_transition(current: int, target: int) -> None:
    """Raise the official invalid-params error when a transition is not legal."""
    _validate_known_state(current, "current")
    _validate_known_state(target, "target")
    if target not in TASK_STATE_TRANSITIONS[current]:
        raise InvalidParamsError(
            message=f"TaskState transition {current!r} -> {target!r} is not permitted"
        )
