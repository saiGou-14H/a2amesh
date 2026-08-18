"""Executable state-plane identity contracts shared by bindings and workers."""

from importlib import import_module

from .dispatch import (
    DispatchContractError,
    DispatchIntent,
    DispatchIntentState,
    accept_dispatch,
    claim_dispatch,
    create_dispatch_intent,
    mark_dispatch_sent,
    reclaim_dispatch,
)
from .lease import LeaseContractError, LeaseGrant, renew_lease, validate_lease_write
from .outbox import (
    OutboxContractError,
    OutboxEvent,
    OutboxState,
    append_event,
    claim_event,
    create_outbox_event,
    mark_published,
    next_publishable,
)
from .reconciliation import (
    ReconciliationClaimOperation,
    ReconciliationDueKind,
    SystemClaimIdentity,
    reconciliation_claim_operation_id,
    reconciliation_claim_scope_bytes,
    reconciliation_due_operation_id,
    reconciliation_due_operation_preimage,
    system_claim_identity,
)
from .task import (
    TaskAggregate,
    TaskClaimDecision,
    TaskClaimKey,
    TaskClaimOutcome,
    TaskContractError,
    evaluate_claim,
)

_ARTIFACT_EXPORTS = frozenset(
    {
        "ARTIFACT_HOLD_CANDIDATE_LEASE_MAX_MS",
        "ArtifactHoldExpiryCandidate",
        "ArtifactHoldExpiryCandidateLedgerEntry",
        "ArtifactHoldExpiryCASState",
        "ArtifactHoldExpiryCommit",
        "ArtifactHoldExpiryConflict",
        "ArtifactHoldExpiryEventRecord",
        "ArtifactHoldExpiryEventSink",
        "ArtifactHoldExpiryLedgerState",
        "ArtifactHoldExpiryOperation",
        "ArtifactHoldExpiryReplayClaimRequest",
        "ArtifactHoldExpiryReplayClaimResult",
        "ArtifactHoldExpiryReplayCurrentAuthority",
        "ArtifactHoldExpiryRequest",
        "ArtifactHoldExpiryResult",
        "ArtifactHoldExpiryScanAuthority",
        "ArtifactHoldExpiryScanRequest",
        "ArtifactHoldExpiryScanResult",
        "ArtifactHoldState",
        "ArtifactHoldStatus",
        "ArtifactLifecycleStatus",
        "apply_artifact_hold_expiry",
        "apply_artifact_hold_expiry_replay_claim",
        "apply_artifact_hold_expiry_scan",
        "artifact_hold_expiry_operation_id",
        "artifact_hold_expiry_preimage",
        "artifact_hold_expiry_replay_claim_operation_id",
        "artifact_hold_expiry_request_digest",
    }
)


def __getattr__(name: str):
    if name in _ARTIFACT_EXPORTS:
        module = import_module(".artifact_hold", __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "DispatchContractError",
    "DispatchIntent",
    "DispatchIntentState",
    "LeaseContractError",
    "LeaseGrant",
    "TaskAggregate",
    "TaskClaimDecision",
    "TaskClaimKey",
    "TaskClaimOutcome",
    "TaskContractError",
    "evaluate_claim",
    "renew_lease",
    "validate_lease_write",
    "accept_dispatch",
    "claim_dispatch",
    "create_dispatch_intent",
    "mark_dispatch_sent",
    "reclaim_dispatch",
    "OutboxContractError",
    "OutboxEvent",
    "OutboxState",
    "append_event",
    "claim_event",
    "create_outbox_event",
    "mark_published",
    "next_publishable",
    "ArtifactHoldExpiryCandidate",
    "ArtifactHoldExpiryCandidateLedgerEntry",
    "ArtifactHoldExpiryCASState",
    "ArtifactHoldExpiryCommit",
    "ArtifactHoldExpiryConflict",
    "ArtifactHoldExpiryEventRecord",
    "ArtifactHoldExpiryEventSink",
    "ArtifactHoldExpiryLedgerState",
    "ArtifactHoldExpiryOperation",
    "ArtifactHoldExpiryRequest",
    "ArtifactHoldExpiryResult",
    "ArtifactHoldExpiryReplayClaimRequest",
    "ArtifactHoldExpiryReplayClaimResult",
    "ArtifactHoldExpiryReplayCurrentAuthority",
    "ArtifactHoldExpiryScanAuthority",
    "ArtifactHoldExpiryScanRequest",
    "ArtifactHoldExpiryScanResult",
    "ArtifactHoldState",
    "ArtifactHoldStatus",
    "ArtifactLifecycleStatus",
    "ARTIFACT_HOLD_CANDIDATE_LEASE_MAX_MS",
    "apply_artifact_hold_expiry",
    "apply_artifact_hold_expiry_replay_claim",
    "apply_artifact_hold_expiry_scan",
    "artifact_hold_expiry_operation_id",
    "artifact_hold_expiry_preimage",
    "artifact_hold_expiry_request_digest",
    "artifact_hold_expiry_replay_claim_operation_id",
    "ReconciliationClaimOperation",
    "ReconciliationDueKind",
    "SystemClaimIdentity",
    "reconciliation_claim_operation_id",
    "reconciliation_claim_scope_bytes",
    "reconciliation_due_operation_id",
    "reconciliation_due_operation_preimage",
    "system_claim_identity",
]
