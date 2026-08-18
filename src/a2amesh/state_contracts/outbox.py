"""Pure outbox/event-sequence contracts for C2."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

import rfc8785

_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]+$")


class OutboxContractError(ValueError):
    """An outbox ordering, claim, or publication contract is invalid."""


class OutboxState(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PUBLISHED = "PUBLISHED"
    DEAD = "DEAD"


def _text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or value != value.strip():
        raise OutboxContractError(f"{label} must be a plain string")
    if not value and not allow_empty:
        raise OutboxContractError(f"{label} must be a plain string")
    if value and not _SAFE_TEXT.fullmatch(value):
        raise OutboxContractError(f"{label} contains a forbidden control character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise OutboxContractError(f"{label} is not valid UTF-8") from exc
    return value


def _digest(value: object, label: str) -> str:
    value = _text(value, label)
    if not _SHA256.fullmatch(value):
        raise OutboxContractError(f"{label} must be lowercase SHA-256 hex")
    return value


def _integer(value: object, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= _MAX_SAFE_INTEGER:
        raise OutboxContractError(f"{label} must be a JSON-safe integer")
    return value


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    event_id: str
    task_id: str
    event_seq: int
    task_version: int
    event_type: str
    payload_digest: str
    state: OutboxState = OutboxState.PENDING
    owner_instance_id: str = ""
    fencing_token: int = 0
    claim_token: str = ""
    lease_until_ms: int = 0
    _snapshot_digest: str = field(default="", repr=False, compare=True)

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        _text(self.task_id, "task_id")
        _integer(self.event_seq, "event_seq", minimum=1)
        _integer(self.task_version, "task_version", minimum=1)
        _text(self.event_type, "event_type")
        _digest(self.payload_digest, "payload_digest")
        if type(self.state) is not OutboxState:
            raise OutboxContractError("state must be OutboxState")
        _text(self.owner_instance_id, "owner_instance_id", allow_empty=True)
        _text(self.claim_token, "claim_token", allow_empty=True)
        _integer(self.fencing_token, "fencing_token")
        _integer(self.lease_until_ms, "lease_until_ms")
        if self.event_id != f"{self.task_id}:{self.event_seq}":
            raise OutboxContractError("event_id must be task_id:event_seq")
        if self.state is OutboxState.PENDING:
            if (
                self.owner_instance_id
                or self.claim_token
                or self.fencing_token
                or self.lease_until_ms
            ):
                raise OutboxContractError("pending event cannot have a claim")
        else:
            _text(self.owner_instance_id, "owner_instance_id")
            _text(self.claim_token, "claim_token")
            _integer(self.fencing_token, "fencing_token", minimum=1)
            _integer(self.lease_until_ms, "lease_until_ms", minimum=1)
        digest = self._compute_digest()
        if type(self._snapshot_digest) is not str:
            raise OutboxContractError("snapshot digest must be plain text")
        if self._snapshot_digest != "":
            if self._snapshot_digest != digest:
                raise OutboxContractError("outbox snapshot digest mismatch")
        else:
            object.__setattr__(self, "_snapshot_digest", digest)

    def _payload(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "taskId": self.task_id,
            "eventSeq": self.event_seq,
            "taskVersion": self.task_version,
            "eventType": self.event_type,
            "payloadDigest": self.payload_digest,
            "state": self.state.value,
            "ownerInstanceId": self.owner_instance_id,
            "fencingToken": self.fencing_token,
            "claimToken": self.claim_token,
            "leaseUntilMs": self.lease_until_ms,
        }

    def _compute_digest(self) -> str:
        return hashlib.sha256(rfc8785.dumps(self._payload())).hexdigest()

    def assert_integrity(self) -> None:
        if self._compute_digest() != self._snapshot_digest:
            raise OutboxContractError("outbox snapshot digest mismatch")

    def canonical_bytes(self) -> bytes:
        self.assert_integrity()
        return rfc8785.dumps(self._payload())

    @property
    def event_digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def create_outbox_event(
    *,
    task_id: str,
    event_seq: int,
    task_version: int,
    event_type: str,
    payload_digest: str,
) -> OutboxEvent:
    return OutboxEvent(
        event_id=f"{task_id}:{event_seq}",
        task_id=task_id,
        event_seq=event_seq,
        task_version=task_version,
        event_type=event_type,
        payload_digest=payload_digest,
    )


def append_event(events: Iterable[OutboxEvent], event: OutboxEvent) -> tuple[OutboxEvent, ...]:
    existing = tuple(events)
    if type(event) is not OutboxEvent:
        raise OutboxContractError("event must be OutboxEvent")
    event.assert_integrity()
    if any(type(item) is not OutboxEvent for item in existing):
        raise OutboxContractError("existing outbox entries must be OutboxEvent")
    if any(item.event_id == event.event_id for item in existing):
        raise OutboxContractError("duplicate outbox event")
    task_events = tuple(item for item in existing if item.task_id == event.task_id)
    if task_events:
        expected = max(item.event_seq for item in task_events) + 1
        if event.event_seq != expected:
            raise OutboxContractError("outbox event sequence must be contiguous")
    return existing + (event,)


def next_publishable(
    events: Iterable[OutboxEvent], *, task_id: str, published_seq: int
) -> OutboxEvent | None:
    _text(task_id, "task_id")
    _integer(published_seq, "published_seq")
    candidates = sorted(
        (event for event in events if event.task_id == task_id),
        key=lambda event: event.event_seq,
    )
    expected = published_seq + 1
    for event in candidates:
        event.assert_integrity()
        if event.event_seq == expected:
            return event if event.state is OutboxState.PENDING else None
        if event.event_seq > expected:
            return None
    return None


def claim_event(
    current: OutboxEvent,
    *,
    owner_instance_id: str,
    fencing_token: int,
    claim_token: str,
    lease_until_ms: int,
    now_ms: int,
) -> OutboxEvent:
    current.assert_integrity()
    if current.state is not OutboxState.PENDING:
        raise OutboxContractError("outbox claim requires PENDING state")
    _text(owner_instance_id, "owner_instance_id")
    _text(claim_token, "claim_token")
    _integer(fencing_token, "fencing_token", minimum=1)
    _integer(lease_until_ms, "lease_until_ms", minimum=1)
    _integer(now_ms, "now_ms")
    if lease_until_ms <= now_ms:
        raise OutboxContractError("outbox lease must be in the future")
    return replace(
        current,
        state=OutboxState.CLAIMED,
        owner_instance_id=owner_instance_id,
        fencing_token=fencing_token,
        claim_token=claim_token,
        lease_until_ms=lease_until_ms,
        _snapshot_digest="",
    )


def mark_published(
    current: OutboxEvent,
    *,
    owner_instance_id: str,
    fencing_token: int,
) -> OutboxEvent:
    current.assert_integrity()
    if current.state is OutboxState.PUBLISHED:
        return current
    if current.state is not OutboxState.CLAIMED:
        raise OutboxContractError("publish requires CLAIMED state")
    _text(owner_instance_id, "owner_instance_id")
    if type(fencing_token) is not int or fencing_token != current.fencing_token:
        raise OutboxContractError("fence does not match")
    if owner_instance_id != current.owner_instance_id:
        raise OutboxContractError("owner does not match")
    return replace(current, state=OutboxState.PUBLISHED, _snapshot_digest="")
