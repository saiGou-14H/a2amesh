"""Pure lease and fencing contracts for the C2 Task aggregate.

Allocation, persistence, and CAS execution belong to the later State adapter.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from typing import Any

import rfc8785

_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]+$")


class LeaseContractError(ValueError):
    """A lease, fencing, or owner write contract is invalid."""


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise LeaseContractError(f"{label} must be a non-empty plain string")
    if not _SAFE_TEXT.fullmatch(value):
        raise LeaseContractError(f"{label} contains a forbidden control character")
    return value


def _digest(value: object, label: str) -> str:
    value = _text(value, label)
    if not _SHA256.fullmatch(value):
        raise LeaseContractError(f"{label} must be lowercase SHA-256 hex")
    return value


def _integer(value: object, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= _MAX_SAFE_INTEGER:
        raise LeaseContractError(f"{label} must be a JSON-safe integer")
    return value


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    lease_id: str
    owner_principal_id: str
    owner_instance_id: str
    fencing_token: int
    attempt: int
    issued_at_ms: int
    lease_until_ms: int
    config_generation: int
    request_digest: str
    _snapshot_digest: str = field(default="", repr=False, compare=True)

    def __post_init__(self) -> None:
        _text(self.lease_id, "lease_id")
        _text(self.owner_principal_id, "owner_principal_id")
        _text(self.owner_instance_id, "owner_instance_id")
        _integer(self.fencing_token, "fencing_token", minimum=1)
        _integer(self.attempt, "attempt", minimum=1)
        _integer(self.issued_at_ms, "issued_at_ms")
        _integer(self.lease_until_ms, "lease_until_ms")
        _integer(self.config_generation, "config_generation", minimum=1)
        _digest(self.request_digest, "request_digest")
        if self.lease_until_ms <= self.issued_at_ms:
            raise LeaseContractError("lease must end after issued time")
        snapshot = self._compute_digest()
        if self._snapshot_digest:
            if type(self._snapshot_digest) is not str or self._snapshot_digest != snapshot:
                raise LeaseContractError("lease snapshot digest mismatch")
        else:
            object.__setattr__(self, "_snapshot_digest", snapshot)

    def _payload(self) -> dict[str, Any]:
        return {
            "leaseId": self.lease_id,
            "ownerPrincipalId": self.owner_principal_id,
            "ownerInstanceId": self.owner_instance_id,
            "fencingToken": self.fencing_token,
            "attempt": self.attempt,
            "issuedAtMs": self.issued_at_ms,
            "leaseUntilMs": self.lease_until_ms,
            "configGeneration": self.config_generation,
            "requestDigest": self.request_digest,
        }

    def _compute_digest(self) -> str:
        return hashlib.sha256(rfc8785.dumps(self._payload())).hexdigest()

    def assert_integrity(self) -> None:
        if type(self.fencing_token) is not int or self.fencing_token < 1:
            raise LeaseContractError("fence must be positive")
        if type(self.attempt) is not int or self.attempt < 1:
            raise LeaseContractError("attempt must be positive")
        if type(self.lease_until_ms) is not int or self.lease_until_ms <= self.issued_at_ms:
            raise LeaseContractError("lease interval is invalid")
        if self._compute_digest() != self._snapshot_digest:
            raise LeaseContractError("lease snapshot digest mismatch")

    def canonical_bytes(self) -> bytes:
        self.assert_integrity()
        return rfc8785.dumps(self._payload())

    @property
    def lease_digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def renew_lease(
    current: LeaseGrant,
    *,
    owner_principal_id: str,
    owner_instance_id: str,
    fencing_token: int,
    lease_until_ms: int,
) -> LeaseGrant:
    if type(current) is not LeaseGrant:
        raise LeaseContractError("current lease must be LeaseGrant")
    current.assert_integrity()
    if (
        owner_principal_id != current.owner_principal_id
        or owner_instance_id != current.owner_instance_id
    ):
        raise LeaseContractError("lease owner does not match")
    if type(fencing_token) is not int or fencing_token != current.fencing_token:
        raise LeaseContractError("lease fence does not match")
    _integer(lease_until_ms, "lease_until_ms")
    if lease_until_ms <= current.lease_until_ms:
        raise LeaseContractError("renewal must extend the lease")
    return replace(current, lease_until_ms=lease_until_ms, _snapshot_digest="")


def validate_lease_write(
    current: LeaseGrant,
    *,
    owner_principal_id: str,
    owner_instance_id: str,
    fencing_token: int,
    attempt: int,
    config_generation: int,
    now_ms: int,
) -> None:
    if type(current) is not LeaseGrant:
        raise LeaseContractError("current lease must be LeaseGrant")
    current.assert_integrity()
    if (
        owner_principal_id != current.owner_principal_id
        or owner_instance_id != current.owner_instance_id
    ):
        raise LeaseContractError("owner does not match")
    if type(fencing_token) is not int or fencing_token != current.fencing_token:
        raise LeaseContractError("fence does not match")
    if type(attempt) is not int or attempt != current.attempt:
        raise LeaseContractError("attempt does not match")
    if type(config_generation) is not int or config_generation != current.config_generation:
        raise LeaseContractError("generation does not match")
    _integer(now_ms, "now_ms")
    if now_ms < current.issued_at_ms or now_ms >= current.lease_until_ms:
        raise LeaseContractError("lease is expired")
