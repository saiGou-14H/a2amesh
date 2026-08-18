"""Pure C2 Task aggregate and idempotent claim contracts.

This module is deliberately transport- and storage-independent. Redis/Lua,
leases, dispatch intents, and outbox publication are separate C2 modules.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

import rfc8785

from a2amesh import protocol

_MAX_JSON_SAFE_INTEGER = 9_007_199_254_740_991
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]+$")


class TaskContractError(ValueError):
    """A pure Task aggregate or claim contract is invalid."""


class TaskClaimOutcome(StrEnum):
    CREATED = "created"
    REPLAY = "replay"
    CONFLICT = "conflict"


def _require_text(value: object, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise TaskContractError(f"{label} must be a non-empty plain string")
    if not _SAFE_TEXT.fullmatch(value):
        raise TaskContractError(f"{label} contains a forbidden control character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TaskContractError(f"{label} is not valid UTF-8 text") from exc
    return value


def _require_digest(value: object, label: str) -> str:
    text = _require_text(value, label)
    if not _SHA256.fullmatch(text):
        raise TaskContractError(f"{label} must be lowercase SHA-256 hex")
    return text


def _require_integer(value: object, label: str, *, minimum: int) -> int:
    if type(value) is not int or not minimum <= value <= _MAX_JSON_SAFE_INTEGER:
        raise TaskContractError(
            f"{label} must be an integer between {minimum} and {_MAX_JSON_SAFE_INTEGER}"
        )
    return value


def _task_json(task: protocol.Task) -> dict[str, Any]:
    return protocol.to_protojson_dict(task)


@dataclass(frozen=True, slots=True)
class TaskClaimKey:
    """Stable caller/target/message identity plus the request digest."""

    principal_id: str
    target_agent_id: str
    message_id: str
    request_digest: str

    def __post_init__(self) -> None:
        _require_text(self.principal_id, "principal_id")
        _require_text(self.target_agent_id, "target_agent_id")
        _require_text(self.message_id, "message_id")
        _require_digest(self.request_digest, "request_digest")

    def identity_dict(self) -> dict[str, str]:
        return {
            "principalId": self.principal_id,
            "targetAgentId": self.target_agent_id,
            "messageId": self.message_id,
        }

    def to_dict(self) -> dict[str, str]:
        return self.identity_dict() | {"requestDigest": self.request_digest}

    def canonical_bytes(self) -> bytes:
        self.assert_integrity()
        return rfc8785.dumps(self.to_dict())

    def assert_integrity(self) -> None:
        _require_text(self.principal_id, "principal_id")
        _require_text(self.target_agent_id, "target_agent_id")
        _require_text(self.message_id, "message_id")
        _require_digest(self.request_digest, "request_digest")

    @property
    def idempotency_digest(self) -> str:
        self.assert_integrity()
        return hashlib.sha256(rfc8785.dumps(self.identity_dict())).hexdigest()


@dataclass(frozen=True, slots=True)
class TaskAggregate:
    """Immutable-in-contract official Task snapshot and monotonic metadata."""

    task: protocol.Task
    claim_key: TaskClaimKey
    command_digest: str
    task_version: int = 1
    event_seq: int = 0
    _snapshot_digest: str = field(default="", repr=False, compare=True)

    @classmethod
    def create(
        cls,
        *,
        task: protocol.Task,
        claim_key: TaskClaimKey,
        command_digest: str,
    ) -> TaskAggregate:
        return cls(
            task=task,
            claim_key=claim_key,
            command_digest=command_digest,
        )

    def __post_init__(self) -> None:
        self._assert_semantics()
        snapshot = protocol.Task()
        snapshot.CopyFrom(self.task)
        object.__setattr__(self, "task", snapshot)
        digest = self._compute_snapshot_digest()
        if type(self._snapshot_digest) is not str:
            raise TaskContractError("task aggregate snapshot digest must be plain text")
        if self._snapshot_digest != "":
            if self._snapshot_digest != digest:
                raise TaskContractError("task aggregate snapshot digest mismatch")
        else:
            object.__setattr__(self, "_snapshot_digest", digest)

    def _assert_semantics(self) -> None:
        if type(self.task) is not protocol.Task:
            raise TaskContractError("task must be the official protocol.Task type")
        if type(self.claim_key) is not TaskClaimKey:
            raise TaskContractError("claim_key must be TaskClaimKey")
        self.claim_key.assert_integrity()
        if type(self.task_version) is not int or not (
            1 <= self.task_version <= _MAX_JSON_SAFE_INTEGER
        ):
            raise TaskContractError("task_version must be a positive JSON-safe integer")
        if type(self.event_seq) is not int or not (
            0 <= self.event_seq <= _MAX_JSON_SAFE_INTEGER
        ):
            raise TaskContractError("event_seq must be a non-negative JSON-safe integer")
        _require_digest(self.command_digest, "command_digest")
        _require_text(self.task.id, "task.id")
        _require_text(self.task.context_id, "task.context_id")
        if type(self.task.status.state) is not int:
            raise TaskContractError("task.status.state must be an official integer state")
        try:
            protocol.legal_task_state_transitions(self.task.status.state)
        except Exception as exc:
            raise TaskContractError("task.status.state is not a known TaskState") from exc


    def _payload(self) -> dict[str, Any]:
        return {
            "task": _task_json(self.task),
            "claim": self.claim_key.to_dict(),
            "commandDigest": self.command_digest,
            "taskVersion": self.task_version,
            "eventSeq": self.event_seq,
        }

    def _compute_snapshot_digest(self) -> str:
        return hashlib.sha256(rfc8785.dumps(self._payload())).hexdigest()

    def assert_integrity(self) -> None:
        self._assert_semantics()
        if self._compute_snapshot_digest() != self._snapshot_digest:
            raise TaskContractError("task aggregate snapshot digest mismatch")

    def canonical_bytes(self) -> bytes:
        self.assert_integrity()
        return rfc8785.dumps(self._payload())

    def aggregate_digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def transition(self, target_state: int) -> TaskAggregate:
        self.assert_integrity()
        try:
            protocol.validate_task_state_transition(self.task.status.state, target_state)
        except Exception as exc:
            raise TaskContractError("task state transition is invalid") from exc
        next_task = protocol.Task()
        next_task.CopyFrom(self.task)
        next_task.status.state = target_state
        return replace(
            self,
            task=next_task,
            task_version=self.task_version + 1,
            event_seq=self.event_seq + 1,
            _snapshot_digest="",
        )


@dataclass(frozen=True, slots=True)
class TaskClaimDecision:
    outcome: TaskClaimOutcome
    aggregate: TaskAggregate


def evaluate_claim(
    existing: TaskAggregate | None,
    requested: TaskAggregate,
) -> TaskClaimDecision:
    """Evaluate an idempotent claim without performing any external write."""
    if type(requested) is not TaskAggregate:
        raise TaskContractError("requested claim must be TaskAggregate")
    requested.assert_integrity()
    if existing is None:
        if requested.task.status.state != protocol.TaskState.TASK_STATE_SUBMITTED:
            raise TaskContractError("CREATED claim must start from SUBMITTED TaskState")
        if requested.task_version != 1 or requested.event_seq != 0:
            raise TaskContractError("CREATED claim must start at taskVersion=1/eventSeq=0")
        return TaskClaimDecision(TaskClaimOutcome.CREATED, requested)
    if type(existing) is not TaskAggregate:
        raise TaskContractError("existing claim must be TaskAggregate or None")
    existing.assert_integrity()
    same_identity = existing.claim_key.idempotency_digest == requested.claim_key.idempotency_digest
    same_request = existing.claim_key.request_digest == requested.claim_key.request_digest
    same_command = existing.command_digest == requested.command_digest
    if same_identity and same_request and same_command:
        return TaskClaimDecision(TaskClaimOutcome.REPLAY, existing)
    return TaskClaimDecision(TaskClaimOutcome.CONFLICT, existing)
