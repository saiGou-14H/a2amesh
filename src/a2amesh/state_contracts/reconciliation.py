"""Stable Reconciliation due and claim operation identities."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DUE_DOMAIN = b"a2amesh-recon-due-operation-v1"
_CLAIM_DOMAIN = b"a2amesh-recon-claim-operation-v1"
_SEPARATOR = b"\x00"
_CLAIM_PREFIX = _CLAIM_DOMAIN + _SEPARATOR


class ReconciliationDueKind(StrEnum):
    CLAIM_EXPIRE = "CLAIM_EXPIRE"
    ESCALATE = "ESCALATE"


class ReconciliationClaimOperation(StrEnum):
    ACQUIRE = "ACQUIRE"
    RENEW = "RENEW"
    RELEASE = "RELEASE"
    EXPIRE = "EXPIRE"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True, slots=True)
class SystemClaimIdentity:
    due_operation_id: str
    idempotency_key: str
    claim_operation_id: str


def reconciliation_due_operation_preimage(
    *,
    due_kind: ReconciliationDueKind | str,
    case_id: str,
    observed_revision: int,
    observed_due_ms: int,
    observed_claim_fencing_token: int | None,
) -> bytes:
    kind = _enum_value(ReconciliationDueKind, due_kind, "due_kind")
    case = _stable_text(case_id, "case_id")
    revision = _safe_integer(observed_revision, "observed_revision", minimum=1)
    due_ms = _safe_integer(observed_due_ms, "observed_due_ms", minimum=0)
    if kind == ReconciliationDueKind.CLAIM_EXPIRE:
        token = _safe_integer(
            observed_claim_fencing_token,
            "observed_claim_fencing_token",
            minimum=1,
        )
        token_bytes = str(token).encode("ascii")
    else:
        if observed_claim_fencing_token is not None:
            raise ValueError("ESCALATE requires a null observed claim fencing token")
        token_bytes = b"null"
    return _SEPARATOR.join(
        (
            _DUE_DOMAIN,
            kind.encode("utf-8"),
            case.encode("utf-8"),
            str(revision).encode("ascii"),
            str(due_ms).encode("ascii"),
            token_bytes,
        )
    )


def reconciliation_due_operation_id(
    *,
    due_kind: ReconciliationDueKind | str,
    case_id: str,
    observed_revision: int,
    observed_due_ms: int,
    observed_claim_fencing_token: int | None,
) -> str:
    """Hash the stable due tuple; scanner lease/instance/token are intentionally absent."""

    preimage = reconciliation_due_operation_preimage(
        due_kind=due_kind,
        case_id=case_id,
        observed_revision=observed_revision,
        observed_due_ms=observed_due_ms,
        observed_claim_fencing_token=observed_claim_fencing_token,
    )
    return hashlib.sha256(preimage).hexdigest()


def reconciliation_claim_scope_bytes(
    *,
    case_id: str,
    operation: ReconciliationClaimOperation | str,
    operator_principal_hash: str,
    idempotency_key: str,
) -> bytes:
    case = _stable_text(case_id, "case_id")
    operation_value = _enum_value(
        ReconciliationClaimOperation,
        operation,
        "operation",
    )
    principal_hash = _lower_sha256(
        operator_principal_hash,
        "operator_principal_hash",
    )
    key = _stable_text(idempotency_key, "idempotency_key")
    return _SEPARATOR.join(
        (
            case.encode("utf-8"),
            operation_value.encode("utf-8"),
            principal_hash.encode("ascii"),
            key.encode("utf-8"),
        )
    )


def reconciliation_claim_operation_id(
    *,
    case_id: str,
    operation: ReconciliationClaimOperation | str,
    operator_principal_hash: str,
    idempotency_key: str,
) -> str:
    scope = reconciliation_claim_scope_bytes(
        case_id=case_id,
        operation=operation,
        operator_principal_hash=operator_principal_hash,
        idempotency_key=idempotency_key,
    )
    return hashlib.sha256(_CLAIM_PREFIX + scope).hexdigest()


def system_claim_identity(
    *,
    due_kind: ReconciliationDueKind | str,
    case_id: str,
    observed_revision: int,
    observed_due_ms: int,
    observed_claim_fencing_token: int | None,
    operator_principal_hash: str,
) -> SystemClaimIdentity:
    kind = ReconciliationDueKind(
        _enum_value(ReconciliationDueKind, due_kind, "due_kind")
    )
    operation = (
        ReconciliationClaimOperation.EXPIRE
        if kind is ReconciliationDueKind.CLAIM_EXPIRE
        else ReconciliationClaimOperation.ESCALATE
    )
    due_id = reconciliation_due_operation_id(
        due_kind=kind,
        case_id=case_id,
        observed_revision=observed_revision,
        observed_due_ms=observed_due_ms,
        observed_claim_fencing_token=observed_claim_fencing_token,
    )
    claim_id = reconciliation_claim_operation_id(
        case_id=case_id,
        operation=operation,
        operator_principal_hash=operator_principal_hash,
        idempotency_key=due_id,
    )
    if claim_id == due_id:  # Cryptographically implausible; keeps the domains explicit.
        raise RuntimeError("claim and due operation domains unexpectedly collided")
    return SystemClaimIdentity(
        due_operation_id=due_id,
        idempotency_key=due_id,
        claim_operation_id=claim_id,
    )


def _stable_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty trimmed string")
    if "\x00" in value or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError(f"{label} contains a forbidden control character")
    value.encode("utf-8")
    return value


def _safe_integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if not minimum <= value <= _MAX_SAFE_INTEGER:
        raise ValueError(
            f"{label} must be between {minimum} and {_MAX_SAFE_INTEGER}"
        )
    return value


def _lower_sha256(value: object, label: str) -> str:
    text = _stable_text(value, label)
    if not _LOWER_SHA256_RE.fullmatch(text):
        raise ValueError(f"{label} must be lowercase SHA-256 hex")
    return text


def _enum_value(enum_type, value: object, label: str) -> str:
    try:
        return enum_type(value).value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not in the closed enum") from exc
