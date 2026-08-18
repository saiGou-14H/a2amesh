"""Pure durable-dispatch intent lifecycle contracts for C2."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

import rfc8785

_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]+$")


class DispatchContractError(ValueError):
    """A dispatch intent lifecycle or fencing contract is invalid."""


class DispatchIntentState(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    ABORTED = "ABORTED"
    DEAD = "DEAD"


def _text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or value != value.strip():
        raise DispatchContractError(f"{label} must be a plain string")
    if not value:
        if allow_empty:
            return value
        raise DispatchContractError(f"{label} must be a plain string")
    if not _SAFE_TEXT.fullmatch(value):
        raise DispatchContractError(f"{label} contains a forbidden control character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise DispatchContractError(f"{label} is not valid UTF-8") from exc
    return value


def _digest(value: object, label: str) -> str:
    value = _text(value, label)
    if not _SHA256.fullmatch(value):
        raise DispatchContractError(f"{label} must be lowercase SHA-256 hex")
    return value


def _integer(value: object, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= _MAX_SAFE_INTEGER:
        raise DispatchContractError(f"{label} must be a JSON-safe integer")
    return value


@dataclass(frozen=True, slots=True)
class DispatchIntent:
    dispatch_id: str
    task_id: str
    target_agent_id: str
    command_digest: str
    task_version: int
    config_generation: int
    state: DispatchIntentState = DispatchIntentState.PENDING
    attempt: int = 1
    owner_instance_id: str = ""
    fencing_token: int = 0
    claim_token: str = ""
    lease_until_ms: int = 0
    _snapshot_digest: str = field(default="", repr=False, compare=True)

    def __post_init__(self) -> None:
        self._assert_semantics()
        snapshot = self._compute_digest()
        if type(self._snapshot_digest) is not str:
            raise DispatchContractError("dispatch snapshot digest must be plain text")
        if self._snapshot_digest != "":
            if self._snapshot_digest != snapshot:
                raise DispatchContractError("dispatch snapshot digest mismatch")
        else:
            object.__setattr__(self, "_snapshot_digest", snapshot)

    def _assert_semantics(self) -> None:
        _text(self.dispatch_id, "dispatch_id")
        _text(self.task_id, "task_id")
        _text(self.target_agent_id, "target_agent_id")
        _digest(self.command_digest, "command_digest")
        _integer(self.task_version, "task_version", minimum=1)
        _integer(self.config_generation, "config_generation", minimum=1)
        if type(self.state) is not DispatchIntentState:
            raise DispatchContractError("state must be DispatchIntentState")
        _integer(self.attempt, "attempt", minimum=1)
        _integer(self.fencing_token, "fencing_token")
        _integer(self.lease_until_ms, "lease_until_ms")
        _text(self.owner_instance_id, "owner_instance_id", allow_empty=True)
        _text(self.claim_token, "claim_token", allow_empty=True)
        if self.state is DispatchIntentState.PENDING:
            if (
                self.owner_instance_id
                or self.claim_token
                or self.fencing_token
                or self.lease_until_ms
            ):
                raise DispatchContractError("pending intent cannot have a claim")
        else:
            _text(self.owner_instance_id, "owner_instance_id")
            _text(self.claim_token, "claim_token")
            _integer(self.fencing_token, "fencing_token", minimum=1)
            _integer(self.lease_until_ms, "lease_until_ms", minimum=1)


    def _payload(self) -> dict[str, Any]:
        return {
            "dispatchId": self.dispatch_id,
            "taskId": self.task_id,
            "targetAgentId": self.target_agent_id,
            "commandDigest": self.command_digest,
            "taskVersion": self.task_version,
            "configGeneration": self.config_generation,
            "state": self.state.value,
            "attempt": self.attempt,
            "ownerInstanceId": self.owner_instance_id,
            "fencingToken": self.fencing_token,
            "claimToken": self.claim_token,
            "leaseUntilMs": self.lease_until_ms,
        }

    def _compute_digest(self) -> str:
        return hashlib.sha256(rfc8785.dumps(self._payload())).hexdigest()

    def assert_integrity(self) -> None:
        self._assert_semantics()
        if self._compute_digest() != self._snapshot_digest:
            raise DispatchContractError("dispatch snapshot digest mismatch")

    def canonical_bytes(self) -> bytes:
        self.assert_integrity()
        return rfc8785.dumps(self._payload())

    @property
    def intent_digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def create_dispatch_intent(
    *,
    dispatch_id: str,
    task_id: str,
    target_agent_id: str,
    command_digest: str,
    task_version: int,
    config_generation: int,
) -> DispatchIntent:
    return DispatchIntent(
        dispatch_id=dispatch_id,
        task_id=task_id,
        target_agent_id=target_agent_id,
        command_digest=command_digest,
        task_version=task_version,
        config_generation=config_generation,
    )


def claim_dispatch(
    current: DispatchIntent,
    *,
    owner_instance_id: str,
    fencing_token: int,
    claim_token: str,
    lease_until_ms: int,
    now_ms: int,
) -> DispatchIntent:
    if type(current) is not DispatchIntent:
        raise DispatchContractError("current dispatch must be DispatchIntent")
    current.assert_integrity()
    if current.state is not DispatchIntentState.PENDING:
        raise DispatchContractError("dispatch claim requires PENDING state")
    _text(owner_instance_id, "owner_instance_id")
    _text(claim_token, "claim_token")
    _integer(fencing_token, "fencing_token", minimum=1)
    _integer(now_ms, "now_ms")
    _integer(lease_until_ms, "lease_until_ms", minimum=1)
    if lease_until_ms <= now_ms:
        raise DispatchContractError("claim lease must be in the future")
    return replace(
        current,
        state=DispatchIntentState.CLAIMED,
        owner_instance_id=owner_instance_id,
        fencing_token=fencing_token,
        claim_token=claim_token,
        lease_until_ms=lease_until_ms,
        _snapshot_digest="",
    )


def mark_dispatch_sent(
    current: DispatchIntent,
    *,
    owner_instance_id: str,
    fencing_token: int,
    claim_token: str,
    attempt: int,
    now_ms: int,
) -> DispatchIntent:
    if type(current) is not DispatchIntent:
        raise DispatchContractError("current dispatch must be DispatchIntent")
    current.assert_integrity()
    if current.state is not DispatchIntentState.CLAIMED:
        raise DispatchContractError("mark sent requires CLAIMED state")
    _text(owner_instance_id, "owner_instance_id")
    _text(claim_token, "claim_token")
    _integer(attempt, "attempt", minimum=1)
    _integer(now_ms, "now_ms")
    if type(fencing_token) is not int or fencing_token != current.fencing_token:
        raise DispatchContractError("fence does not match")
    if owner_instance_id != current.owner_instance_id:
        raise DispatchContractError("owner does not match")
    if claim_token != current.claim_token:
        raise DispatchContractError("claim token does not match")
    if attempt != current.attempt:
        raise DispatchContractError("attempt does not match")
    if now_ms >= current.lease_until_ms:
        raise DispatchContractError("dispatch lease is expired")
    return replace(current, state=DispatchIntentState.SENT, _snapshot_digest="")


def accept_dispatch(
    current: DispatchIntent,
    *,
    owner_instance_id: str,
    fencing_token: int,
    claim_token: str,
    attempt: int,
    now_ms: int,
) -> DispatchIntent:
    if type(current) is not DispatchIntent:
        raise DispatchContractError("current dispatch must be DispatchIntent")
    current.assert_integrity()
    _text(owner_instance_id, "owner_instance_id")
    _text(claim_token, "claim_token")
    _integer(attempt, "attempt", minimum=1)
    _integer(now_ms, "now_ms")
    if type(fencing_token) is not int or fencing_token != current.fencing_token:
        raise DispatchContractError("fence does not match")
    if owner_instance_id != current.owner_instance_id:
        raise DispatchContractError("owner does not match")
    if claim_token != current.claim_token:
        raise DispatchContractError("claim token does not match")
    if attempt != current.attempt:
        raise DispatchContractError("attempt does not match")
    if current.state is DispatchIntentState.ACCEPTED:
        if now_ms >= current.lease_until_ms:
            raise DispatchContractError("dispatch lease is expired")
        return current
    if current.state is not DispatchIntentState.SENT:
        raise DispatchContractError("accept requires SENT state")
    if now_ms >= current.lease_until_ms:
        raise DispatchContractError("dispatch lease is expired")
    return replace(current, state=DispatchIntentState.ACCEPTED, _snapshot_digest="")


def reclaim_dispatch(
    current: DispatchIntent,
    *,
    new_owner_instance_id: str,
    new_fencing_token: int,
    new_claim_token: str,
    new_lease_until_ms: int,
    now_ms: int,
) -> DispatchIntent:
    if type(current) is not DispatchIntent:
        raise DispatchContractError("current dispatch must be DispatchIntent")
    current.assert_integrity()
    if current.state not in {DispatchIntentState.CLAIMED, DispatchIntentState.SENT}:
        raise DispatchContractError("reclaim requires CLAIMED or SENT state")
    _text(new_owner_instance_id, "new_owner_instance_id")
    _text(new_claim_token, "new_claim_token")
    _integer(new_fencing_token, "new_fencing_token", minimum=1)
    _integer(new_lease_until_ms, "new_lease_until_ms", minimum=1)
    _integer(now_ms, "now_ms")
    if now_ms < current.lease_until_ms:
        raise DispatchContractError("dispatch lease is not expired")
    if new_fencing_token <= current.fencing_token:
        raise DispatchContractError("new fence must be greater than old fence")
    if new_lease_until_ms <= now_ms:
        raise DispatchContractError("new lease must be in the future")
    return replace(
        current,
        state=DispatchIntentState.CLAIMED,
        attempt=current.attempt + 1,
        owner_instance_id=new_owner_instance_id,
        fencing_token=new_fencing_token,
        claim_token=new_claim_token,
        lease_until_ms=new_lease_until_ms,
        _snapshot_digest="",
    )
