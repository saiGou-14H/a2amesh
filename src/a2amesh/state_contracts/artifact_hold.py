"""ArtifactHold SCAN/EXPIRE identity, candidate, CAS, and replay contracts."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

import rfc8785

_MAX_SAFE_INTEGER = 9_007_199_254_740_991
ARTIFACT_HOLD_CANDIDATE_LEASE_MAX_MS = 300_000
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_EXPIRE_DOMAIN = b"a2amesh-artifact-hold-expire-v1"
_COMMIT_DOMAIN = b"a2amesh-artifact-hold-expire-commit-v1"
_EVENT_DOMAIN = b"a2amesh-artifact-hold-expire-event-v1"
_REPLAY_CLAIM_DOMAIN = b"a2amesh-artifact-hold-replay-claim-v1"
ARTIFACT_HOLD_REAPER_COMPONENT_TYPE = "artifact-hold-reaper"
ARTIFACT_HOLD_REAPER_PRINCIPAL_ID = "component:artifact-hold-reaper"
ARTIFACT_HOLD_EXPIRY_SUBJECT = "a2a.v1.state.artifact.hold.expire"
ARTIFACT_DELETE_WORKER_COMPONENT_TYPE = "artifact-delete-worker"
ARTIFACT_DELETE_SUBJECT = "a2a.v1.state.artifact.delete"
_SEPARATOR = b"\x00"


class ArtifactHoldStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class ArtifactLifecycleStatus(StrEnum):
    PENDING_UPLOAD = "PENDING_UPLOAD"
    AVAILABLE = "AVAILABLE"
    QUARANTINED = "QUARANTINED"
    DELETING = "DELETING"
    DELETED = "DELETED"
    FAILED = "FAILED"


class ArtifactHoldExpiryOperation(StrEnum):
    SCAN = "SCAN"
    EXPIRE = "EXPIRE"
    REPLAY_CLAIM = "REPLAY_CLAIM"


class ArtifactHoldExpiryEventSink(StrEnum):
    AUDIT = "AUDIT"
    OUTBOX = "OUTBOX"


class ArtifactHoldExpiryConflict(ValueError):
    """Raised when a closed wire or State predicate fails closed."""


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryScanRequest:
    schema_version: str
    operation: ArtifactHoldExpiryOperation
    scan_operation_id: str
    max_candidates: int
    idempotency_key: str
    request_digest: str

    @classmethod
    def create(
        cls,
        *,
        scan_operation_id: str,
        max_candidates: int,
    ) -> ArtifactHoldExpiryScanRequest:
        request = cls(
            schema_version="1",
            operation=ArtifactHoldExpiryOperation.SCAN,
            scan_operation_id=scan_operation_id,
            max_candidates=max_candidates,
            idempotency_key=scan_operation_id,
            request_digest="",
        )
        created = replace(request, request_digest=_scan_request_digest(request))
        created.validate()
        return created

    @classmethod
    def from_canonical_json(
        cls, wire: bytes
    ) -> ArtifactHoldExpiryScanRequest:
        value = _parse_canonical_object(wire, "hold expiry scan request")
        _require_exact_fields(
            value,
            {
                "schemaVersion",
                "operation",
                "scanOperationId",
                "maxCandidates",
                "idempotencyKey",
                "requestDigest",
            },
            "hold expiry scan request",
        )
        operation = _parse_operation(value["operation"], "scan request operation")
        request = cls(
            schema_version=value["schemaVersion"],
            operation=operation,
            scan_operation_id=value["scanOperationId"],
            max_candidates=value["maxCandidates"],
            idempotency_key=value["idempotencyKey"],
            request_digest=value["requestDigest"],
        )
        request.validate()
        return request

    def validate(self) -> None:
        if self.schema_version != "1":
            raise ArtifactHoldExpiryConflict("unsupported hold expiry scan schema")
        if self.operation is not ArtifactHoldExpiryOperation.SCAN:
            raise ArtifactHoldExpiryConflict("hold expiry scan operation must be SCAN")
        _require_sha256(self.scan_operation_id, "scan_operation_id")
        _require_sha256(self.idempotency_key, "scan idempotency_key")
        _require_sha256(self.request_digest, "scan request_digest")
        _require_integer(self.max_candidates, "max_candidates", minimum=1, maximum=100)
        if self.idempotency_key != self.scan_operation_id:
            raise ArtifactHoldExpiryConflict(
                "scan idempotency_key must equal scan_operation_id"
            )
        if self.request_digest != _scan_request_digest(self):
            raise ArtifactHoldExpiryConflict("scan request_digest mismatch")

    def to_wire_dict(self) -> dict[str, object]:
        return _scan_request_payload(self) | {"requestDigest": self.request_digest}

    def canonical_json(self) -> bytes:
        return rfc8785.dumps(self.to_wire_dict())


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryCandidate:
    schema_version: str
    scan_operation_id: str
    candidate_lease_id: str
    candidate_fencing_token: int
    candidate_token: str
    owner_principal_id: str
    owner_instance_id: str
    issued_at_ms: int
    lease_until_ms: int
    artifact_id: str
    hold_id: str
    expected_hold_digest: str
    expected_artifact_version: int
    observed_expires_ms: int
    candidate_digest: str

    @classmethod
    def create(
        cls,
        *,
        scan_operation_id: str,
        candidate_lease_id: str,
        candidate_fencing_token: int,
        candidate_token: str,
        owner_principal_id: str,
        owner_instance_id: str,
        issued_at_ms: int,
        lease_until_ms: int,
        artifact_id: str,
        hold_id: str,
        expected_hold_digest: str,
        expected_artifact_version: int,
        observed_expires_ms: int,
    ) -> ArtifactHoldExpiryCandidate:
        candidate = cls(
            schema_version="1",
            scan_operation_id=scan_operation_id,
            candidate_lease_id=candidate_lease_id,
            candidate_fencing_token=candidate_fencing_token,
            candidate_token=candidate_token,
            owner_principal_id=owner_principal_id,
            owner_instance_id=owner_instance_id,
            issued_at_ms=issued_at_ms,
            lease_until_ms=lease_until_ms,
            artifact_id=artifact_id,
            hold_id=hold_id,
            expected_hold_digest=expected_hold_digest,
            expected_artifact_version=expected_artifact_version,
            observed_expires_ms=observed_expires_ms,
            candidate_digest="",
        )
        created = replace(
            candidate,
            candidate_digest=_canonical_digest(_candidate_payload(candidate)),
        )
        created.validate()
        return created

    @classmethod
    def from_canonical_json(cls, wire: bytes) -> ArtifactHoldExpiryCandidate:
        value = _parse_canonical_object(wire, "hold expiry candidate")
        return _candidate_from_mapping(value, "hold expiry candidate")

    def validate(self) -> None:
        if self.schema_version != "1":
            raise ArtifactHoldExpiryConflict("unsupported expiry candidate schema")
        _require_sha256(self.scan_operation_id, "candidate.scan_operation_id")
        _require_candidate_token(self.candidate_lease_id, "candidate_lease_id")
        _require_candidate_token(self.candidate_token, "candidate_token")
        if self.candidate_lease_id == self.candidate_token:
            raise ArtifactHoldExpiryConflict(
                "candidate lease ID and candidate token must be distinct"
            )
        _require_integer(
            self.candidate_fencing_token,
            "candidate_fencing_token",
            minimum=1,
        )
        _require_stable_text(self.owner_principal_id, "candidate owner principal")
        _require_stable_text(self.owner_instance_id, "candidate owner instance")
        _require_integer(self.issued_at_ms, "candidate issued_at_ms", minimum=0)
        _require_integer(self.lease_until_ms, "candidate lease_until_ms", minimum=0)
        if self.lease_until_ms <= self.issued_at_ms:
            raise ArtifactHoldExpiryConflict("candidate lease must end after issue time")
        if self.lease_until_ms - self.issued_at_ms > ARTIFACT_HOLD_CANDIDATE_LEASE_MAX_MS:
            raise ArtifactHoldExpiryConflict(
                "candidate lease exceeds maximum candidate lease duration"
            )
        _require_stable_text(self.artifact_id, "candidate artifact_id")
        _require_stable_text(self.hold_id, "candidate hold_id")
        _require_sha256(self.expected_hold_digest, "candidate expected_hold_digest")
        _require_integer(
            self.expected_artifact_version,
            "candidate expected_artifact_version",
            minimum=1,
        )
        _require_integer(
            self.observed_expires_ms,
            "candidate observed_expires_ms",
            minimum=0,
        )
        _require_sha256(self.candidate_digest, "candidate_digest")
        if self.candidate_digest != _canonical_digest(_candidate_payload(self)):
            raise ArtifactHoldExpiryConflict("candidate_digest mismatch")

    def to_wire_dict(self) -> dict[str, object]:
        return _candidate_payload(self) | {"candidateDigest": self.candidate_digest}

    def canonical_json(self) -> bytes:
        return rfc8785.dumps(self.to_wire_dict())


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryScanResult:
    schema_version: str
    scan_operation_id: str
    request_digest: str
    candidates: tuple[ArtifactHoldExpiryCandidate, ...]
    result_digest: str

    @classmethod
    def create(
        cls,
        *,
        request: ArtifactHoldExpiryScanRequest,
        candidates: tuple[ArtifactHoldExpiryCandidate, ...],
    ) -> ArtifactHoldExpiryScanResult:
        request.validate()
        if not isinstance(candidates, tuple):
            raise ArtifactHoldExpiryConflict("scan candidates must be an immutable tuple")
        if len(candidates) > request.max_candidates:
            raise ArtifactHoldExpiryConflict(
                "scan result exceeds request max_candidates"
            )
        result = cls(
            schema_version="1",
            scan_operation_id=request.scan_operation_id,
            request_digest=request.request_digest,
            candidates=candidates,
            result_digest="",
        )
        created = replace(result, result_digest=_scan_result_digest(result))
        created.validate_for(request)
        return created

    @classmethod
    def from_canonical_json(cls, wire: bytes) -> ArtifactHoldExpiryScanResult:
        value = _parse_canonical_object(wire, "hold expiry scan result")
        _require_exact_fields(
            value,
            {
                "schemaVersion",
                "scanOperationId",
                "requestDigest",
                "candidates",
                "resultDigest",
            },
            "hold expiry scan result",
        )
        candidate_values = value["candidates"]
        if not isinstance(candidate_values, list):
            raise ArtifactHoldExpiryConflict(
                "hold expiry scan result candidates must be an array"
            )
        candidates = tuple(
            _candidate_from_mapping(candidate, f"scan candidate[{index}]")
            for index, candidate in enumerate(candidate_values)
        )
        result = cls(
            schema_version=value["schemaVersion"],
            scan_operation_id=value["scanOperationId"],
            request_digest=value["requestDigest"],
            candidates=candidates,
            result_digest=value["resultDigest"],
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1":
            raise ArtifactHoldExpiryConflict("unsupported expiry scan result schema")
        _require_sha256(self.scan_operation_id, "scan result operation ID")
        _require_sha256(self.request_digest, "scan result request digest")
        if not isinstance(self.candidates, tuple):
            raise ArtifactHoldExpiryConflict("scan candidates must be an immutable tuple")
        previous_key: tuple[bytes, bytes] | None = None
        candidate_lease_ids: set[str] = set()
        candidate_tokens: set[str] = set()
        candidate_fences: set[int] = set()
        for candidate in self.candidates:
            if not isinstance(candidate, ArtifactHoldExpiryCandidate):
                raise ArtifactHoldExpiryConflict("scan result contains invalid candidate")
            candidate.validate()
            if candidate.scan_operation_id != self.scan_operation_id:
                raise ArtifactHoldExpiryConflict("candidate scan operation mismatch")
            if (
                candidate.candidate_lease_id in candidate_lease_ids
                or candidate.candidate_token in candidate_tokens
                or candidate.candidate_fencing_token in candidate_fences
            ):
                raise ArtifactHoldExpiryConflict(
                    "scan result contains duplicate candidate authority"
                )
            if (
                candidate.candidate_lease_id in candidate_tokens
                or candidate.candidate_token in candidate_lease_ids
            ):
                raise ArtifactHoldExpiryConflict(
                    "candidate authority namespace collision"
                )
            candidate_lease_ids.add(candidate.candidate_lease_id)
            candidate_tokens.add(candidate.candidate_token)
            candidate_fences.add(candidate.candidate_fencing_token)
            key = (candidate.artifact_id.encode(), candidate.hold_id.encode())
            if previous_key is not None and key <= previous_key:
                raise ArtifactHoldExpiryConflict(
                    "scan candidates must be unique and UTF-8 sorted"
                )
            previous_key = key
        _require_sha256(self.result_digest, "scan result_digest")
        if self.result_digest != _scan_result_digest(self):
            raise ArtifactHoldExpiryConflict("scan result_digest mismatch")

    def validate_for(self, request: ArtifactHoldExpiryScanRequest) -> None:
        request.validate()
        self.validate()
        if (
            self.scan_operation_id != request.scan_operation_id
            or self.request_digest != request.request_digest
        ):
            raise ArtifactHoldExpiryConflict(
                "scan result is not bound to originating request"
            )
        if len(self.candidates) > request.max_candidates:
            raise ArtifactHoldExpiryConflict(
                "scan result exceeds request max_candidates"
            )

    def to_wire_dict(self) -> dict[str, object]:
        return _scan_result_payload(self) | {"resultDigest": self.result_digest}

    def canonical_json(self) -> bytes:
        return rfc8785.dumps(self.to_wire_dict())


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryScanAuthority:
    """Immutable pure-contract seal for one SCAN candidate projection."""

    schema_version: str
    scan_operation_id: str
    candidate_lease_id: str
    candidate_token: str
    candidate_digest: str
    result_digest: str
    owner_principal_id: str
    owner_instance_id: str
    candidate_fencing_token: int
    issued_at_ms: int
    lease_until_ms: int

    @classmethod
    def create(
        cls,
        *,
        request: ArtifactHoldExpiryScanRequest,
        result: ArtifactHoldExpiryScanResult,
        candidate: ArtifactHoldExpiryCandidate,
    ) -> ArtifactHoldExpiryScanAuthority:
        request.validate()
        result.validate_for(request)
        candidate.validate()
        if candidate not in result.candidates:
            raise ArtifactHoldExpiryConflict(
                "scan authority candidate is absent from scan result"
            )
        authority = cls(
            schema_version="1",
            scan_operation_id=request.scan_operation_id,
            candidate_lease_id=candidate.candidate_lease_id,
            candidate_token=candidate.candidate_token,
            candidate_digest=candidate.candidate_digest,
            result_digest=result.result_digest,
            owner_principal_id=candidate.owner_principal_id,
            owner_instance_id=candidate.owner_instance_id,
            candidate_fencing_token=candidate.candidate_fencing_token,
            issued_at_ms=candidate.issued_at_ms,
            lease_until_ms=candidate.lease_until_ms,
        )
        authority.validate()
        return authority

    def validate(self) -> None:
        if self.schema_version != "1":
            raise ArtifactHoldExpiryConflict("unsupported scan authority schema")
        _require_sha256(self.scan_operation_id, "scan authority operation ID")
        _require_candidate_token(self.candidate_lease_id, "scan authority lease ID")
        _require_candidate_token(self.candidate_token, "scan authority token")
        if self.candidate_lease_id == self.candidate_token:
            raise ArtifactHoldExpiryConflict("scan authority lease and token must differ")
        _require_sha256(self.candidate_digest, "scan authority candidate digest")
        _require_sha256(self.result_digest, "scan authority result digest")
        _require_stable_text(
            self.owner_principal_id,
            "scan authority owner principal",
        )
        _require_stable_text(self.owner_instance_id, "scan authority owner instance")
        _require_integer(
            self.candidate_fencing_token,
            "scan authority fencing token",
            minimum=1,
        )
        _require_integer(self.issued_at_ms, "scan authority issued_at_ms", minimum=0)
        _require_integer(
            self.lease_until_ms,
            "scan authority lease_until_ms",
            minimum=0,
        )
        if self.lease_until_ms <= self.issued_at_ms:
            raise ArtifactHoldExpiryConflict(
                "scan authority lease must end after issue time"
            )
        if (
            self.lease_until_ms - self.issued_at_ms
            > ARTIFACT_HOLD_CANDIDATE_LEASE_MAX_MS
        ):
            raise ArtifactHoldExpiryConflict(
                "scan authority lease exceeds maximum duration"
            )

    def validate_against(
        self,
        request: ArtifactHoldExpiryScanRequest,
        result: ArtifactHoldExpiryScanResult,
        candidate: ArtifactHoldExpiryCandidate,
    ) -> None:
        self.validate()
        result.validate_for(request)
        candidate.validate()
        if candidate not in result.candidates:
            raise ArtifactHoldExpiryConflict(
                "scan authority candidate is absent from scan result"
            )
        if (
            self.scan_operation_id != request.scan_operation_id
            or self.candidate_lease_id != candidate.candidate_lease_id
            or self.candidate_token != candidate.candidate_token
            or self.candidate_digest != candidate.candidate_digest
            or self.result_digest != result.result_digest
            or self.owner_principal_id != candidate.owner_principal_id
            or self.owner_instance_id != candidate.owner_instance_id
            or self.candidate_fencing_token
            != candidate.candidate_fencing_token
            or self.issued_at_ms != candidate.issued_at_ms
            or self.lease_until_ms != candidate.lease_until_ms
        ):
            raise ArtifactHoldExpiryConflict(
                "scan authority seal contradicts scan evidence"
            )


_SCAN_ALLOCATION_SEAL = object()


@dataclass(frozen=True, slots=True)
class _ArtifactHoldExpiryScanAllocation:
    """Private State-issued proof required to persist a SCAN candidate."""

    request: ArtifactHoldExpiryScanRequest
    candidate: ArtifactHoldExpiryCandidate
    previous_fence_high_water: int
    allocation_digest: str
    _seal: object

    def validate(self) -> None:
        if self._seal is not _SCAN_ALLOCATION_SEAL:
            raise ArtifactHoldExpiryConflict(
                "scan allocation proof is not State-issued"
            )
        self.request.validate()
        self.candidate.validate()
        _require_integer(
            self.previous_fence_high_water,
            "scan allocation previous fence high-water",
            minimum=0,
        )
        if self.candidate.scan_operation_id != self.request.scan_operation_id:
            raise ArtifactHoldExpiryConflict(
                "scan allocation candidate operation mismatch"
            )
        if (
            self.candidate.candidate_fencing_token
            <= self.previous_fence_high_water
        ):
            raise ArtifactHoldExpiryConflict(
                "scan allocation fence does not advance high-water"
            )
        _require_sha256(self.allocation_digest, "scan allocation digest")
        if self.allocation_digest != _scan_allocation_digest(self):
            raise ArtifactHoldExpiryConflict("scan allocation digest mismatch")

    def validate_against(
        self,
        request: ArtifactHoldExpiryScanRequest,
        candidate: ArtifactHoldExpiryCandidate,
        previous_fence_high_water: int,
    ) -> None:
        self.validate()
        if (
            self.request != request
            or self.candidate != candidate
            or self.previous_fence_high_water != previous_fence_high_water
        ):
            raise ArtifactHoldExpiryConflict(
                "scan allocation proof does not bind persisted scan evidence"
            )


def _issue_scan_allocation(
    *,
    request: ArtifactHoldExpiryScanRequest,
    candidate: ArtifactHoldExpiryCandidate,
    previous_fence_high_water: int,
) -> _ArtifactHoldExpiryScanAllocation:
    request.validate()
    candidate.validate()
    _require_integer(
        previous_fence_high_water,
        "scan allocation previous fence high-water",
        minimum=0,
    )
    allocation = _ArtifactHoldExpiryScanAllocation(
        request=request,
        candidate=candidate,
        previous_fence_high_water=previous_fence_high_water,
        allocation_digest="",
        _seal=_SCAN_ALLOCATION_SEAL,
    )
    return replace(
        allocation,
        allocation_digest=_scan_allocation_digest(allocation),
    )


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryReplayClaimRequest:
    schema_version: str
    operation: ArtifactHoldExpiryOperation
    replay_operation_id: str
    expire_operation_id: str
    base_commit_digest: str
    artifact_id: str
    hold_id: str
    expected_hold_digest: str
    expected_artifact_version: int
    observed_expires_ms: int
    candidate_lease_id: str
    candidate_token: str
    idempotency_key: str
    request_digest: str

    @classmethod
    def create(
        cls,
        *,
        replay_operation_id: str,
        expire_operation_id: str,
        base_commit_digest: str,
        artifact_id: str,
        hold_id: str,
        expected_hold_digest: str,
        expected_artifact_version: int,
        observed_expires_ms: int,
        candidate_lease_id: str,
        candidate_token: str,
    ) -> ArtifactHoldExpiryReplayClaimRequest:
        request = cls(
            schema_version="1",
            operation=ArtifactHoldExpiryOperation.REPLAY_CLAIM,
            replay_operation_id=replay_operation_id,
            expire_operation_id=expire_operation_id,
            base_commit_digest=base_commit_digest,
            artifact_id=artifact_id,
            hold_id=hold_id,
            expected_hold_digest=expected_hold_digest,
            expected_artifact_version=expected_artifact_version,
            observed_expires_ms=observed_expires_ms,
            candidate_lease_id=candidate_lease_id,
            candidate_token=candidate_token,
            idempotency_key=replay_operation_id,
            request_digest="",
        )
        created = replace(
            request,
            request_digest=_replay_claim_request_digest(request),
        )
        created.validate()
        return created

    @classmethod
    def from_canonical_json(
        cls,
        wire: bytes,
    ) -> ArtifactHoldExpiryReplayClaimRequest:
        value = _parse_canonical_object(wire, "hold expiry replay claim request")
        _require_exact_fields(
            value,
            {
                "schemaVersion",
                "operation",
                "replayOperationId",
                "expireOperationId",
                "baseCommitDigest",
                "artifactId",
                "holdId",
                "expectedHoldDigest",
                "expectedArtifactVersion",
                "observedExpiresMs",
                "candidateLeaseId",
                "candidateToken",
                "idempotencyKey",
                "requestDigest",
            },
            "hold expiry replay claim request",
        )
        request = cls(
            schema_version=value["schemaVersion"],
            operation=_parse_operation(
                value["operation"],
                "replay claim request operation",
            ),
            replay_operation_id=value["replayOperationId"],
            expire_operation_id=value["expireOperationId"],
            base_commit_digest=value["baseCommitDigest"],
            artifact_id=value["artifactId"],
            hold_id=value["holdId"],
            expected_hold_digest=value["expectedHoldDigest"],
            expected_artifact_version=value["expectedArtifactVersion"],
            observed_expires_ms=value["observedExpiresMs"],
            candidate_lease_id=value["candidateLeaseId"],
            candidate_token=value["candidateToken"],
            idempotency_key=value["idempotencyKey"],
            request_digest=value["requestDigest"],
        )
        request.validate()
        return request

    def validate(self) -> None:
        if self.schema_version != "1":
            raise ArtifactHoldExpiryConflict(
                "unsupported hold expiry replay claim schema"
            )
        if self.operation is not ArtifactHoldExpiryOperation.REPLAY_CLAIM:
            raise ArtifactHoldExpiryConflict(
                "hold expiry replay claim operation must be REPLAY_CLAIM"
            )
        _require_sha256(self.replay_operation_id, "replay_operation_id")
        _require_sha256(self.expire_operation_id, "expire_operation_id")
        _require_sha256(self.base_commit_digest, "replay claim base_commit_digest")
        _require_stable_text(self.artifact_id, "replay claim artifact_id")
        _require_stable_text(self.hold_id, "replay claim hold_id")
        _require_sha256(
            self.expected_hold_digest,
            "replay claim expected_hold_digest",
        )
        _require_integer(
            self.expected_artifact_version,
            "replay claim expected_artifact_version",
            minimum=1,
        )
        _require_integer(
            self.observed_expires_ms,
            "replay claim observed_expires_ms",
            minimum=0,
        )
        _require_candidate_token(
            self.candidate_lease_id,
            "replay claim candidate_lease_id",
        )
        _require_candidate_token(
            self.candidate_token,
            "replay claim candidate_token",
        )
        if self.candidate_lease_id == self.candidate_token:
            raise ArtifactHoldExpiryConflict(
                "replay claim lease ID and token must differ"
            )
        if self.idempotency_key != self.replay_operation_id:
            raise ArtifactHoldExpiryConflict(
                "replay claim idempotency_key must equal replay_operation_id"
            )
        if self.expire_operation_id != artifact_hold_expiry_operation_id(
            artifact_id=self.artifact_id,
            hold_id=self.hold_id,
            expected_hold_digest=self.expected_hold_digest,
            expected_artifact_version=self.expected_artifact_version,
            observed_expires_ms=self.observed_expires_ms,
        ):
            raise ArtifactHoldExpiryConflict(
                "replay claim expire_operation_id does not match due tuple"
            )
        if self.replay_operation_id != artifact_hold_expiry_replay_claim_operation_id(
            self.expire_operation_id,
            self.candidate_lease_id,
        ):
            raise ArtifactHoldExpiryConflict(
                "replay claim operation ID does not match commit and lease"
            )
        _require_sha256(self.request_digest, "replay claim request_digest")
        if self.request_digest != _replay_claim_request_digest(self):
            raise ArtifactHoldExpiryConflict(
                "replay claim request_digest mismatch"
            )

    def to_wire_dict(self) -> dict[str, object]:
        return _replay_claim_request_payload(self) | {
            "requestDigest": self.request_digest
        }

    def canonical_json(self) -> bytes:
        return rfc8785.dumps(self.to_wire_dict())


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryReplayClaimResult:
    schema_version: str
    replay_operation_id: str
    expire_operation_id: str
    base_commit_digest: str
    request_digest: str
    result_code: str
    candidate: ArtifactHoldExpiryCandidate
    result_digest: str

    @classmethod
    def create(
        cls,
        *,
        request: ArtifactHoldExpiryReplayClaimRequest,
        candidate: ArtifactHoldExpiryCandidate,
    ) -> ArtifactHoldExpiryReplayClaimResult:
        request.validate()
        candidate.validate()
        result = cls(
            schema_version="1",
            replay_operation_id=request.replay_operation_id,
            expire_operation_id=request.expire_operation_id,
            base_commit_digest=request.base_commit_digest,
            request_digest=request.request_digest,
            result_code="CLAIMED",
            candidate=candidate,
            result_digest="",
        )
        created = replace(
            result,
            result_digest=_replay_claim_result_digest(result),
        )
        created.validate_for(request)
        return created

    @classmethod
    def from_canonical_json(
        cls,
        wire: bytes,
    ) -> ArtifactHoldExpiryReplayClaimResult:
        value = _parse_canonical_object(wire, "hold expiry replay claim result")
        _require_exact_fields(
            value,
            {
                "schemaVersion",
                "replayOperationId",
                "expireOperationId",
                "baseCommitDigest",
                "requestDigest",
                "resultCode",
                "candidate",
                "resultDigest",
            },
            "hold expiry replay claim result",
        )
        candidate_value = value["candidate"]
        if not isinstance(candidate_value, Mapping):
            raise ArtifactHoldExpiryConflict(
                "replay claim result candidate must be an object"
            )
        result = cls(
            schema_version=value["schemaVersion"],
            replay_operation_id=value["replayOperationId"],
            expire_operation_id=value["expireOperationId"],
            base_commit_digest=value["baseCommitDigest"],
            request_digest=value["requestDigest"],
            result_code=value["resultCode"],
            candidate=_candidate_from_mapping(
                candidate_value,
                "replay claim result candidate",
            ),
            result_digest=value["resultDigest"],
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1":
            raise ArtifactHoldExpiryConflict(
                "unsupported hold expiry replay claim result schema"
            )
        _require_sha256(self.replay_operation_id, "replay result operation ID")
        _require_sha256(self.expire_operation_id, "replay result expire operation ID")
        _require_sha256(self.base_commit_digest, "replay result base_commit_digest")
        _require_sha256(self.request_digest, "replay result request digest")
        if self.result_code != "CLAIMED":
            raise ArtifactHoldExpiryConflict("unknown hold expiry replay claim result")
        if not isinstance(self.candidate, ArtifactHoldExpiryCandidate):
            raise ArtifactHoldExpiryConflict("replay result has invalid candidate")
        self.candidate.validate()
        if self.candidate.scan_operation_id != self.replay_operation_id:
            raise ArtifactHoldExpiryConflict(
                "replay result candidate is not bound to claim operation"
            )
        _require_sha256(self.result_digest, "replay result digest")
        if self.result_digest != _replay_claim_result_digest(self):
            raise ArtifactHoldExpiryConflict("replay result digest mismatch")

    def validate_for(self, request: ArtifactHoldExpiryReplayClaimRequest) -> None:
        request.validate()
        self.validate()
        candidate = self.candidate
        if (
            self.replay_operation_id != request.replay_operation_id
            or self.expire_operation_id != request.expire_operation_id
            or self.base_commit_digest != request.base_commit_digest
            or self.request_digest != request.request_digest
            or candidate.candidate_lease_id != request.candidate_lease_id
            or candidate.candidate_token != request.candidate_token
            or candidate.artifact_id != request.artifact_id
            or candidate.hold_id != request.hold_id
            or candidate.expected_hold_digest != request.expected_hold_digest
            or candidate.expected_artifact_version
            != request.expected_artifact_version
            or candidate.observed_expires_ms != request.observed_expires_ms
        ):
            raise ArtifactHoldExpiryConflict(
                "replay result is not bound to originating claim request"
            )

    def to_wire_dict(self) -> dict[str, object]:
        return _replay_claim_result_payload(self) | {
            "resultDigest": self.result_digest
        }

    def canonical_json(self) -> bytes:
        return rfc8785.dumps(self.to_wire_dict())


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryReplayCurrentAuthority:
    """Immutable pure-contract projection of the current replay authority."""

    schema_version: str
    expire_operation_id: str
    replay_operation_id: str
    base_commit_digest: str
    request_digest: str
    candidate_lease_id: str
    candidate_token: str
    candidate_digest: str
    result_digest: str
    owner_principal_id: str
    owner_instance_id: str
    candidate_fencing_token: int
    issued_at_ms: int
    lease_until_ms: int
    revision: int

    @classmethod
    def create(
        cls,
        *,
        request: ArtifactHoldExpiryReplayClaimRequest,
        result: ArtifactHoldExpiryReplayClaimResult,
        revision: int,
    ) -> ArtifactHoldExpiryReplayCurrentAuthority:
        request.validate()
        result.validate_for(request)
        _require_integer(revision, "replay current authority revision", minimum=1)
        candidate = result.candidate
        authority = cls(
            schema_version="1",
            expire_operation_id=request.expire_operation_id,
            replay_operation_id=request.replay_operation_id,
            base_commit_digest=request.base_commit_digest,
            request_digest=request.request_digest,
            candidate_lease_id=candidate.candidate_lease_id,
            candidate_token=candidate.candidate_token,
            candidate_digest=candidate.candidate_digest,
            result_digest=result.result_digest,
            owner_principal_id=candidate.owner_principal_id,
            owner_instance_id=candidate.owner_instance_id,
            candidate_fencing_token=candidate.candidate_fencing_token,
            issued_at_ms=candidate.issued_at_ms,
            lease_until_ms=candidate.lease_until_ms,
            revision=revision,
        )
        authority.validate()
        return authority

    def validate(self) -> None:
        if self.schema_version != "1":
            raise ArtifactHoldExpiryConflict(
                "unsupported replay current authority schema"
            )
        _require_sha256(
            self.expire_operation_id,
            "replay current authority expire operation ID",
        )
        _require_sha256(
            self.replay_operation_id,
            "replay current authority operation ID",
        )
        _require_sha256(
            self.base_commit_digest,
            "replay current authority base commit digest",
        )
        _require_sha256(
            self.request_digest,
            "replay current authority request digest",
        )
        _require_candidate_token(
            self.candidate_lease_id,
            "replay current authority lease ID",
        )
        _require_candidate_token(
            self.candidate_token,
            "replay current authority token",
        )
        _require_sha256(
            self.candidate_digest,
            "replay current authority candidate digest",
        )
        _require_sha256(
            self.result_digest,
            "replay current authority result digest",
        )
        _require_stable_text(
            self.owner_principal_id,
            "replay current authority owner principal",
        )
        _require_stable_text(
            self.owner_instance_id,
            "replay current authority owner instance",
        )
        _require_integer(
            self.candidate_fencing_token,
            "replay current authority fencing token",
            minimum=1,
        )
        _require_integer(
            self.issued_at_ms,
            "replay current authority issued_at_ms",
            minimum=0,
        )
        _require_integer(
            self.lease_until_ms,
            "replay current authority lease_until_ms",
            minimum=0,
        )
        if self.lease_until_ms <= self.issued_at_ms:
            raise ArtifactHoldExpiryConflict(
                "replay current authority lease must end after issue time"
            )
        if (
            self.lease_until_ms - self.issued_at_ms
            > ARTIFACT_HOLD_CANDIDATE_LEASE_MAX_MS
        ):
            raise ArtifactHoldExpiryConflict(
                "replay current authority lease exceeds maximum duration"
            )
        _require_integer(
            self.revision,
            "replay current authority revision",
            minimum=1,
        )

    def validate_against(
        self,
        request: ArtifactHoldExpiryReplayClaimRequest,
        result: ArtifactHoldExpiryReplayClaimResult,
    ) -> None:
        self.validate()
        result.validate_for(request)
        candidate = result.candidate
        if (
            self.expire_operation_id != request.expire_operation_id
            or self.replay_operation_id != request.replay_operation_id
            or self.base_commit_digest != request.base_commit_digest
            or self.request_digest != request.request_digest
            or self.candidate_lease_id != candidate.candidate_lease_id
            or self.candidate_token != candidate.candidate_token
            or self.candidate_digest != candidate.candidate_digest
            or self.result_digest != result.result_digest
            or self.owner_principal_id != candidate.owner_principal_id
            or self.owner_instance_id != candidate.owner_instance_id
            or self.candidate_fencing_token
            != candidate.candidate_fencing_token
            or self.issued_at_ms != candidate.issued_at_ms
            or self.lease_until_ms != candidate.lease_until_ms
        ):
            raise ArtifactHoldExpiryConflict(
                "replay current authority pointer contradicts claim evidence"
            )


_REPLAY_AUTHORITY_SEAL = object()


@dataclass(frozen=True, slots=True)
class _VerifiedArtifactHoldExpiryReplayAuthority:
    candidate: ArtifactHoldExpiryCandidate
    _seal: object

    def validate(self) -> None:
        if self._seal is not _REPLAY_AUTHORITY_SEAL:
            raise ArtifactHoldExpiryConflict("replay authority proof is not ledger-verified")
        if not isinstance(self.candidate, ArtifactHoldExpiryCandidate):
            raise ArtifactHoldExpiryConflict("replay authority proof has invalid candidate")
        self.candidate.validate()


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryCandidateLedgerEntry:
    candidate: ArtifactHoldExpiryCandidate
    consumed_by_expire_operation_id: str | None = None
    consumed_at_ms: int | None = None

    @property
    def consumed(self) -> bool:
        return self.consumed_by_expire_operation_id is not None

    def validate(self) -> None:
        if not isinstance(self.candidate, ArtifactHoldExpiryCandidate):
            raise ArtifactHoldExpiryConflict(
                "candidate ledger entry contains an invalid candidate"
            )
        self.candidate.validate()
        if self.consumed_by_expire_operation_id is None:
            if self.consumed_at_ms is not None:
                raise ArtifactHoldExpiryConflict(
                    "active candidate ledger entry has consumed timestamp"
                )
            return
        _require_sha256(
            self.consumed_by_expire_operation_id,
            "candidate consumed operation ID",
        )
        if self.consumed_at_ms is None:
            raise ArtifactHoldExpiryConflict(
                "consumed candidate ledger entry is missing timestamp"
            )
        _require_integer(
            self.consumed_at_ms,
            "candidate consumed timestamp",
            minimum=0,
        )


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryLedgerState:
    candidate_fence_high_water: int
    scan_requests: tuple[ArtifactHoldExpiryScanRequest, ...]
    scan_results: tuple[ArtifactHoldExpiryScanResult, ...]
    candidate_entries: tuple[ArtifactHoldExpiryCandidateLedgerEntry, ...]
    replay_claim_requests: tuple[ArtifactHoldExpiryReplayClaimRequest, ...] = ()
    replay_claim_results: tuple[ArtifactHoldExpiryReplayClaimResult, ...] = ()
    replay_current_authorities: tuple[ArtifactHoldExpiryReplayCurrentAuthority, ...] = ()
    scan_authorities: tuple[ArtifactHoldExpiryScanAuthority, ...] = ()
    scan_allocations: tuple[_ArtifactHoldExpiryScanAllocation, ...] = ()

    @classmethod
    def empty(cls) -> ArtifactHoldExpiryLedgerState:
        return cls(
            candidate_fence_high_water=0,
            scan_requests=(),
            scan_results=(),
            candidate_entries=(),
            replay_claim_requests=(),
            replay_claim_results=(),
        )

    def validate(self) -> None:
        _require_integer(
            self.candidate_fence_high_water,
            "candidate fence high-water",
            minimum=0,
        )
        if not isinstance(self.scan_requests, tuple):
            raise ArtifactHoldExpiryConflict("scan request ledger must be immutable")
        if not isinstance(self.scan_results, tuple):
            raise ArtifactHoldExpiryConflict("scan result ledger must be immutable")
        if not isinstance(self.candidate_entries, tuple):
            raise ArtifactHoldExpiryConflict("candidate ledger must be immutable")
        if not isinstance(self.replay_claim_requests, tuple):
            raise ArtifactHoldExpiryConflict(
                "replay claim request ledger must be immutable"
            )
        if not isinstance(self.replay_claim_results, tuple):
            raise ArtifactHoldExpiryConflict(
                "replay claim result ledger must be immutable"
            )
        if not isinstance(self.replay_current_authorities, tuple):
            raise ArtifactHoldExpiryConflict(
                "replay current authority ledger must be immutable"
            )
        if not isinstance(self.scan_authorities, tuple):
            raise ArtifactHoldExpiryConflict(
                "scan authority ledger must be immutable"
            )
        if not isinstance(self.scan_allocations, tuple):
            raise ArtifactHoldExpiryConflict(
                "scan allocation ledger must be immutable"
            )
        if len(self.scan_requests) != len(self.scan_results):
            raise ArtifactHoldExpiryConflict(
                "scan request/result ledger cardinality mismatch"
            )
        if len(self.replay_claim_requests) != len(self.replay_claim_results):
            raise ArtifactHoldExpiryConflict(
                "replay claim request/result ledger cardinality mismatch"
            )

        persisted_candidates: list[ArtifactHoldExpiryCandidate] = []
        scan_ids: set[str] = set()
        scan_evidence_by_lease: dict[
            str,
            tuple[
                ArtifactHoldExpiryScanRequest,
                ArtifactHoldExpiryScanResult,
                ArtifactHoldExpiryCandidate,
            ],
        ] = {}
        for request, result in zip(
            self.scan_requests,
            self.scan_results,
            strict=True,
        ):
            if not isinstance(request, ArtifactHoldExpiryScanRequest):
                raise ArtifactHoldExpiryConflict("scan ledger contains invalid request")
            if not isinstance(result, ArtifactHoldExpiryScanResult):
                raise ArtifactHoldExpiryConflict("scan ledger contains invalid result")
            result.validate_for(request)
            if request.scan_operation_id in scan_ids:
                raise ArtifactHoldExpiryConflict("scan ledger contains duplicate scan ID")
            scan_ids.add(request.scan_operation_id)
            for candidate in result.candidates:
                if candidate.candidate_lease_id in scan_evidence_by_lease:
                    raise ArtifactHoldExpiryConflict(
                        "scan candidate authority was duplicated"
                    )
                scan_evidence_by_lease[candidate.candidate_lease_id] = (
                    request,
                    result,
                    candidate,
                )
            persisted_candidates.extend(result.candidates)

        scan_authorities_by_lease: dict[str, ArtifactHoldExpiryScanAuthority] = {}
        for authority in self.scan_authorities:
            if not isinstance(authority, ArtifactHoldExpiryScanAuthority):
                raise ArtifactHoldExpiryConflict(
                    "scan authority ledger contains invalid seal"
                )
            authority.validate()
            if authority.candidate_lease_id in scan_authorities_by_lease:
                raise ArtifactHoldExpiryConflict(
                    "scan authority ledger contains duplicate seal"
                )
            scan_authorities_by_lease[authority.candidate_lease_id] = authority
        if set(scan_authorities_by_lease) != set(scan_evidence_by_lease):
            raise ArtifactHoldExpiryConflict(
                "scan authority seal set is incomplete"
            )
        for candidate_lease_id, evidence in scan_evidence_by_lease.items():
            scan_authorities_by_lease[candidate_lease_id].validate_against(*evidence)

        scan_allocations_by_lease: dict[
            str, _ArtifactHoldExpiryScanAllocation
        ] = {}
        for allocation in self.scan_allocations:
            if not isinstance(allocation, _ArtifactHoldExpiryScanAllocation):
                raise ArtifactHoldExpiryConflict(
                    "scan allocation ledger contains invalid proof"
                )
            allocation.validate()
            candidate_lease_id = allocation.candidate.candidate_lease_id
            if candidate_lease_id in scan_allocations_by_lease:
                raise ArtifactHoldExpiryConflict(
                    "scan allocation ledger contains duplicate proof"
                )
            scan_allocations_by_lease[candidate_lease_id] = allocation
        if set(scan_allocations_by_lease) != set(scan_evidence_by_lease):
            raise ArtifactHoldExpiryConflict(
                "scan allocation proof set is incomplete"
            )
        for candidate_lease_id, evidence in scan_evidence_by_lease.items():
            allocation = scan_allocations_by_lease[candidate_lease_id]
            allocation.validate_against(
                evidence[0],
                evidence[2],
                allocation.previous_fence_high_water,
            )

        replay_ids: set[str] = set()
        replay_candidates_by_expire_operation: dict[
            str, list[ArtifactHoldExpiryCandidate]
        ] = {}
        replay_claims_by_expire_operation: dict[
            str,
            list[
                tuple[
                    ArtifactHoldExpiryReplayClaimRequest,
                    ArtifactHoldExpiryReplayClaimResult,
                ]
            ],
        ] = {}
        for request, result in zip(
            self.replay_claim_requests,
            self.replay_claim_results,
            strict=True,
        ):
            if not isinstance(request, ArtifactHoldExpiryReplayClaimRequest):
                raise ArtifactHoldExpiryConflict(
                    "replay claim ledger contains invalid request"
                )
            if not isinstance(result, ArtifactHoldExpiryReplayClaimResult):
                raise ArtifactHoldExpiryConflict(
                    "replay claim ledger contains invalid result"
                )
            result.validate_for(request)
            if request.replay_operation_id in replay_ids:
                raise ArtifactHoldExpiryConflict(
                    "replay claim ledger contains duplicate operation ID"
                )
            if request.replay_operation_id in scan_ids:
                raise ArtifactHoldExpiryConflict(
                    "candidate issuance operation ID was reused across kinds"
                )
            replay_ids.add(request.replay_operation_id)
            persisted_candidates.append(result.candidate)
            replay_candidates_by_expire_operation.setdefault(
                request.expire_operation_id,
                [],
            ).append(result.candidate)
            replay_claims_by_expire_operation.setdefault(
                request.expire_operation_id,
                [],
            ).append((request, result))

        for candidates in replay_candidates_by_expire_operation.values():
            ordered = sorted(
                candidates,
                key=lambda candidate: candidate.candidate_fencing_token,
            )
            for previous, current in zip(ordered, ordered[1:], strict=False):
                if current.issued_at_ms < previous.lease_until_ms:
                    raise ArtifactHoldExpiryConflict(
                        "replay claim authority leases overlap"
                    )

        current_authorities_by_expire_operation: dict[
            str, ArtifactHoldExpiryReplayCurrentAuthority
        ] = {}
        for authority in self.replay_current_authorities:
            if not isinstance(authority, ArtifactHoldExpiryReplayCurrentAuthority):
                raise ArtifactHoldExpiryConflict(
                    "replay current authority ledger contains invalid pointer"
                )
            authority.validate()
            if authority.expire_operation_id in current_authorities_by_expire_operation:
                raise ArtifactHoldExpiryConflict(
                    "duplicate replay current authority pointer"
                )
            current_authorities_by_expire_operation[
                authority.expire_operation_id
            ] = authority
        if set(current_authorities_by_expire_operation) != set(
            replay_claims_by_expire_operation
        ):
            raise ArtifactHoldExpiryConflict(
                "replay current authority pointer set is incomplete"
            )
        for expire_operation_id, claims in replay_claims_by_expire_operation.items():
            ordered_claims = sorted(
                claims,
                key=lambda claim: claim[1].candidate.candidate_fencing_token,
            )
            current_request, current_result = ordered_claims[-1]
            current_authority = current_authorities_by_expire_operation[
                expire_operation_id
            ]
            current_authority.validate_against(current_request, current_result)
            if current_authority.revision != len(claims):
                raise ArtifactHoldExpiryConflict(
                    "replay current authority revision is not append-bound"
                )

        authority_tokens: set[str] = set()
        fences: set[int] = set()
        entry_candidates: list[ArtifactHoldExpiryCandidate] = []
        previous_fence = 0
        for entry in self.candidate_entries:
            if not isinstance(entry, ArtifactHoldExpiryCandidateLedgerEntry):
                raise ArtifactHoldExpiryConflict("candidate ledger contains invalid entry")
            entry.validate()
            candidate = entry.candidate
            if candidate.candidate_fencing_token <= previous_fence:
                raise ArtifactHoldExpiryConflict(
                    "candidate ledger append order is not fence-monotonic"
                )
            previous_fence = candidate.candidate_fencing_token
            for token in (
                candidate.candidate_lease_id,
                candidate.candidate_token,
            ):
                if token in authority_tokens:
                    raise ArtifactHoldExpiryConflict(
                        "global candidate bearer authority was reused"
                    )
                authority_tokens.add(token)
            if candidate.candidate_fencing_token in fences:
                raise ArtifactHoldExpiryConflict(
                    "global candidate fencing authority was reused"
                )
            if (
                candidate.candidate_fencing_token
                > self.candidate_fence_high_water
            ):
                raise ArtifactHoldExpiryConflict(
                    "candidate fence exceeds persisted high-water"
                )
            fences.add(candidate.candidate_fencing_token)
            entry_candidates.append(candidate)

        for allocation in self.scan_allocations:
            try:
                candidate_index = entry_candidates.index(allocation.candidate)
            except ValueError as exc:
                raise ArtifactHoldExpiryConflict(
                    "scan allocation candidate is absent from candidate ledger"
                ) from exc
            expected_previous_fence = (
                entry_candidates[candidate_index - 1].candidate_fencing_token
                if candidate_index
                else 0
            )
            if allocation.previous_fence_high_water != expected_previous_fence:
                raise ArtifactHoldExpiryConflict(
                    "scan allocation previous high-water is not persisted provenance"
                )

        if entry_candidates != sorted(
            persisted_candidates,
            key=lambda candidate: candidate.candidate_fencing_token,
        ):
            raise ArtifactHoldExpiryConflict(
                "candidate ledger append order does not project persisted authority"
            )

    def _record_scan(
        self,
        request: ArtifactHoldExpiryScanRequest,
        result: ArtifactHoldExpiryScanResult,
        *,
        allocations: tuple[_ArtifactHoldExpiryScanAllocation, ...] | None = None,
    ) -> ArtifactHoldExpiryLedgerState:
        self.validate()
        result.validate_for(request)
        if not isinstance(allocations, tuple):
            raise ArtifactHoldExpiryConflict(
                "scan persistence requires State-issued allocation proofs"
            )
        if len(allocations) != len(result.candidates):
            raise ArtifactHoldExpiryConflict(
                "scan allocation/result cardinality mismatch"
            )
        for allocation, candidate in zip(
            allocations,
            result.candidates,
            strict=True,
        ):
            if not isinstance(allocation, _ArtifactHoldExpiryScanAllocation):
                raise ArtifactHoldExpiryConflict(
                    "scan persistence received an invalid allocation proof"
                )
            allocation.validate()
            if allocation.request != request or allocation.candidate != candidate:
                raise ArtifactHoldExpiryConflict(
                    "scan allocation proof does not match result candidate"
                )
        for stored_request, stored_result in zip(
            self.scan_requests,
            self.scan_results,
            strict=True,
        ):
            if stored_request.scan_operation_id != request.scan_operation_id:
                continue
            if stored_request == request and stored_result == result:
                persisted_allocations = tuple(
                    allocation
                    for candidate in stored_result.candidates
                    for allocation in self.scan_allocations
                    if allocation.candidate == candidate
                )
                if persisted_allocations != allocations:
                    raise ArtifactHoldExpiryConflict(
                        "scan retry does not carry the persisted allocation proof"
                    )
                return self
            raise ArtifactHoldExpiryConflict(
                "scan ID already has different persisted request or result"
            )

        authority_tokens = {
            token
            for entry in self.candidate_entries
            for token in (
                entry.candidate.candidate_lease_id,
                entry.candidate.candidate_token,
            )
        }
        next_high_water = self.candidate_fence_high_water
        new_entries: list[ArtifactHoldExpiryCandidateLedgerEntry] = []
        new_scan_authorities: list[ArtifactHoldExpiryScanAuthority] = []
        for candidate, allocation in zip(
            result.candidates,
            allocations,
            strict=True,
        ):
            allocation.validate_against(request, candidate, next_high_water)
            if (
                candidate.candidate_lease_id in authority_tokens
                or candidate.candidate_token in authority_tokens
                or candidate.candidate_lease_id == candidate.candidate_token
                or candidate.candidate_fencing_token <= next_high_water
            ):
                raise ArtifactHoldExpiryConflict(
                    "global candidate authority was reused or regressed"
                )
            authority_tokens.add(candidate.candidate_lease_id)
            authority_tokens.add(candidate.candidate_token)
            next_high_water = candidate.candidate_fencing_token
            new_entries.append(
                ArtifactHoldExpiryCandidateLedgerEntry(candidate=candidate)
            )
            new_scan_authorities.append(
                ArtifactHoldExpiryScanAuthority.create(
                    request=request,
                    result=result,
                    candidate=candidate,
                )
            )

        updated = replace(
            self,
            candidate_fence_high_water=next_high_water,
            scan_requests=self.scan_requests + (request,),
            scan_results=self.scan_results + (result,),
            candidate_entries=self.candidate_entries + tuple(new_entries),
            scan_authorities=self.scan_authorities + tuple(new_scan_authorities),
            scan_allocations=self.scan_allocations + allocations,
        )
        updated.validate()
        return updated

    def _record_replay_claim(
        self,
        request: ArtifactHoldExpiryReplayClaimRequest,
        result: ArtifactHoldExpiryReplayClaimResult,
    ) -> ArtifactHoldExpiryLedgerState:
        self.validate()
        result.validate_for(request)
        for stored_request, stored_result in zip(
            self.replay_claim_requests,
            self.replay_claim_results,
            strict=True,
        ):
            if stored_request.replay_operation_id != request.replay_operation_id:
                continue
            if stored_request == request and stored_result == result:
                return self
            raise ArtifactHoldExpiryConflict(
                "replay claim ID already has different persisted request or result"
            )

        authority_tokens = {
            token
            for entry in self.candidate_entries
            for token in (
                entry.candidate.candidate_lease_id,
                entry.candidate.candidate_token,
            )
        }
        candidate = result.candidate
        if (
            candidate.candidate_lease_id in authority_tokens
            or candidate.candidate_token in authority_tokens
            or candidate.candidate_lease_id == candidate.candidate_token
            or candidate.candidate_fencing_token <= self.candidate_fence_high_water
        ):
            raise ArtifactHoldExpiryConflict(
                "replay claim authority was reused or regressed"
            )
        current_authorities = list(self.replay_current_authorities)
        existing_authorities = [
            authority
            for authority in current_authorities
            if authority.expire_operation_id == request.expire_operation_id
        ]
        if len(existing_authorities) > 1:
            raise ArtifactHoldExpiryConflict(
                "duplicate replay current authority pointer"
            )
        revision = existing_authorities[0].revision + 1 if existing_authorities else 1
        current_authority = ArtifactHoldExpiryReplayCurrentAuthority.create(
            request=request,
            result=result,
            revision=revision,
        )
        if existing_authorities:
            current_authorities[
                current_authorities.index(existing_authorities[0])
            ] = current_authority
        else:
            current_authorities.append(current_authority)
        updated = replace(
            self,
            candidate_fence_high_water=candidate.candidate_fencing_token,
            replay_claim_requests=self.replay_claim_requests + (request,),
            replay_claim_results=self.replay_claim_results + (result,),
            candidate_entries=self.candidate_entries
            + (ArtifactHoldExpiryCandidateLedgerEntry(candidate=candidate),),
            replay_current_authorities=tuple(current_authorities),
        )
        updated.validate()
        return updated

    def consume_candidate(
        self,
        request: ArtifactHoldExpiryRequest,
        *,
        consumed_at_ms: int,
    ) -> ArtifactHoldExpiryLedgerState:
        self.validate()
        request.validate()
        _require_integer(consumed_at_ms, "candidate consumed timestamp", minimum=0)
        entry = self.candidate_entry_for(request.candidate_lease_id)
        if entry.consumed:
            raise ArtifactHoldExpiryConflict("candidate authority was already consumed")
        _require_candidate_request_binding(request, entry.candidate)
        entry_index = self.candidate_entries.index(entry)
        consumed_entry = replace(
            entry,
            consumed_by_expire_operation_id=request.expire_operation_id,
            consumed_at_ms=consumed_at_ms,
        )
        entries = list(self.candidate_entries)
        entries[entry_index] = consumed_entry
        updated = replace(self, candidate_entries=tuple(entries))
        updated.validate()
        return updated

    def candidate_entry_for(
        self,
        candidate_lease_id: str,
    ) -> ArtifactHoldExpiryCandidateLedgerEntry:
        _require_candidate_token(candidate_lease_id, "candidate lease ID")
        matches = tuple(
            entry
            for entry in self.candidate_entries
            if entry.candidate.candidate_lease_id == candidate_lease_id
        )
        if len(matches) != 1:
            raise ArtifactHoldExpiryConflict(
                "authoritative candidate ledger entry is missing"
            )
        return matches[0]

    def replay_claim_for_candidate(
        self,
        candidate: ArtifactHoldExpiryCandidate,
    ) -> tuple[
        ArtifactHoldExpiryReplayClaimRequest,
        ArtifactHoldExpiryReplayClaimResult,
    ] | None:
        matches = tuple(
            (request, result)
            for request, result in zip(
                self.replay_claim_requests,
                self.replay_claim_results,
                strict=True,
            )
            if result.candidate == candidate
        )
        if len(matches) > 1:
            raise ArtifactHoldExpiryConflict(
                "replay candidate has duplicate claim evidence"
            )
        return matches[0] if matches else None

    def replay_claims_for_expire_operation(
        self,
        expire_operation_id: str,
    ) -> tuple[
        tuple[
            ArtifactHoldExpiryReplayClaimRequest,
            ArtifactHoldExpiryReplayClaimResult,
        ],
        ...,
    ]:
        _require_sha256(expire_operation_id, "expire operation ID")
        return tuple(
            (request, result)
            for request, result in zip(
                self.replay_claim_requests,
                self.replay_claim_results,
                strict=True,
            )
            if request.expire_operation_id == expire_operation_id
        )

    def current_replay_claim_for_expire_operation(
        self,
        expire_operation_id: str,
    ) -> tuple[
        ArtifactHoldExpiryReplayClaimRequest,
        ArtifactHoldExpiryReplayClaimResult,
    ] | None:
        claims = self.replay_claims_for_expire_operation(expire_operation_id)
        if not claims:
            return None
        authorities = tuple(
            authority
            for authority in self.replay_current_authorities
            if authority.expire_operation_id == expire_operation_id
        )
        if len(authorities) != 1:
            raise ArtifactHoldExpiryConflict(
                "replay current authority pointer is missing or duplicated"
            )
        authority = authorities[0]
        matches = tuple(
            claim
            for claim in claims
            if claim[0].replay_operation_id == authority.replay_operation_id
        )
        if len(matches) != 1:
            raise ArtifactHoldExpiryConflict(
                "replay current authority pointer has no matching claim"
            )
        authority.validate_against(*matches[0])
        return matches[0]

    def is_replay_candidate(self, candidate: ArtifactHoldExpiryCandidate) -> bool:
        return self.replay_claim_for_candidate(candidate) is not None

    def require_replay_authority(
        self,
        request: ArtifactHoldExpiryRequest,
        *,
        authenticated_principal_id: str,
        authenticated_instance_id: str,
        authenticated_fencing_token: int,
        server_now_ms: int,
    ) -> _VerifiedArtifactHoldExpiryReplayAuthority:
        self.validate()
        request.validate()
        _require_stable_text(
            authenticated_principal_id,
            "authenticated replay principal",
        )
        _require_stable_text(
            authenticated_instance_id,
            "authenticated replay instance",
        )
        _require_integer(
            authenticated_fencing_token,
            "authenticated replay fence",
            minimum=1,
        )
        _require_integer(server_now_ms, "server_now_ms", minimum=0)
        committed_matches = tuple(
            entry
            for entry in self.candidate_entries
            if entry.consumed_by_expire_operation_id == request.expire_operation_id
        )
        if len(committed_matches) != 1:
            raise ArtifactHoldExpiryConflict(
                "committed candidate authority is missing or duplicated"
            )
        committed_candidate = committed_matches[0].candidate
        if (
            request.artifact_id != committed_candidate.artifact_id
            or request.hold_id != committed_candidate.hold_id
            or request.expected_hold_digest
            != committed_candidate.expected_hold_digest
            or request.expected_artifact_version
            != committed_candidate.expected_artifact_version
            or request.observed_expires_ms != committed_candidate.observed_expires_ms
        ):
            raise ArtifactHoldExpiryConflict(
                "replay request does not match committed business identity"
            )
        if authenticated_principal_id != committed_candidate.owner_principal_id:
            raise ArtifactHoldExpiryConflict(
                "authenticated replay principal does not own committed authority"
            )
        current_replay_claim = self.current_replay_claim_for_expire_operation(
            request.expire_operation_id
        )
        if request.candidate_lease_id == committed_candidate.candidate_lease_id:
            if current_replay_claim is not None:
                raise ArtifactHoldExpiryConflict(
                    "committed replay authority was superseded"
                )
            _require_candidate_request_binding(request, committed_candidate)
            if (
                authenticated_fencing_token
                < committed_candidate.candidate_fencing_token
            ):
                raise ArtifactHoldExpiryConflict(
                    "authenticated replay fence is stale for committed authority"
                )
            if (
                authenticated_fencing_token
                > committed_candidate.candidate_fencing_token
            ):
                raise ArtifactHoldExpiryConflict(
                    "higher-fence replay requires a persisted candidate authority"
                )
            if authenticated_instance_id != committed_candidate.owner_instance_id:
                raise ArtifactHoldExpiryConflict(
                    "authenticated replay instance does not own committed fence"
                )
            return _VerifiedArtifactHoldExpiryReplayAuthority(
                candidate=committed_candidate,
                _seal=_REPLAY_AUTHORITY_SEAL,
            )

        replay_entry = self.candidate_entry_for(request.candidate_lease_id)
        if replay_entry.consumed:
            raise ArtifactHoldExpiryConflict(
                "replay candidate authority was already consumed"
            )
        replay_candidate = replay_entry.candidate
        claim = self.replay_claim_for_candidate(replay_candidate)
        if claim is None:
            raise ArtifactHoldExpiryConflict(
                "persisted replay candidate claim is missing"
            )
        claim_request, claim_result = claim
        claim_result.validate_for(claim_request)
        if current_replay_claim is None or claim_result != current_replay_claim[1]:
            raise ArtifactHoldExpiryConflict(
                "persisted replay candidate authority was superseded"
            )
        if (
            claim_result.expire_operation_id != request.expire_operation_id
            or replay_candidate.candidate_fencing_token
            <= committed_candidate.candidate_fencing_token
        ):
            raise ArtifactHoldExpiryConflict(
                "replay candidate is not a newer authority for this commit"
            )
        _require_candidate_request_binding(request, replay_candidate)
        if (
            replay_candidate.owner_principal_id != authenticated_principal_id
            or replay_candidate.owner_instance_id != authenticated_instance_id
            or replay_candidate.candidate_fencing_token
            != authenticated_fencing_token
        ):
            raise ArtifactHoldExpiryConflict(
                "authenticated replay context does not own persisted candidate"
            )
        if not (
            replay_candidate.issued_at_ms
            <= server_now_ms
            < replay_candidate.lease_until_ms
        ):
            raise ArtifactHoldExpiryConflict(
                "persisted replay candidate lease is not live"
            )
        return _VerifiedArtifactHoldExpiryReplayAuthority(
            candidate=replay_candidate,
            _seal=_REPLAY_AUTHORITY_SEAL,
        )


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryRequest:
    schema_version: str
    operation: ArtifactHoldExpiryOperation
    expire_operation_id: str
    artifact_id: str
    hold_id: str
    expected_hold_digest: str
    expected_artifact_version: int
    observed_expires_ms: int
    scan_operation_id: str
    candidate_lease_id: str
    candidate_fencing_token: int
    candidate_token: str
    idempotency_key: str
    request_digest: str

    @classmethod
    def create(
        cls,
        *,
        candidate: ArtifactHoldExpiryCandidate,
    ) -> ArtifactHoldExpiryRequest:
        candidate.validate()
        operation_id = artifact_hold_expiry_operation_id(
            artifact_id=candidate.artifact_id,
            hold_id=candidate.hold_id,
            expected_hold_digest=candidate.expected_hold_digest,
            expected_artifact_version=candidate.expected_artifact_version,
            observed_expires_ms=candidate.observed_expires_ms,
        )
        request = cls(
            schema_version="1",
            operation=ArtifactHoldExpiryOperation.EXPIRE,
            expire_operation_id=operation_id,
            artifact_id=candidate.artifact_id,
            hold_id=candidate.hold_id,
            expected_hold_digest=candidate.expected_hold_digest,
            expected_artifact_version=candidate.expected_artifact_version,
            observed_expires_ms=candidate.observed_expires_ms,
            scan_operation_id=candidate.scan_operation_id,
            candidate_lease_id=candidate.candidate_lease_id,
            candidate_fencing_token=candidate.candidate_fencing_token,
            candidate_token=candidate.candidate_token,
            idempotency_key=operation_id,
            request_digest="",
        )
        created = replace(
            request,
            request_digest=artifact_hold_expiry_request_digest(request),
        )
        created.validate()
        return created

    @classmethod
    def from_canonical_json(cls, wire: bytes) -> ArtifactHoldExpiryRequest:
        value = _parse_canonical_object(wire, "artifact hold expiry request")
        _require_exact_fields(
            value,
            {
                "schemaVersion",
                "operation",
                "expireOperationId",
                "artifactId",
                "holdId",
                "expectedHoldDigest",
                "expectedArtifactVersion",
                "observedExpiresMs",
                "scanOperationId",
                "candidateLeaseId",
                "candidateFencingToken",
                "candidateToken",
                "idempotencyKey",
                "requestDigest",
            },
            "artifact hold expiry request",
        )
        operation = _parse_operation(value["operation"], "expiry request operation")
        request = cls(
            schema_version=value["schemaVersion"],
            operation=operation,
            expire_operation_id=value["expireOperationId"],
            artifact_id=value["artifactId"],
            hold_id=value["holdId"],
            expected_hold_digest=value["expectedHoldDigest"],
            expected_artifact_version=value["expectedArtifactVersion"],
            observed_expires_ms=value["observedExpiresMs"],
            scan_operation_id=value["scanOperationId"],
            candidate_lease_id=value["candidateLeaseId"],
            candidate_fencing_token=value["candidateFencingToken"],
            candidate_token=value["candidateToken"],
            idempotency_key=value["idempotencyKey"],
            request_digest=value["requestDigest"],
        )
        request.validate()
        return request

    def validate(self) -> None:
        if self.schema_version != "1":
            raise ArtifactHoldExpiryConflict("unsupported artifact hold expiry schema")
        if self.operation is not ArtifactHoldExpiryOperation.EXPIRE:
            raise ArtifactHoldExpiryConflict("artifact hold expiry operation must be EXPIRE")
        _require_sha256(self.expire_operation_id, "expire_operation_id")
        _require_stable_text(self.artifact_id, "artifact_id")
        _require_stable_text(self.hold_id, "hold_id")
        _require_sha256(self.expected_hold_digest, "expected_hold_digest")
        _require_integer(
            self.expected_artifact_version,
            "expected_artifact_version",
            minimum=1,
        )
        _require_integer(self.observed_expires_ms, "observed_expires_ms", minimum=0)
        _require_sha256(self.scan_operation_id, "scan_operation_id")
        _require_candidate_token(self.candidate_lease_id, "candidate_lease_id")
        _require_integer(
            self.candidate_fencing_token,
            "candidate_fencing_token",
            minimum=1,
        )
        _require_candidate_token(self.candidate_token, "candidate_token")
        if self.candidate_lease_id == self.candidate_token:
            raise ArtifactHoldExpiryConflict(
                "candidate lease ID and candidate token must differ"
            )
        _require_sha256(self.idempotency_key, "idempotency_key")
        _require_sha256(self.request_digest, "request_digest")
        expected_operation_id = artifact_hold_expiry_operation_id(
            artifact_id=self.artifact_id,
            hold_id=self.hold_id,
            expected_hold_digest=self.expected_hold_digest,
            expected_artifact_version=self.expected_artifact_version,
            observed_expires_ms=self.observed_expires_ms,
        )
        if self.expire_operation_id != expected_operation_id:
            raise ArtifactHoldExpiryConflict("expire_operation_id does not match due tuple")
        if self.idempotency_key != self.expire_operation_id:
            raise ArtifactHoldExpiryConflict(
                "idempotency_key must equal expire_operation_id"
            )
        if self.request_digest != artifact_hold_expiry_request_digest(self):
            raise ArtifactHoldExpiryConflict("request_digest mismatch")

    def to_wire_dict(self) -> dict[str, object]:
        return _expire_request_payload(self) | {"requestDigest": self.request_digest}

    def canonical_json(self) -> bytes:
        return rfc8785.dumps(self.to_wire_dict())


@dataclass(frozen=True, slots=True)
class ArtifactHoldState:
    artifact_id: str
    artifact_version: int
    artifact_status: ArtifactLifecycleStatus
    hold_id: str
    hold_digest: str
    hold_status: ArtifactHoldStatus
    expires_ms: int | None
    active_hold_ids: frozenset[str]
    active_ref_count: int
    minimum_delete_at_ms: int | None
    due_indexed: bool
    due_score_ms: int | None
    candidate: ArtifactHoldExpiryCandidate | None

    def retention_locked_at(self, server_now_ms: int) -> bool:
        _require_integer(server_now_ms, "server_now_ms", minimum=0)
        minimum_time_lock = (
            self.minimum_delete_at_ms is not None
            and server_now_ms < self.minimum_delete_at_ms
        )
        return bool(self.active_hold_ids) or self.active_ref_count > 0 or minimum_time_lock

    def validate(self) -> None:
        _require_stable_text(self.artifact_id, "artifact_id")
        _require_integer(self.artifact_version, "artifact_version", minimum=1)
        if not isinstance(self.artifact_status, ArtifactLifecycleStatus):
            raise ArtifactHoldExpiryConflict("unknown artifact lifecycle status")
        _require_stable_text(self.hold_id, "hold_id")
        _require_sha256(self.hold_digest, "hold_digest")
        if not isinstance(self.hold_status, ArtifactHoldStatus):
            raise ArtifactHoldExpiryConflict("unknown hold status")
        if self.expires_ms is not None:
            _require_integer(self.expires_ms, "expires_ms", minimum=0)
        if not isinstance(self.active_hold_ids, frozenset):
            raise ArtifactHoldExpiryConflict("active_hold_ids must be a frozenset")
        for hold_id in self.active_hold_ids:
            _require_stable_text(hold_id, "active hold ID")
        _require_integer(self.active_ref_count, "active_ref_count", minimum=0)
        if self.minimum_delete_at_ms is not None:
            _require_integer(
                self.minimum_delete_at_ms,
                "minimum_delete_at_ms",
                minimum=0,
            )
        if type(self.due_indexed) is not bool:
            raise ArtifactHoldExpiryConflict("due_indexed must be a boolean")
        if self.due_score_ms is not None:
            _require_integer(self.due_score_ms, "due_score_ms", minimum=0)

        is_finite_active = (
            self.hold_status is ArtifactHoldStatus.ACTIVE
            and self.expires_ms is not None
        )
        if self.hold_status is ArtifactHoldStatus.ACTIVE:
            if self.hold_id not in self.active_hold_ids:
                raise ArtifactHoldExpiryConflict(
                    "ACTIVE hold is absent from active hold set"
                )
        elif self.hold_id in self.active_hold_ids:
            raise ArtifactHoldExpiryConflict(
                "terminal hold remains in active retention set"
            )
        if is_finite_active:
            if not self.due_indexed or self.due_score_ms != self.expires_ms:
                raise ArtifactHoldExpiryConflict(
                    "finite ACTIVE hold due index is missing or changed"
                )
        elif self.due_indexed or self.due_score_ms is not None:
            raise ArtifactHoldExpiryConflict(
                "non-expiring or terminal hold must not remain in due index"
            )

        if self.candidate is not None:
            self.candidate.validate()
            comparisons = (
                self.candidate.artifact_id == self.artifact_id,
                self.candidate.hold_id == self.hold_id,
                self.candidate.expected_hold_digest == self.hold_digest,
                self.candidate.expected_artifact_version == self.artifact_version,
                self.candidate.observed_expires_ms == self.expires_ms,
                is_finite_active,
                self.due_indexed,
                self.due_score_ms == self.expires_ms,
            )
            if not all(comparisons):
                raise ArtifactHoldExpiryConflict(
                    "candidate ledger is not bound to current due tuple"
                )
        elif self.hold_status is not ArtifactHoldStatus.ACTIVE:
            return


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryResult:
    schema_version: str
    expire_operation_id: str
    artifact_id: str
    hold_id: str
    request_digest: str
    result_code: str
    hold_status: ArtifactHoldStatus
    artifact_version: int
    retention_locked: bool
    audit_event_type: str
    outbox_event_type: str
    result_digest: str

    @classmethod
    def create(
        cls,
        *,
        expire_operation_id: str,
        artifact_id: str,
        hold_id: str,
        request_digest: str,
        artifact_version: int,
        retention_locked: bool,
    ) -> ArtifactHoldExpiryResult:
        result = cls(
            schema_version="1",
            expire_operation_id=expire_operation_id,
            artifact_id=artifact_id,
            hold_id=hold_id,
            request_digest=request_digest,
            result_code="EXPIRED",
            hold_status=ArtifactHoldStatus.EXPIRED,
            artifact_version=artifact_version,
            retention_locked=retention_locked,
            audit_event_type="ArtifactHoldExpired",
            outbox_event_type="ArtifactHoldExpired",
            result_digest="",
        )
        created = replace(result, result_digest=_expire_result_digest(result))
        created.validate()
        return created

    @classmethod
    def from_canonical_json(cls, wire: bytes) -> ArtifactHoldExpiryResult:
        value = _parse_canonical_object(wire, "stored expiry result")
        expected_fields = {
            "schemaVersion",
            "expireOperationId",
            "artifactId",
            "holdId",
            "requestDigest",
            "resultCode",
            "holdStatus",
            "artifactVersion",
            "retentionLocked",
            "auditEventType",
            "outboxEventType",
            "resultDigest",
        }
        if set(value) != expected_fields:
            raise ArtifactHoldExpiryConflict(
                "stored expiry result has missing or extra fields"
            )
        try:
            hold_status = ArtifactHoldStatus(value["holdStatus"])
        except (TypeError, ValueError) as exc:
            raise ArtifactHoldExpiryConflict(
                "stored expiry result has unknown holdStatus"
            ) from exc
        result = cls(
            schema_version=value["schemaVersion"],
            expire_operation_id=value["expireOperationId"],
            artifact_id=value["artifactId"],
            hold_id=value["holdId"],
            request_digest=value["requestDigest"],
            result_code=value["resultCode"],
            hold_status=hold_status,
            artifact_version=value["artifactVersion"],
            retention_locked=value["retentionLocked"],
            audit_event_type=value["auditEventType"],
            outbox_event_type=value["outboxEventType"],
            result_digest=value["resultDigest"],
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1":
            raise ArtifactHoldExpiryConflict("unsupported expiry result schema")
        _require_sha256(self.expire_operation_id, "result.expire_operation_id")
        _require_stable_text(self.artifact_id, "result.artifact_id")
        _require_stable_text(self.hold_id, "result.hold_id")
        _require_sha256(self.request_digest, "result.request_digest")
        if self.result_code != "EXPIRED":
            raise ArtifactHoldExpiryConflict("invalid expiry result code")
        if self.hold_status is not ArtifactHoldStatus.EXPIRED:
            raise ArtifactHoldExpiryConflict("invalid stored expiry hold status")
        _require_integer(self.artifact_version, "result.artifact_version", minimum=1)
        if type(self.retention_locked) is not bool:
            raise ArtifactHoldExpiryConflict(
                "result.retention_locked must be a boolean"
            )
        if (
            self.audit_event_type != "ArtifactHoldExpired"
            or self.outbox_event_type != "ArtifactHoldExpired"
        ):
            raise ArtifactHoldExpiryConflict("expiry result sink mismatch")
        _require_sha256(self.result_digest, "result.result_digest")
        if self.result_digest != _expire_result_digest(self):
            raise ArtifactHoldExpiryConflict("stored expiry result digest mismatch")

    def to_wire_dict(self) -> dict[str, object]:
        return _expire_result_payload(self) | {"resultDigest": self.result_digest}

    def canonical_json(self) -> bytes:
        return rfc8785.dumps(self.to_wire_dict())


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryCommit:
    schema_version: str
    expire_operation_id: str
    request_digest: str
    expected_hold_digest: str
    authorized_candidate_digest: str
    authorized_reaper_principal_id: str
    authorized_reaper_fencing_token: int
    previous_artifact_version: int
    remaining_active_hold_count: int
    active_ref_count: int
    minimum_delete_at_ms: int | None
    committed_at_ms: int
    result_json: bytes
    commit_digest: str

    @classmethod
    def create(
        cls,
        *,
        request: ArtifactHoldExpiryRequest,
        result: ArtifactHoldExpiryResult,
        before_state: ArtifactHoldState,
        updated_state: ArtifactHoldState,
        committed_at_ms: int,
    ) -> ArtifactHoldExpiryCommit:
        request.validate()
        result.validate()
        before_state.validate()
        updated_state.validate()
        _require_integer(committed_at_ms, "committed_at_ms", minimum=0)
        candidate = before_state.candidate
        if candidate is None:
            raise ArtifactHoldExpiryConflict(
                "commit evidence requires the consumed candidate ledger"
            )
        _require_candidate_request_binding(request, candidate)
        if (
            result.expire_operation_id != request.expire_operation_id
            or result.request_digest != request.request_digest
            or result.artifact_id != request.artifact_id
            or result.hold_id != request.hold_id
        ):
            raise ArtifactHoldExpiryConflict("result does not match expiry request")
        if (
            before_state.artifact_id != updated_state.artifact_id
            or before_state.hold_id != updated_state.hold_id
            or before_state.artifact_status
            in {ArtifactLifecycleStatus.DELETING, ArtifactLifecycleStatus.DELETED}
            or updated_state.artifact_status != before_state.artifact_status
            or before_state.hold_status is not ArtifactHoldStatus.ACTIVE
            or before_state.hold_id not in before_state.active_hold_ids
            or not before_state.due_indexed
            or before_state.due_score_ms != before_state.expires_ms
            or before_state.expires_ms is None
            or committed_at_ms < before_state.expires_ms
            or candidate.issued_at_ms > committed_at_ms
            or committed_at_ms >= candidate.lease_until_ms
            or updated_state.artifact_version != before_state.artifact_version + 1
            or updated_state.hold_digest != before_state.hold_digest
            or updated_state.expires_ms != before_state.expires_ms
            or updated_state.active_ref_count != before_state.active_ref_count
            or updated_state.minimum_delete_at_ms
            != before_state.minimum_delete_at_ms
            or updated_state.active_hold_ids
            != before_state.active_hold_ids - {before_state.hold_id}
            or result.artifact_version != updated_state.artifact_version
            or updated_state.hold_status is not ArtifactHoldStatus.EXPIRED
            or updated_state.due_indexed
            or updated_state.due_score_ms is not None
            or updated_state.candidate is not None
        ):
            raise ArtifactHoldExpiryConflict("invalid expiry transition evidence")
        expected_retention = updated_state.retention_locked_at(committed_at_ms)
        if result.retention_locked != expected_retention:
            raise ArtifactHoldExpiryConflict(
                "result retention projection contradicts transition evidence"
            )
        commit = cls(
            schema_version="1",
            expire_operation_id=request.expire_operation_id,
            request_digest=request.request_digest,
            expected_hold_digest=request.expected_hold_digest,
            authorized_candidate_digest=candidate.candidate_digest,
            authorized_reaper_principal_id=candidate.owner_principal_id,
            authorized_reaper_fencing_token=candidate.candidate_fencing_token,
            previous_artifact_version=before_state.artifact_version,
            remaining_active_hold_count=len(updated_state.active_hold_ids),
            active_ref_count=updated_state.active_ref_count,
            minimum_delete_at_ms=updated_state.minimum_delete_at_ms,
            committed_at_ms=committed_at_ms,
            result_json=result.canonical_json(),
            commit_digest="",
        )
        created = replace(commit, commit_digest=_commit_digest(commit))
        created._validate_result_for_request(
            request,
            authenticated_reaper_principal_id=candidate.owner_principal_id,
            authenticated_reaper_fencing_token=candidate.candidate_fencing_token,
            authenticated_component_type=ARTIFACT_HOLD_REAPER_COMPONENT_TYPE,
            authenticated_subject=ARTIFACT_HOLD_EXPIRY_SUBJECT,
        )
        return created

    @classmethod
    def from_canonical_json(cls, wire: bytes) -> ArtifactHoldExpiryCommit:
        value = _parse_canonical_object(wire, "artifact hold expiry commit")
        _require_exact_fields(
            value,
            {
                "schemaVersion",
                "expireOperationId",
                "requestDigest",
                "expectedHoldDigest",
                "authorizedCandidateDigest",
                "authorizedReaperPrincipalId",
                "authorizedReaperFencingToken",
                "previousArtifactVersion",
                "remainingActiveHoldCount",
                "activeRefCount",
                "minimumDeleteAtMs",
                "committedAtMs",
                "resultJson",
                "commitDigest",
            },
            "artifact hold expiry commit",
        )
        result_json = value["resultJson"]
        if not isinstance(result_json, str):
            raise ArtifactHoldExpiryConflict("commit resultJson must be a string")
        commit = cls(
            schema_version=value["schemaVersion"],
            expire_operation_id=value["expireOperationId"],
            request_digest=value["requestDigest"],
            expected_hold_digest=value["expectedHoldDigest"],
            authorized_candidate_digest=value["authorizedCandidateDigest"],
            authorized_reaper_principal_id=value["authorizedReaperPrincipalId"],
            authorized_reaper_fencing_token=value[
                "authorizedReaperFencingToken"
            ],
            previous_artifact_version=value["previousArtifactVersion"],
            remaining_active_hold_count=value["remainingActiveHoldCount"],
            active_ref_count=value["activeRefCount"],
            minimum_delete_at_ms=value["minimumDeleteAtMs"],
            committed_at_ms=value["committedAtMs"],
            result_json=result_json.encode("utf-8"),
            commit_digest=value["commitDigest"],
        )  # type: ignore[arg-type]
        commit.validate()
        return commit

    def validate(self) -> None:
        if self.schema_version != "1":
            raise ArtifactHoldExpiryConflict("unsupported expiry commit schema")
        _require_sha256(self.expire_operation_id, "commit expire_operation_id")
        _require_sha256(self.request_digest, "commit request_digest")
        _require_sha256(self.expected_hold_digest, "commit expected_hold_digest")
        _require_sha256(
            self.authorized_candidate_digest,
            "commit authorized_candidate_digest",
        )
        _require_stable_text(
            self.authorized_reaper_principal_id,
            "commit authorized reaper principal",
        )
        _require_integer(
            self.authorized_reaper_fencing_token,
            "commit authorized reaper fencing token",
            minimum=1,
        )
        _require_integer(
            self.previous_artifact_version,
            "commit previous_artifact_version",
            minimum=1,
        )
        _require_integer(
            self.remaining_active_hold_count,
            "commit remaining_active_hold_count",
            minimum=0,
        )
        _require_integer(self.active_ref_count, "commit active_ref_count", minimum=0)
        if self.minimum_delete_at_ms is not None:
            _require_integer(
                self.minimum_delete_at_ms,
                "commit minimum_delete_at_ms",
                minimum=0,
            )
        _require_integer(self.committed_at_ms, "commit committed_at_ms", minimum=0)
        if not isinstance(self.result_json, bytes):
            raise ArtifactHoldExpiryConflict("commit result_json must be exact bytes")
        ArtifactHoldExpiryResult.from_canonical_json(self.result_json)
        _require_sha256(self.commit_digest, "commit_digest")
        if self.commit_digest != _commit_digest(self):
            raise ArtifactHoldExpiryConflict("commit_digest mismatch")

    def _validate_result_for_request(
        self,
        request: ArtifactHoldExpiryRequest,
        *,
        authenticated_reaper_principal_id: str,
        authenticated_reaper_fencing_token: int,
        authenticated_component_type: str,
        authenticated_subject: str,
        verified_replay_authority: (
            _VerifiedArtifactHoldExpiryReplayAuthority | None
        ) = None,
    ) -> ArtifactHoldExpiryResult:
        request.validate()
        self.validate()
        _require_stable_text(
            authenticated_reaper_principal_id,
            "authenticated reaper principal",
        )
        _require_integer(
            authenticated_reaper_fencing_token,
            "authenticated reaper fencing token",
            minimum=1,
        )
        _require_stable_text(
            authenticated_component_type,
            "authenticated component type",
        )
        _require_stable_text(authenticated_subject, "authenticated subject")
        if authenticated_component_type != ARTIFACT_HOLD_REAPER_COMPONENT_TYPE:
            raise ArtifactHoldExpiryConflict(
                "only artifact-hold-reaper may replay hold expiry"
            )
        if authenticated_subject != ARTIFACT_HOLD_EXPIRY_SUBJECT:
            raise ArtifactHoldExpiryConflict(
                "hold expiry replay used a forbidden subject"
            )
        replay_candidate: ArtifactHoldExpiryCandidate | None = None
        if verified_replay_authority is not None:
            if not isinstance(
                verified_replay_authority,
                _VerifiedArtifactHoldExpiryReplayAuthority,
            ):
                raise ArtifactHoldExpiryConflict(
                    "replay authority proof is not ledger-verified"
                )
            verified_replay_authority.validate()
            replay_candidate = verified_replay_authority.candidate
        if (
            self.expire_operation_id != request.expire_operation_id
            or self.request_digest != request.request_digest
            or self.expected_hold_digest != request.expected_hold_digest
            or self.previous_artifact_version != request.expected_artifact_version
        ):
            raise ArtifactHoldExpiryConflict("stored commit does not match request")
        if replay_candidate is not None and (
            replay_candidate.candidate_fencing_token
            == self.authorized_reaper_fencing_token
            and replay_candidate.candidate_digest
            != self.authorized_candidate_digest
        ):
            raise ArtifactHoldExpiryConflict(
                "stored commit candidate digest does not match persisted candidate"
            )
        if authenticated_reaper_principal_id != self.authorized_reaper_principal_id:
            raise ArtifactHoldExpiryConflict(
                "authenticated reaper principal cannot replay this commit"
            )
        if authenticated_reaper_fencing_token < self.authorized_reaper_fencing_token:
            raise ArtifactHoldExpiryConflict(
                "authenticated reaper fence is stale for this commit"
            )
        if authenticated_reaper_fencing_token > self.authorized_reaper_fencing_token:
            if replay_candidate is None:
                raise ArtifactHoldExpiryConflict(
                    "higher-fence replay requires ledger-verified authority"
                )
            _require_candidate_request_binding(request, replay_candidate)
            if (
                replay_candidate.owner_principal_id
                != authenticated_reaper_principal_id
                or replay_candidate.candidate_fencing_token
                != authenticated_reaper_fencing_token
            ):
                raise ArtifactHoldExpiryConflict(
                    "verified replay candidate does not bind authenticated authority"
                )
        result = ArtifactHoldExpiryResult.from_canonical_json(self.result_json)
        if (
            result.expire_operation_id != request.expire_operation_id
            or result.request_digest != request.request_digest
            or result.artifact_id != request.artifact_id
            or result.hold_id != request.hold_id
            or result.artifact_version != self.previous_artifact_version + 1
        ):
            raise ArtifactHoldExpiryConflict(
                "stored result does not match request or transition evidence"
            )
        minimum_lock = (
            self.minimum_delete_at_ms is not None
            and self.committed_at_ms < self.minimum_delete_at_ms
        )
        expected_retention = (
            self.remaining_active_hold_count > 0
            or self.active_ref_count > 0
            or minimum_lock
        )
        if result.retention_locked != expected_retention:
            raise ArtifactHoldExpiryConflict(
                "stored result retention contradicts commit evidence"
            )
        return result

    def to_wire_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "expireOperationId": self.expire_operation_id,
            "requestDigest": self.request_digest,
            "expectedHoldDigest": self.expected_hold_digest,
            "authorizedCandidateDigest": self.authorized_candidate_digest,
            "authorizedReaperPrincipalId": self.authorized_reaper_principal_id,
            "authorizedReaperFencingToken": self.authorized_reaper_fencing_token,
            "previousArtifactVersion": self.previous_artifact_version,
            "remainingActiveHoldCount": self.remaining_active_hold_count,
            "activeRefCount": self.active_ref_count,
            "minimumDeleteAtMs": self.minimum_delete_at_ms,
            "committedAtMs": self.committed_at_ms,
            "resultJson": self.result_json.decode("utf-8"),
            "commitDigest": self.commit_digest,
        }

    def canonical_json(self) -> bytes:
        return rfc8785.dumps(self.to_wire_dict())


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryEventRecord:
    schema_version: str
    sink: ArtifactHoldExpiryEventSink
    event_id: str
    event_type: str
    expire_operation_id: str
    artifact_id: str
    hold_id: str
    artifact_version: int
    result_digest: str
    commit_digest: str
    recorded_at_ms: int
    event_digest: str

    @classmethod
    def create(
        cls,
        *,
        sink: ArtifactHoldExpiryEventSink,
        commit: ArtifactHoldExpiryCommit,
    ) -> ArtifactHoldExpiryEventRecord:
        commit.validate()
        if not isinstance(sink, ArtifactHoldExpiryEventSink):
            raise ArtifactHoldExpiryConflict("unknown hold expiry event sink")
        result = ArtifactHoldExpiryResult.from_canonical_json(commit.result_json)
        event_id = hashlib.sha256(
            _SEPARATOR.join(
                (
                    _EVENT_DOMAIN,
                    sink.value.encode("ascii"),
                    commit.expire_operation_id.encode("ascii"),
                )
            )
        ).hexdigest()
        record = cls(
            schema_version="1",
            sink=sink,
            event_id=event_id,
            event_type="ArtifactHoldExpired",
            expire_operation_id=commit.expire_operation_id,
            artifact_id=result.artifact_id,
            hold_id=result.hold_id,
            artifact_version=result.artifact_version,
            result_digest=result.result_digest,
            commit_digest=commit.commit_digest,
            recorded_at_ms=commit.committed_at_ms,
            event_digest="",
        )
        created = replace(record, event_digest=_event_digest(record._digest_payload()))
        created.validate()
        return created

    def _digest_payload(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "sink": self.sink.value,
            "eventId": self.event_id,
            "eventType": self.event_type,
            "expireOperationId": self.expire_operation_id,
            "artifactId": self.artifact_id,
            "holdId": self.hold_id,
            "artifactVersion": self.artifact_version,
            "resultDigest": self.result_digest,
            "commitDigest": self.commit_digest,
            "recordedAtMs": self.recorded_at_ms,
        }

    def validate(self) -> None:
        if self.schema_version != "1":
            raise ArtifactHoldExpiryConflict("unknown hold expiry event schema")
        if not isinstance(self.sink, ArtifactHoldExpiryEventSink):
            raise ArtifactHoldExpiryConflict("unknown hold expiry event sink")
        _require_sha256(self.event_id, "hold expiry event ID")
        if self.event_type != "ArtifactHoldExpired":
            raise ArtifactHoldExpiryConflict("unknown hold expiry event type")
        _require_sha256(self.expire_operation_id, "expire operation ID")
        _require_stable_text(self.artifact_id, "artifact_id")
        _require_stable_text(self.hold_id, "hold_id")
        _require_integer(self.artifact_version, "artifact_version", minimum=1)
        _require_sha256(self.result_digest, "result_digest")
        _require_sha256(self.commit_digest, "commit_digest")
        _require_integer(self.recorded_at_ms, "recorded_at_ms", minimum=0)
        expected_id = hashlib.sha256(
            _SEPARATOR.join(
                (
                    _EVENT_DOMAIN,
                    self.sink.value.encode("ascii"),
                    self.expire_operation_id.encode("ascii"),
                )
            )
        ).hexdigest()
        if self.event_id != expected_id:
            raise ArtifactHoldExpiryConflict("hold expiry event ID mismatch")
        _require_sha256(self.event_digest, "event_digest")
        if self.event_digest != _event_digest(self._digest_payload()):
            raise ArtifactHoldExpiryConflict("hold expiry event digest mismatch")


@dataclass(frozen=True, slots=True)
class ArtifactHoldExpiryCASState:
    hold_state: ArtifactHoldState
    candidate_ledger: ArtifactHoldExpiryLedgerState
    commits: tuple[ArtifactHoldExpiryCommit, ...]
    audit_records: tuple[ArtifactHoldExpiryEventRecord, ...]
    outbox_records: tuple[ArtifactHoldExpiryEventRecord, ...]

    @classmethod
    def create(
        cls,
        *,
        hold_state: ArtifactHoldState,
        candidate_ledger: ArtifactHoldExpiryLedgerState,
    ) -> ArtifactHoldExpiryCASState:
        created = cls(
            hold_state=hold_state,
            candidate_ledger=candidate_ledger,
            commits=(),
            audit_records=(),
            outbox_records=(),
        )
        created.validate()
        return created

    def commit_for(
        self,
        expire_operation_id: str,
    ) -> ArtifactHoldExpiryCommit | None:
        _require_sha256(expire_operation_id, "expire operation ID")
        matches = tuple(
            commit
            for commit in self.commits
            if commit.expire_operation_id == expire_operation_id
        )
        if len(matches) > 1:
            raise ArtifactHoldExpiryConflict("duplicate expiry commit evidence")
        return matches[0] if matches else None

    def validate(self) -> None:
        if not isinstance(self.hold_state, ArtifactHoldState):
            raise ArtifactHoldExpiryConflict("CAS snapshot has invalid hold state")
        if not isinstance(self.candidate_ledger, ArtifactHoldExpiryLedgerState):
            raise ArtifactHoldExpiryConflict("CAS snapshot has invalid candidate ledger")
        self.hold_state.validate()
        self.candidate_ledger.validate()
        if self.hold_state.hold_status is ArtifactHoldStatus.ACTIVE:
            current_incarnation_candidates = tuple(
                entry.candidate
                for entry in self.candidate_ledger.candidate_entries
                if not entry.consumed
                and not self.candidate_ledger.is_replay_candidate(entry.candidate)
                and entry.candidate.artifact_id == self.hold_state.artifact_id
                and entry.candidate.hold_id == self.hold_state.hold_id
                and entry.candidate.expected_hold_digest
                == self.hold_state.hold_digest
                and entry.candidate.expected_artifact_version
                == self.hold_state.artifact_version
                and entry.candidate.observed_expires_ms
                == self.hold_state.expires_ms
            )
            latest_candidate = (
                max(
                    current_incarnation_candidates,
                    key=lambda candidate: candidate.candidate_fencing_token,
                )
                if current_incarnation_candidates
                else None
            )
            if self.hold_state.candidate != latest_candidate:
                raise ArtifactHoldExpiryConflict(
                    "active hold candidate projection is not the latest ledger authority"
                )
        for name, values in (
            ("commit", self.commits),
            ("audit", self.audit_records),
            ("outbox", self.outbox_records),
        ):
            if not isinstance(values, tuple):
                raise ArtifactHoldExpiryConflict(f"{name} ledger must be immutable")

        if self.hold_state.candidate is not None:
            entry = self.candidate_ledger.candidate_entry_for(
                self.hold_state.candidate.candidate_lease_id
            )
            if (
                entry.candidate != self.hold_state.candidate
                or entry.consumed
                or self.candidate_ledger.is_replay_candidate(entry.candidate)
            ):
                raise ArtifactHoldExpiryConflict(
                    "hold candidate projection contradicts authoritative ledger"
                )

        commits_by_operation: dict[str, ArtifactHoldExpiryCommit] = {}
        results_by_operation: dict[str, ArtifactHoldExpiryResult] = {}
        for commit in self.commits:
            if not isinstance(commit, ArtifactHoldExpiryCommit):
                raise ArtifactHoldExpiryConflict("commit ledger contains invalid evidence")
            commit.validate()
            if commit.expire_operation_id in commits_by_operation:
                raise ArtifactHoldExpiryConflict("duplicate expiry commit evidence")
            result = ArtifactHoldExpiryResult.from_canonical_json(commit.result_json)
            commits_by_operation[commit.expire_operation_id] = commit
            results_by_operation[commit.expire_operation_id] = result

        for claim_request, claim_result in zip(
            self.candidate_ledger.replay_claim_requests,
            self.candidate_ledger.replay_claim_results,
            strict=True,
        ):
            commit = commits_by_operation.get(claim_request.expire_operation_id)
            if commit is None:
                raise ArtifactHoldExpiryConflict(
                    "replay claim has no committed expiry evidence"
                )
            if (
                claim_request.base_commit_digest != commit.commit_digest
                or claim_result.base_commit_digest != commit.commit_digest
            ):
                raise ArtifactHoldExpiryConflict(
                    "replay claim base commit digest is not persisted commit"
                )
            committed_result = results_by_operation[claim_request.expire_operation_id]
            candidate = claim_result.candidate
            if (
                candidate.owner_principal_id
                != commit.authorized_reaper_principal_id
                or candidate.artifact_id != committed_result.artifact_id
                or candidate.hold_id != committed_result.hold_id
                or candidate.expected_hold_digest != commit.expected_hold_digest
                or candidate.expected_artifact_version
                != commit.previous_artifact_version
                or candidate.observed_expires_ms
                != self.hold_state.expires_ms
                or candidate.candidate_fencing_token
                <= commit.authorized_reaper_fencing_token
                or candidate.issued_at_ms < commit.committed_at_ms
            ):
                raise ArtifactHoldExpiryConflict(
                    "replay claim candidate contradicts committed expiry evidence"
                )
            if (
                self.hold_state.hold_status is not ArtifactHoldStatus.EXPIRED
                or self.hold_state.due_indexed
                or self.hold_state.candidate is not None
            ):
                raise ArtifactHoldExpiryConflict(
                    "replay claim requires the committed terminal hold state"
                )

        consumed_by_operation: dict[
            str, ArtifactHoldExpiryCandidateLedgerEntry
        ] = {}
        for entry in self.candidate_ledger.candidate_entries:
            if not entry.consumed:
                continue
            operation_id = entry.consumed_by_expire_operation_id
            if operation_id is None or operation_id in consumed_by_operation:
                raise ArtifactHoldExpiryConflict(
                    "candidate consumption is missing or duplicated"
                )
            consumed_by_operation[operation_id] = entry
        if set(consumed_by_operation) != set(commits_by_operation):
            raise ArtifactHoldExpiryConflict(
                "candidate tombstones and commit evidence are not atomic"
            )

        for operation_id, commit in commits_by_operation.items():
            entry = consumed_by_operation[operation_id]
            if entry.consumed_at_ms != commit.committed_at_ms:
                raise ArtifactHoldExpiryConflict(
                    "candidate tombstone timestamp contradicts commit"
                )
            if (
                commit.authorized_reaper_fencing_token
                != entry.candidate.candidate_fencing_token
            ):
                raise ArtifactHoldExpiryConflict(
                    "commit fence contradicts consumed candidate"
                )
            candidate = entry.candidate
            if candidate.candidate_digest != commit.authorized_candidate_digest:
                raise ArtifactHoldExpiryConflict(
                    "commit candidate digest contradicts consumed candidate"
                )
            if not (
                candidate.issued_at_ms
                <= commit.committed_at_ms
                < candidate.lease_until_ms
            ):
                raise ArtifactHoldExpiryConflict(
                    "commit time is outside candidate lease"
                )
            if commit.committed_at_ms < candidate.observed_expires_ms:
                raise ArtifactHoldExpiryConflict(
                    "commit time precedes observed expiry"
                )
            request = ArtifactHoldExpiryRequest.create(candidate=candidate)
            result = commit._validate_result_for_request(
                request,
                authenticated_reaper_principal_id=entry.candidate.owner_principal_id,
                authenticated_reaper_fencing_token=(
                    entry.candidate.candidate_fencing_token
                ),
                authenticated_component_type=ARTIFACT_HOLD_REAPER_COMPONENT_TYPE,
                authenticated_subject=ARTIFACT_HOLD_EXPIRY_SUBJECT,
            )
            if result != results_by_operation[operation_id]:
                raise ArtifactHoldExpiryConflict("commit result evidence drifted")
            for later_entry in self.candidate_ledger.candidate_entries:
                later_candidate = later_entry.candidate
                if (
                    not later_entry.consumed
                    and not self.candidate_ledger.is_replay_candidate(later_candidate)
                    and later_candidate.artifact_id == entry.candidate.artifact_id
                    and later_candidate.hold_id == entry.candidate.hold_id
                    and later_candidate.expected_hold_digest
                    == entry.candidate.expected_hold_digest
                    and later_candidate.observed_expires_ms
                    == entry.candidate.observed_expires_ms
                    and later_candidate.candidate_fencing_token
                    > entry.candidate.candidate_fencing_token
                ):
                    raise ArtifactHoldExpiryConflict(
                        "terminal hold cannot carry a newer unconsumed candidate"
                    )
            expected_audit = ArtifactHoldExpiryEventRecord.create(
                sink=ArtifactHoldExpiryEventSink.AUDIT,
                commit=commit,
            )
            expected_outbox = ArtifactHoldExpiryEventRecord.create(
                sink=ArtifactHoldExpiryEventSink.OUTBOX,
                commit=commit,
            )
            if self.audit_records.count(expected_audit) != 1:
                raise ArtifactHoldExpiryConflict(
                    "commit does not have exactly one bound audit record"
                )
            if self.outbox_records.count(expected_outbox) != 1:
                raise ArtifactHoldExpiryConflict(
                    "commit does not have exactly one bound outbox record"
                )
            if (
                result.artifact_id == self.hold_state.artifact_id
                and result.hold_id == self.hold_state.hold_id
            ):
                if self.hold_state.hold_digest != commit.expected_hold_digest:
                    raise ArtifactHoldExpiryConflict(
                        "current hold incarnation contradicts committed expiry"
                    )
                if self.hold_state.expires_ms != candidate.observed_expires_ms:
                    raise ArtifactHoldExpiryConflict(
                        "current hold expiry timestamp contradicts committed due tuple"
                    )
                if (
                    self.hold_state.hold_status is not ArtifactHoldStatus.EXPIRED
                    or self.hold_state.hold_id in self.hold_state.active_hold_ids
                    or self.hold_state.due_indexed
                    or self.hold_state.due_score_ms is not None
                    or self.hold_state.candidate is not None
                    or self.hold_state.artifact_version < result.artifact_version
                ):
                    raise ArtifactHoldExpiryConflict(
                        "current state does not reflect the committed expiry"
                    )
                if self.hold_state.artifact_version == result.artifact_version:
                    if (
                        commit.remaining_active_hold_count
                        != len(self.hold_state.active_hold_ids)
                        or commit.active_ref_count
                        != self.hold_state.active_ref_count
                        or commit.minimum_delete_at_ms
                        != self.hold_state.minimum_delete_at_ms
                        or result.retention_locked
                        != self.hold_state.retention_locked_at(
                            commit.committed_at_ms
                        )
                    ):
                        raise ArtifactHoldExpiryConflict(
                            "commit retention evidence contradicts final state"
                        )

        if len(self.audit_records) != len(self.commits):
            raise ArtifactHoldExpiryConflict("audit ledger cardinality mismatch")
        if len(self.outbox_records) != len(self.commits):
            raise ArtifactHoldExpiryConflict("outbox ledger cardinality mismatch")
        for record in self.audit_records:
            if (
                not isinstance(record, ArtifactHoldExpiryEventRecord)
                or record.sink is not ArtifactHoldExpiryEventSink.AUDIT
            ):
                raise ArtifactHoldExpiryConflict("audit ledger contains invalid record")
            record.validate()
        for record in self.outbox_records:
            if (
                not isinstance(record, ArtifactHoldExpiryEventRecord)
                or record.sink is not ArtifactHoldExpiryEventSink.OUTBOX
            ):
                raise ArtifactHoldExpiryConflict("outbox ledger contains invalid record")
            record.validate()


def _new_candidate_token() -> str:
    return secrets.token_urlsafe(32)


def _allocate_scan_candidate(
    *,
    request: ArtifactHoldExpiryScanRequest,
    state: ArtifactHoldExpiryCASState,
    hold: ArtifactHoldState,
    server_now_ms: int,
    authenticated_reaper_principal_id: str,
    authenticated_reaper_instance_id: str,
) -> _ArtifactHoldExpiryScanAllocation:
    """Allocate SCAN authority from the authoritative State snapshot.

    The public SCAN writer intentionally has no candidate argument.  In the
    real State Function the two tokens come from the State-side CSPRNG and the
    fence comes from the persisted global high-water.  This pure contract
    models that boundary by allocating them here, before the result/ledger
    projection is written.
    """

    previous_fence_high_water = state.candidate_ledger.candidate_fence_high_water
    next_fence = previous_fence_high_water + 1
    lease_until_ms = server_now_ms + ARTIFACT_HOLD_CANDIDATE_LEASE_MAX_MS
    candidate_lease_id = _new_candidate_token()
    candidate_token = _new_candidate_token()
    while candidate_token == candidate_lease_id:
        candidate_token = _new_candidate_token()
    candidate = ArtifactHoldExpiryCandidate.create(
        scan_operation_id=request.scan_operation_id,
        candidate_lease_id=candidate_lease_id,
        candidate_fencing_token=next_fence,
        candidate_token=candidate_token,
        owner_principal_id=authenticated_reaper_principal_id,
        owner_instance_id=authenticated_reaper_instance_id,
        issued_at_ms=server_now_ms,
        lease_until_ms=lease_until_ms,
        artifact_id=hold.artifact_id,
        hold_id=hold.hold_id,
        expected_hold_digest=hold.hold_digest,
        expected_artifact_version=hold.artifact_version,
        observed_expires_ms=hold.expires_ms
        if hold.expires_ms is not None
        else 0,
    )
    return _issue_scan_allocation(
        request=request,
        candidate=candidate,
        previous_fence_high_water=previous_fence_high_water,
    )


def apply_artifact_hold_expiry_scan(
    request: ArtifactHoldExpiryScanRequest,
    state: ArtifactHoldExpiryCASState,
    *,
    server_now_ms: int,
    authenticated_reaper_principal_id: str,
    authenticated_reaper_instance_id: str,
    authenticated_component_type: str,
    authenticated_subject: str,
) -> tuple[ArtifactHoldExpiryCASState, ArtifactHoldExpiryScanResult]:
    request.validate()
    if not isinstance(state, ArtifactHoldExpiryCASState):
        raise ArtifactHoldExpiryConflict(
            "hold expiry scan requires an authoritative atomic CAS snapshot"
        )
    state.validate()
    _require_integer(server_now_ms, "server_now_ms", minimum=0)
    _require_stable_text(
        authenticated_reaper_principal_id,
        "authenticated reaper principal",
    )
    _require_stable_text(
        authenticated_reaper_instance_id,
        "authenticated reaper instance",
    )
    _require_stable_text(authenticated_component_type, "authenticated component type")
    _require_stable_text(authenticated_subject, "authenticated subject")
    if authenticated_component_type != ARTIFACT_HOLD_REAPER_COMPONENT_TYPE:
        raise ArtifactHoldExpiryConflict(
            "only artifact-hold-reaper may scan hold expiry"
        )
    if authenticated_reaper_principal_id != ARTIFACT_HOLD_REAPER_PRINCIPAL_ID:
        raise ArtifactHoldExpiryConflict(
            "authenticated principal is not the configured artifact-hold-reaper"
        )
    if authenticated_subject != ARTIFACT_HOLD_EXPIRY_SUBJECT:
        raise ArtifactHoldExpiryConflict("hold expiry scan used a forbidden subject")
    for stored_request, stored_result in zip(
        state.candidate_ledger.scan_requests,
        state.candidate_ledger.scan_results,
        strict=True,
    ):
        if stored_request.scan_operation_id != request.scan_operation_id:
            continue
        if stored_request != request:
            raise ArtifactHoldExpiryConflict(
                "scan ID already has different persisted request or result"
            )
        if any(
            candidate.owner_principal_id != authenticated_reaper_principal_id
            or candidate.owner_instance_id != authenticated_reaper_instance_id
            for candidate in stored_result.candidates
        ):
            raise ArtifactHoldExpiryConflict(
                "scan result candidate owner does not match authenticated reaper"
            )
        return state, stored_result

    hold = state.hold_state
    if hold.hold_status is not ArtifactHoldStatus.ACTIVE:
        raise ArtifactHoldExpiryConflict("scan requires an ACTIVE hold")
    if hold.expires_ms is None or not hold.due_indexed or hold.due_score_ms is None:
        raise ArtifactHoldExpiryConflict("scan requires a persisted due hold")
    if server_now_ms < hold.due_score_ms:
        raise ArtifactHoldExpiryConflict("scan server time precedes due score")

    allocation = _allocate_scan_candidate(
        request=request,
        state=state,
        hold=hold,
        server_now_ms=server_now_ms,
        authenticated_reaper_principal_id=authenticated_reaper_principal_id,
        authenticated_reaper_instance_id=authenticated_reaper_instance_id,
    )
    result = ArtifactHoldExpiryScanResult.create(
        request=request,
        candidates=(allocation.candidate,),
    )
    for candidate in result.candidates:
        if (
            candidate.owner_principal_id != authenticated_reaper_principal_id
            or candidate.owner_instance_id != authenticated_reaper_instance_id
        ):
            raise ArtifactHoldExpiryConflict(
                "scan candidate owner does not match authenticated reaper"
            )
        if not (
            candidate.issued_at_ms <= server_now_ms < candidate.lease_until_ms
        ):
            raise ArtifactHoldExpiryConflict(
                "scan candidate lease is not live at server time"
            )
        if candidate.issued_at_ms < candidate.observed_expires_ms:
            raise ArtifactHoldExpiryConflict(
                "scan candidate was issued before observed expiry"
            )
        if (
            candidate.artifact_id != hold.artifact_id
            or candidate.hold_id != hold.hold_id
            or candidate.expected_hold_digest != hold.hold_digest
            or candidate.expected_artifact_version != hold.artifact_version
            or candidate.observed_expires_ms != hold.expires_ms
        ):
            raise ArtifactHoldExpiryConflict(
                "scan candidate does not match current due tuple"
            )
        if candidate.candidate_fencing_token <= state.candidate_ledger.candidate_fence_high_water:
            raise ArtifactHoldExpiryConflict(
                "scan candidate fence is not above persisted high-water"
            )

    updated_ledger = state.candidate_ledger._record_scan(
        request,
        result,
        allocations=(allocation,),
    )
    current_candidates = tuple(
        entry.candidate
        for entry in updated_ledger.candidate_entries
        if not entry.consumed
        and entry.candidate.artifact_id == hold.artifact_id
        and entry.candidate.hold_id == hold.hold_id
        and entry.candidate.expected_hold_digest == hold.hold_digest
        and entry.candidate.expected_artifact_version == hold.artifact_version
        and entry.candidate.observed_expires_ms == hold.expires_ms
    )
    latest_candidate = (
        max(current_candidates, key=lambda item: item.candidate_fencing_token)
        if current_candidates
        else None
    )
    updated = replace(
        state,
        hold_state=replace(hold, candidate=latest_candidate),
        candidate_ledger=updated_ledger,
    )
    updated.validate()
    return updated, result


def apply_artifact_hold_expiry_replay_claim(
    request: ArtifactHoldExpiryReplayClaimRequest,
    state: ArtifactHoldExpiryCASState,
    *,
    server_now_ms: int,
    lease_until_ms: int,
    authenticated_reaper_principal_id: str,
    authenticated_reaper_instance_id: str,
    authenticated_component_type: str,
    authenticated_subject: str,
) -> tuple[ArtifactHoldExpiryCASState, ArtifactHoldExpiryReplayClaimResult]:
    request.validate()
    if not isinstance(state, ArtifactHoldExpiryCASState):
        raise ArtifactHoldExpiryConflict(
            "replay claim requires an authoritative atomic CAS snapshot"
        )
    state.validate()
    _require_integer(server_now_ms, "server_now_ms", minimum=0)
    _require_integer(lease_until_ms, "replay claim lease_until_ms", minimum=0)
    _require_stable_text(
        authenticated_reaper_principal_id,
        "authenticated reaper principal",
    )
    _require_stable_text(
        authenticated_reaper_instance_id,
        "authenticated reaper instance",
    )
    _require_stable_text(authenticated_component_type, "authenticated component type")
    _require_stable_text(authenticated_subject, "authenticated subject")
    if authenticated_component_type != ARTIFACT_HOLD_REAPER_COMPONENT_TYPE:
        raise ArtifactHoldExpiryConflict(
            "only artifact-hold-reaper may claim expiry replay"
        )
    if authenticated_subject != ARTIFACT_HOLD_EXPIRY_SUBJECT:
        raise ArtifactHoldExpiryConflict(
            "hold expiry replay claim used a forbidden subject"
        )
    for stored_request, stored_result in zip(
        state.candidate_ledger.replay_claim_requests,
        state.candidate_ledger.replay_claim_results,
        strict=True,
    ):
        if stored_request.replay_operation_id != request.replay_operation_id:
            continue
        if stored_request != request:
            raise ArtifactHoldExpiryConflict(
                "replay claim ID already has a different request"
            )
        candidate = stored_result.candidate
        if (
            candidate.owner_principal_id != authenticated_reaper_principal_id
            or candidate.owner_instance_id != authenticated_reaper_instance_id
        ):
            raise ArtifactHoldExpiryConflict(
                "persisted replay claim is not owned by authenticated reaper"
            )
        return state, stored_result

    if lease_until_ms <= server_now_ms:
        raise ArtifactHoldExpiryConflict("replay claim lease must be live")

    commit = state.commit_for(request.expire_operation_id)
    if commit is None:
        raise ArtifactHoldExpiryConflict(
            "replay claim requires persisted expiry commit evidence"
        )
    if request.base_commit_digest != commit.commit_digest:
        raise ArtifactHoldExpiryConflict(
            "replay claim base commit digest does not match persisted commit"
        )
    committed_result = ArtifactHoldExpiryResult.from_canonical_json(commit.result_json)
    consumed_matches = tuple(
        entry
        for entry in state.candidate_ledger.candidate_entries
        if entry.consumed_by_expire_operation_id == request.expire_operation_id
    )
    if len(consumed_matches) != 1:
        raise ArtifactHoldExpiryConflict(
            "replay claim requires exactly one consumed expiry candidate"
        )
    original_candidate = consumed_matches[0].candidate
    if (
        request.artifact_id != original_candidate.artifact_id
        or request.hold_id != original_candidate.hold_id
        or request.expected_hold_digest != original_candidate.expected_hold_digest
        or request.expected_artifact_version
        != original_candidate.expected_artifact_version
        or request.observed_expires_ms != original_candidate.observed_expires_ms
        or request.expire_operation_id
        != artifact_hold_expiry_operation_id(
            artifact_id=original_candidate.artifact_id,
            hold_id=original_candidate.hold_id,
            expected_hold_digest=original_candidate.expected_hold_digest,
            expected_artifact_version=original_candidate.expected_artifact_version,
            observed_expires_ms=original_candidate.observed_expires_ms,
        )
        or committed_result.artifact_id != request.artifact_id
        or committed_result.hold_id != request.hold_id
    ):
        raise ArtifactHoldExpiryConflict(
            "replay claim request does not match committed expiry"
        )
    if authenticated_reaper_principal_id != commit.authorized_reaper_principal_id:
        raise ArtifactHoldExpiryConflict(
            "replay claim principal does not own committed expiry"
        )
    if (
        state.hold_state.hold_status is not ArtifactHoldStatus.EXPIRED
        or state.hold_state.artifact_id != request.artifact_id
        or state.hold_state.hold_id != request.hold_id
        or state.hold_state.hold_digest != request.expected_hold_digest
        or state.hold_state.expires_ms != request.observed_expires_ms
        or state.hold_state.due_indexed
        or state.hold_state.candidate is not None
        or state.hold_state.artifact_version < committed_result.artifact_version
    ):
        raise ArtifactHoldExpiryConflict(
            "replay claim requires the committed terminal hold state"
        )

    current_replay_claim = (
        state.candidate_ledger.current_replay_claim_for_expire_operation(
            request.expire_operation_id
        )
    )
    if current_replay_claim is not None:
        current_candidate = current_replay_claim[1].candidate
        if server_now_ms < current_candidate.issued_at_ms:
            raise ArtifactHoldExpiryConflict(
                "server time predates current replay authority"
            )
        if server_now_ms < current_candidate.lease_until_ms:
            raise ArtifactHoldExpiryConflict(
                "current replay authority lease is still live"
            )

    candidate = ArtifactHoldExpiryCandidate.create(
        scan_operation_id=request.replay_operation_id,
        candidate_lease_id=request.candidate_lease_id,
        candidate_fencing_token=state.candidate_ledger.candidate_fence_high_water
        + 1,
        candidate_token=request.candidate_token,
        owner_principal_id=authenticated_reaper_principal_id,
        owner_instance_id=authenticated_reaper_instance_id,
        issued_at_ms=server_now_ms,
        lease_until_ms=lease_until_ms,
        artifact_id=request.artifact_id,
        hold_id=request.hold_id,
        expected_hold_digest=request.expected_hold_digest,
        expected_artifact_version=request.expected_artifact_version,
        observed_expires_ms=request.observed_expires_ms,
    )
    result = ArtifactHoldExpiryReplayClaimResult.create(
        request=request,
        candidate=candidate,
    )
    updated = replace(
        state,
        candidate_ledger=state.candidate_ledger._record_replay_claim(
            request,
            result,
        ),
    )
    updated.validate()
    return updated, result


def artifact_hold_expiry_preimage(
    *,
    artifact_id: str,
    hold_id: str,
    expected_hold_digest: str,
    expected_artifact_version: int,
    observed_expires_ms: int,
) -> bytes:
    _require_stable_text(artifact_id, "artifact_id")
    _require_stable_text(hold_id, "hold_id")
    _require_sha256(expected_hold_digest, "expected_hold_digest")
    _require_integer(
        expected_artifact_version,
        "expected_artifact_version",
        minimum=1,
    )
    _require_integer(observed_expires_ms, "observed_expires_ms", minimum=0)
    return _SEPARATOR.join(
        (
            _EXPIRE_DOMAIN,
            artifact_id.encode(),
            hold_id.encode(),
            expected_hold_digest.encode("ascii"),
            str(expected_artifact_version).encode("ascii"),
            str(observed_expires_ms).encode("ascii"),
        )
    )


def artifact_hold_expiry_operation_id(
    *,
    artifact_id: str,
    hold_id: str,
    expected_hold_digest: str,
    expected_artifact_version: int,
    observed_expires_ms: int,
) -> str:
    preimage = artifact_hold_expiry_preimage(
        artifact_id=artifact_id,
        hold_id=hold_id,
        expected_hold_digest=expected_hold_digest,
        expected_artifact_version=expected_artifact_version,
        observed_expires_ms=observed_expires_ms,
    )
    return hashlib.sha256(preimage).hexdigest()


def artifact_hold_expiry_request_digest(request: ArtifactHoldExpiryRequest) -> str:
    return _canonical_digest(_expire_request_business_payload(request))


def artifact_hold_expiry_replay_claim_operation_id(
    expire_operation_id: str,
    candidate_lease_id: str,
) -> str:
    _require_sha256(expire_operation_id, "expire_operation_id")
    _require_candidate_token(candidate_lease_id, "replay claim candidate lease ID")
    return hashlib.sha256(
        _SEPARATOR.join(
            (
                _REPLAY_CLAIM_DOMAIN,
                expire_operation_id.encode("ascii"),
                candidate_lease_id.encode("ascii"),
            )
        )
    ).hexdigest()


def _project_artifact_hold_expiry(
    request: ArtifactHoldExpiryRequest,
    state: ArtifactHoldState,
    *,
    server_now_ms: int,
    authenticated_reaper_principal_id: str,
    authenticated_reaper_instance_id: str,
    authenticated_reaper_fencing_token: int,
    authenticated_component_type: str,
    authenticated_subject: str,
) -> tuple[ArtifactHoldState, ArtifactHoldExpiryResult, ArtifactHoldExpiryCommit]:
    request.validate()
    _require_integer(server_now_ms, "server_now_ms", minimum=0)
    _require_stable_text(
        authenticated_reaper_principal_id,
        "authenticated reaper principal",
    )
    _require_stable_text(
        authenticated_reaper_instance_id,
        "authenticated reaper instance",
    )
    _require_integer(
        authenticated_reaper_fencing_token,
        "authenticated reaper fencing token",
        minimum=1,
    )
    _require_stable_text(authenticated_component_type, "authenticated component type")
    _require_stable_text(authenticated_subject, "authenticated subject")
    if authenticated_component_type != ARTIFACT_HOLD_REAPER_COMPONENT_TYPE:
        raise ArtifactHoldExpiryConflict(
            "only artifact-hold-reaper may submit hold expiry"
        )
    if authenticated_subject != ARTIFACT_HOLD_EXPIRY_SUBJECT:
        raise ArtifactHoldExpiryConflict("hold expiry used a forbidden subject")
    state.validate()
    candidate = state.candidate
    if candidate is None:
        raise ArtifactHoldExpiryConflict("expiry candidate ledger is missing")
    _require_expiry_predicate(
        request,
        state,
        candidate,
        server_now_ms=server_now_ms,
        authenticated_reaper_principal_id=authenticated_reaper_principal_id,
        authenticated_reaper_instance_id=authenticated_reaper_instance_id,
        authenticated_reaper_fencing_token=authenticated_reaper_fencing_token,
    )
    updated = ArtifactHoldState(
        artifact_id=state.artifact_id,
        artifact_version=state.artifact_version + 1,
        artifact_status=state.artifact_status,
        hold_id=state.hold_id,
        hold_digest=state.hold_digest,
        hold_status=ArtifactHoldStatus.EXPIRED,
        expires_ms=state.expires_ms,
        active_hold_ids=state.active_hold_ids - {state.hold_id},
        active_ref_count=state.active_ref_count,
        minimum_delete_at_ms=state.minimum_delete_at_ms,
        due_indexed=False,
        due_score_ms=None,
        candidate=None,
    )
    updated.validate()
    result = ArtifactHoldExpiryResult.create(
        expire_operation_id=request.expire_operation_id,
        artifact_id=state.artifact_id,
        hold_id=state.hold_id,
        request_digest=request.request_digest,
        artifact_version=updated.artifact_version,
        retention_locked=updated.retention_locked_at(server_now_ms),
    )
    commit = ArtifactHoldExpiryCommit.create(
        request=request,
        result=result,
        before_state=state,
        updated_state=updated,
        committed_at_ms=server_now_ms,
    )
    return updated, result, commit


def apply_artifact_hold_expiry(
    request: ArtifactHoldExpiryRequest,
    state: ArtifactHoldExpiryCASState,
    *,
    server_now_ms: int,
    authenticated_reaper_principal_id: str,
    authenticated_reaper_instance_id: str,
    authenticated_reaper_fencing_token: int,
    authenticated_component_type: str,
    authenticated_subject: str,
) -> tuple[ArtifactHoldExpiryCASState, ArtifactHoldExpiryResult]:
    request.validate()
    _require_integer(server_now_ms, "server_now_ms", minimum=0)
    _require_stable_text(
        authenticated_reaper_principal_id,
        "authenticated reaper principal",
    )
    _require_stable_text(
        authenticated_reaper_instance_id,
        "authenticated reaper instance",
    )
    _require_integer(
        authenticated_reaper_fencing_token,
        "authenticated reaper fencing token",
        minimum=1,
    )
    _require_stable_text(authenticated_component_type, "authenticated component type")
    _require_stable_text(authenticated_subject, "authenticated subject")
    if authenticated_component_type != ARTIFACT_HOLD_REAPER_COMPONENT_TYPE:
        raise ArtifactHoldExpiryConflict(
            "only artifact-hold-reaper may submit hold expiry"
        )
    if authenticated_subject != ARTIFACT_HOLD_EXPIRY_SUBJECT:
        raise ArtifactHoldExpiryConflict("hold expiry used a forbidden subject")
    if not isinstance(state, ArtifactHoldExpiryCASState):
        raise ArtifactHoldExpiryConflict(
            "hold expiry requires an authoritative atomic CAS snapshot"
        )
    state.validate()

    committed = state.commit_for(request.expire_operation_id)
    if committed is not None:
        verified_replay_authority = (
            state.candidate_ledger.require_replay_authority(
                request,
                authenticated_principal_id=authenticated_reaper_principal_id,
                authenticated_instance_id=authenticated_reaper_instance_id,
                authenticated_fencing_token=authenticated_reaper_fencing_token,
                server_now_ms=server_now_ms,
            )
        )
        result = committed._validate_result_for_request(
            request,
            authenticated_reaper_principal_id=authenticated_reaper_principal_id,
            authenticated_reaper_fencing_token=authenticated_reaper_fencing_token,
            authenticated_component_type=authenticated_component_type,
            authenticated_subject=authenticated_subject,
            verified_replay_authority=verified_replay_authority,
        )
        if (
            state.hold_state.artifact_id != request.artifact_id
            or state.hold_state.hold_id != request.hold_id
        ):
            raise ArtifactHoldExpiryConflict(
                "committed expiry cannot replay for a different current resource"
            )
        if state.hold_state.hold_digest != committed.expected_hold_digest:
            raise ArtifactHoldExpiryConflict(
                "committed expiry cannot replay for a different hold incarnation"
            )
        if state.hold_state.artifact_version < result.artifact_version:
            raise ArtifactHoldExpiryConflict(
                "current artifact version predates the committed expiry"
            )
        return state, result

    entry = state.candidate_ledger.candidate_entry_for(
        request.candidate_lease_id
    )
    if entry.consumed:
        raise ArtifactHoldExpiryConflict("candidate authority was already consumed")
    if state.hold_state.candidate != entry.candidate:
        raise ArtifactHoldExpiryConflict(
            "current candidate projection is not authoritative"
        )
    updated_hold, result, commit = _project_artifact_hold_expiry(
        request,
        state.hold_state,
        server_now_ms=server_now_ms,
        authenticated_reaper_principal_id=authenticated_reaper_principal_id,
        authenticated_reaper_instance_id=authenticated_reaper_instance_id,
        authenticated_reaper_fencing_token=authenticated_reaper_fencing_token,
        authenticated_component_type=authenticated_component_type,
        authenticated_subject=authenticated_subject,
    )
    updated_ledger = state.candidate_ledger.consume_candidate(
        request,
        consumed_at_ms=server_now_ms,
    )
    audit_record = ArtifactHoldExpiryEventRecord.create(
        sink=ArtifactHoldExpiryEventSink.AUDIT,
        commit=commit,
    )
    outbox_record = ArtifactHoldExpiryEventRecord.create(
        sink=ArtifactHoldExpiryEventSink.OUTBOX,
        commit=commit,
    )
    updated = replace(
        state,
        hold_state=updated_hold,
        candidate_ledger=updated_ledger,
        commits=state.commits + (commit,),
        audit_records=state.audit_records + (audit_record,),
        outbox_records=state.outbox_records + (outbox_record,),
    )
    updated.validate()
    return updated, result


def _require_candidate_request_binding(
    request: ArtifactHoldExpiryRequest,
    candidate: ArtifactHoldExpiryCandidate,
) -> None:
    if (
        request.artifact_id != candidate.artifact_id
        or request.hold_id != candidate.hold_id
        or request.expected_hold_digest != candidate.expected_hold_digest
        or request.expected_artifact_version != candidate.expected_artifact_version
        or request.observed_expires_ms != candidate.observed_expires_ms
        or request.scan_operation_id != candidate.scan_operation_id
        or request.candidate_lease_id != candidate.candidate_lease_id
        or request.candidate_fencing_token != candidate.candidate_fencing_token
        or request.candidate_token != candidate.candidate_token
    ):
        raise ArtifactHoldExpiryConflict(
            "candidate ledger is not bound to expiry request"
        )


def _require_expiry_predicate(
    request: ArtifactHoldExpiryRequest,
    state: ArtifactHoldState,
    candidate: ArtifactHoldExpiryCandidate,
    *,
    server_now_ms: int,
    authenticated_reaper_principal_id: str,
    authenticated_reaper_instance_id: str,
    authenticated_reaper_fencing_token: int,
) -> None:
    candidate.validate()
    _require_candidate_request_binding(request, candidate)
    comparisons = (
        (
            state.artifact_status
            not in {
                ArtifactLifecycleStatus.DELETING,
                ArtifactLifecycleStatus.DELETED,
            },
            "artifact lifecycle forbids hold expiry",
        ),
        (state.hold_status is ArtifactHoldStatus.ACTIVE, "hold is not ACTIVE"),
        (
            state.hold_id in state.active_hold_ids,
            "hold is absent from active retention set",
        ),
        (state.expires_ms is not None, "legal/non-expiring hold cannot expire"),
        (state.due_indexed, "hold due index membership is missing"),
        (state.due_score_ms == state.expires_ms, "hold due score changed"),
        (server_now_ms >= request.observed_expires_ms, "hold is not due"),
        (
            candidate.issued_at_ms <= server_now_ms,
            "candidate lease is not active yet",
        ),
        (server_now_ms < candidate.lease_until_ms, "candidate lease expired"),
        (
            candidate.owner_principal_id == authenticated_reaper_principal_id,
            "authenticated reaper principal is not candidate owner",
        ),
        (
            candidate.owner_instance_id == authenticated_reaper_instance_id,
            "authenticated reaper instance is not candidate owner",
        ),
        (
            candidate.candidate_fencing_token == authenticated_reaper_fencing_token,
            "authenticated reaper fence does not match candidate",
        ),
        (
            request.artifact_id == state.artifact_id == candidate.artifact_id,
            "artifact_id mismatch",
        ),
        (
            request.hold_id == state.hold_id == candidate.hold_id,
            "hold_id mismatch",
        ),
        (
            request.expected_hold_digest
            == state.hold_digest
            == candidate.expected_hold_digest,
            "hold digest changed",
        ),
        (
            request.expected_artifact_version
            == state.artifact_version
            == candidate.expected_artifact_version,
            "artifact version changed",
        ),
        (
            request.observed_expires_ms
            == state.expires_ms
            == candidate.observed_expires_ms,
            "expiry due tuple changed",
        ),
        (
            request.scan_operation_id == candidate.scan_operation_id,
            "scan operation mismatch",
        ),
        (
            request.candidate_lease_id == candidate.candidate_lease_id,
            "candidate lease ID mismatch",
        ),
        (
            request.candidate_fencing_token == candidate.candidate_fencing_token,
            "candidate fence mismatch",
        ),
        (
            request.candidate_token == candidate.candidate_token,
            "candidate token mismatch",
        ),
    )
    for matches, message in comparisons:
        if not matches:
            raise ArtifactHoldExpiryConflict(message)


def _scan_request_payload(request: ArtifactHoldExpiryScanRequest) -> dict[str, object]:
    return {
        "schemaVersion": request.schema_version,
        "operation": request.operation.value,
        "scanOperationId": request.scan_operation_id,
        "maxCandidates": request.max_candidates,
        "idempotencyKey": request.idempotency_key,
    }


def _scan_request_digest(request: ArtifactHoldExpiryScanRequest) -> str:
    return _canonical_digest(_scan_request_payload(request))


def _candidate_payload(candidate: ArtifactHoldExpiryCandidate) -> dict[str, object]:
    return {
        "schemaVersion": candidate.schema_version,
        "scanOperationId": candidate.scan_operation_id,
        "candidateLeaseId": candidate.candidate_lease_id,
        "candidateFencingToken": candidate.candidate_fencing_token,
        "candidateToken": candidate.candidate_token,
        "ownerPrincipalId": candidate.owner_principal_id,
        "ownerInstanceId": candidate.owner_instance_id,
        "issuedAtMs": candidate.issued_at_ms,
        "leaseUntilMs": candidate.lease_until_ms,
        "artifactId": candidate.artifact_id,
        "holdId": candidate.hold_id,
        "expectedHoldDigest": candidate.expected_hold_digest,
        "expectedArtifactVersion": candidate.expected_artifact_version,
        "observedExpiresMs": candidate.observed_expires_ms,
    }


def _scan_allocation_payload(
    allocation: _ArtifactHoldExpiryScanAllocation,
) -> dict[str, object]:
    return {
        "schemaVersion": "1",
        "scanOperationId": allocation.request.scan_operation_id,
        "requestDigest": allocation.request.request_digest,
        "candidateDigest": allocation.candidate.candidate_digest,
        "previousFenceHighWater": allocation.previous_fence_high_water,
    }


def _scan_allocation_digest(
    allocation: _ArtifactHoldExpiryScanAllocation,
) -> str:
    return _canonical_digest(_scan_allocation_payload(allocation))


def _scan_result_payload(result: ArtifactHoldExpiryScanResult) -> dict[str, object]:
    return {
        "schemaVersion": result.schema_version,
        "scanOperationId": result.scan_operation_id,
        "requestDigest": result.request_digest,
        "candidates": [candidate.to_wire_dict() for candidate in result.candidates],
    }


def _scan_result_digest(result: ArtifactHoldExpiryScanResult) -> str:
    return _canonical_digest(_scan_result_payload(result))


def _expire_request_business_payload(
    request: ArtifactHoldExpiryRequest,
) -> dict[str, object]:
    return {
        "schemaVersion": request.schema_version,
        "operation": request.operation.value,
        "expireOperationId": request.expire_operation_id,
        "artifactId": request.artifact_id,
        "holdId": request.hold_id,
        "expectedHoldDigest": request.expected_hold_digest,
        "expectedArtifactVersion": request.expected_artifact_version,
        "observedExpiresMs": request.observed_expires_ms,
        "idempotencyKey": request.idempotency_key,
    }


def _expire_request_payload(request: ArtifactHoldExpiryRequest) -> dict[str, object]:
    return _expire_request_business_payload(request) | {
        "scanOperationId": request.scan_operation_id,
        "candidateLeaseId": request.candidate_lease_id,
        "candidateFencingToken": request.candidate_fencing_token,
        "candidateToken": request.candidate_token,
    }


def _replay_claim_request_payload(
    request: ArtifactHoldExpiryReplayClaimRequest,
) -> dict[str, object]:
    return {
        "schemaVersion": request.schema_version,
        "operation": request.operation.value,
        "replayOperationId": request.replay_operation_id,
        "expireOperationId": request.expire_operation_id,
        "baseCommitDigest": request.base_commit_digest,
        "artifactId": request.artifact_id,
        "holdId": request.hold_id,
        "expectedHoldDigest": request.expected_hold_digest,
        "expectedArtifactVersion": request.expected_artifact_version,
        "observedExpiresMs": request.observed_expires_ms,
        "candidateLeaseId": request.candidate_lease_id,
        "candidateToken": request.candidate_token,
        "idempotencyKey": request.idempotency_key,
    }


def _replay_claim_request_digest(
    request: ArtifactHoldExpiryReplayClaimRequest,
) -> str:
    return _canonical_digest(_replay_claim_request_payload(request))


def _replay_claim_result_payload(
    result: ArtifactHoldExpiryReplayClaimResult,
) -> dict[str, object]:
    return {
        "schemaVersion": result.schema_version,
        "replayOperationId": result.replay_operation_id,
        "expireOperationId": result.expire_operation_id,
        "baseCommitDigest": result.base_commit_digest,
        "requestDigest": result.request_digest,
        "resultCode": result.result_code,
        "candidate": result.candidate.to_wire_dict(),
    }


def _replay_claim_result_digest(
    result: ArtifactHoldExpiryReplayClaimResult,
) -> str:
    return _canonical_digest(_replay_claim_result_payload(result))


def _expire_result_payload(result: ArtifactHoldExpiryResult) -> dict[str, object]:
    return {
        "schemaVersion": result.schema_version,
        "expireOperationId": result.expire_operation_id,
        "artifactId": result.artifact_id,
        "holdId": result.hold_id,
        "requestDigest": result.request_digest,
        "resultCode": result.result_code,
        "holdStatus": result.hold_status.value,
        "artifactVersion": result.artifact_version,
        "retentionLocked": result.retention_locked,
        "auditEventType": result.audit_event_type,
        "outboxEventType": result.outbox_event_type,
    }


def _expire_result_digest(result: ArtifactHoldExpiryResult) -> str:
    return _canonical_digest(_expire_result_payload(result))


def _commit_digest(commit: ArtifactHoldExpiryCommit) -> str:
    minimum_delete = (
        b"null"
        if commit.minimum_delete_at_ms is None
        else str(commit.minimum_delete_at_ms).encode("ascii")
    )
    preimage = _SEPARATOR.join(
        (
            _COMMIT_DOMAIN,
            commit.schema_version.encode("ascii"),
            commit.expire_operation_id.encode("ascii"),
            commit.request_digest.encode("ascii"),
            commit.expected_hold_digest.encode("ascii"),
            commit.authorized_candidate_digest.encode("ascii"),
            commit.authorized_reaper_principal_id.encode(),
            str(commit.authorized_reaper_fencing_token).encode("ascii"),
            str(commit.previous_artifact_version).encode("ascii"),
            str(commit.remaining_active_hold_count).encode("ascii"),
            str(commit.active_ref_count).encode("ascii"),
            minimum_delete,
            str(commit.committed_at_ms).encode("ascii"),
            commit.result_json,
        )
    )
    return hashlib.sha256(preimage).hexdigest()


def _canonical_digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(rfc8785.dumps(dict(payload))).hexdigest()


def _event_digest(payload: Mapping[str, object]) -> str:
    preimage = _SEPARATOR.join(
        (
            _EVENT_DOMAIN,
            rfc8785.dumps(dict(payload)),
        )
    )
    return hashlib.sha256(preimage).hexdigest()


def _require_exact_fields(
    value: Mapping[str, object], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        raise ArtifactHoldExpiryConflict(f"{label} has missing or extra fields")


def _parse_operation(value: object, label: str) -> ArtifactHoldExpiryOperation:
    try:
        return ArtifactHoldExpiryOperation(value)
    except (TypeError, ValueError) as exc:
        raise ArtifactHoldExpiryConflict(f"{label} is unknown") from exc


def _candidate_from_mapping(
    value: object, label: str
) -> ArtifactHoldExpiryCandidate:
    if not isinstance(value, Mapping):
        raise ArtifactHoldExpiryConflict(f"{label} must be an object")
    _require_exact_fields(
        value,
        {
            "schemaVersion",
            "scanOperationId",
            "candidateLeaseId",
            "candidateFencingToken",
            "candidateToken",
            "ownerPrincipalId",
            "ownerInstanceId",
            "issuedAtMs",
            "leaseUntilMs",
            "artifactId",
            "holdId",
            "expectedHoldDigest",
            "expectedArtifactVersion",
            "observedExpiresMs",
            "candidateDigest",
        },
        label,
    )
    candidate = ArtifactHoldExpiryCandidate(
        schema_version=value["schemaVersion"],
        scan_operation_id=value["scanOperationId"],
        candidate_lease_id=value["candidateLeaseId"],
        candidate_fencing_token=value["candidateFencingToken"],
        candidate_token=value["candidateToken"],
        owner_principal_id=value["ownerPrincipalId"],
        owner_instance_id=value["ownerInstanceId"],
        issued_at_ms=value["issuedAtMs"],
        lease_until_ms=value["leaseUntilMs"],
        artifact_id=value["artifactId"],
        hold_id=value["holdId"],
        expected_hold_digest=value["expectedHoldDigest"],
        expected_artifact_version=value["expectedArtifactVersion"],
        observed_expires_ms=value["observedExpiresMs"],
        candidate_digest=value["candidateDigest"],
    )  # type: ignore[arg-type]
    candidate.validate()
    return candidate


def _parse_canonical_object(wire: bytes, label: str) -> dict[str, object]:
    if not isinstance(wire, bytes):
        raise ArtifactHoldExpiryConflict(f"{label} must be bytes")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ArtifactHoldExpiryConflict(f"{label} contains duplicate field")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            wire.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ArtifactHoldExpiryConflict(f"{label} contains {value}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactHoldExpiryConflict(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise ArtifactHoldExpiryConflict(f"{label} must be a JSON object")
    try:
        canonical = rfc8785.dumps(parsed)
    except (TypeError, ValueError) as exc:
        raise ArtifactHoldExpiryConflict(f"{label} is not RFC8785-compatible") from exc
    if canonical != wire:
        raise ArtifactHoldExpiryConflict(f"{label} is not canonical RFC8785 bytes")
    return parsed


def _require_stable_text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ArtifactHoldExpiryConflict(f"{label} must be non-empty trimmed text")
    if "\x00" in value or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ArtifactHoldExpiryConflict(f"{label} contains a control character")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ArtifactHoldExpiryConflict(f"{label} is not valid Unicode text") from exc


def _require_candidate_token(value: object, label: str) -> None:
    _require_stable_text(value, label)
    if not isinstance(value, str) or not _CANDIDATE_TOKEN_RE.fullmatch(value):
        raise ArtifactHoldExpiryConflict(f"{label} must be 32-byte base64url")
    decoded = base64.urlsafe_b64decode(value + "=")
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if len(decoded) != 32 or canonical != value:
        raise ArtifactHoldExpiryConflict(
            f"{label} must be canonical 32-byte base64url"
        )


def _require_sha256(value: object, label: str) -> None:
    _require_stable_text(value, label)
    if not isinstance(value, str) or not _LOWER_SHA256_RE.fullmatch(value):
        raise ArtifactHoldExpiryConflict(f"{label} must be lowercase SHA-256 hex")


def _require_integer(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int = _MAX_SAFE_INTEGER,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArtifactHoldExpiryConflict(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise ArtifactHoldExpiryConflict(f"{label} outside safe integer range")
