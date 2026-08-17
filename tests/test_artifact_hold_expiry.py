"""Executable ArtifactHold SCAN/EXPIRE/CAS/replay contract tests."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
import rfc8785

import a2amesh.state_contracts.artifact_hold as artifact_hold_contract
from a2amesh.state_contracts.artifact_hold import (
    ARTIFACT_HOLD_CANDIDATE_LEASE_MAX_MS,
    ArtifactHoldExpiryCandidate,
    ArtifactHoldExpiryCandidateLedgerEntry,
    ArtifactHoldExpiryCASState,
    ArtifactHoldExpiryCommit,
    ArtifactHoldExpiryConflict,
    ArtifactHoldExpiryEventRecord,
    ArtifactHoldExpiryEventSink,
    ArtifactHoldExpiryLedgerState,
    ArtifactHoldExpiryOperation,
    ArtifactHoldExpiryReplayClaimRequest,
    ArtifactHoldExpiryReplayClaimResult,
    ArtifactHoldExpiryRequest,
    ArtifactHoldExpiryResult,
    ArtifactHoldExpiryScanRequest,
    ArtifactHoldExpiryScanResult,
    ArtifactHoldState,
    ArtifactHoldStatus,
    ArtifactLifecycleStatus,
    apply_artifact_hold_expiry,
    apply_artifact_hold_expiry_replay_claim,
    apply_artifact_hold_expiry_scan,
    artifact_hold_expiry_preimage,
    artifact_hold_expiry_replay_claim_operation_id,
)

# TEST-ARTIFACT-HOLD-REPLAY-001: REPLAY_CLAIM authority, evidence binding,
# takeover fencing, strict wire fixture, and fail-closed snapshot coverage.

ROOT = Path(__file__).parents[1]
ARTIFACT_SPEC = ROOT / "docs" / "specs" / "A2AMesh_Artifact与对象存储设计_V1.2.md"
REDIS_SPEC = ROOT / "docs" / "specs" / "A2AMesh_Redis状态平面与数据设计_V1.6.md"
NATS_SPEC = ROOT / "docs" / "specs" / "A2AMesh_A2A协议与NATS集成适配设计_V1.6.md"
CONFIG_SPEC = ROOT / "docs" / "specs" / "A2AMesh_受信配置与变更治理设计_V1.2.md"
IMPLEMENTATION_PLAN = ROOT / "docs" / "specs" / "A2AMesh_开发实施计划.md"
FIXTURE = Path(__file__).parent / "fixtures" / "state_contracts" / "artifact_hold_expiry_v1.json"

SCAN_ID = "11" * 32
TOKEN_A = "A" * 43
TOKEN_B = "B" * 42 + "A"
BASE64URL_C = "Q0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0M"
BASE64URL_D = "REREREREREREREREREREREREREREREREREREREREREQ"
BASE64URL_E = "RUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUU"
BASE64URL_F = "RkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkY"
OWNER_PRINCIPAL = "component:artifact-hold-reaper"
OWNER_INSTANCE = "hold-reaper-01"


def make_candidate(
    *,
    scan_operation_id: str = SCAN_ID,
    candidate_lease_id: str = TOKEN_A,
    candidate_fencing_token: int = 7,
    candidate_token: str = TOKEN_B,
    owner_principal_id: str = OWNER_PRINCIPAL,
    owner_instance_id: str = OWNER_INSTANCE,
    issued_at_ms: int = 1000,
    lease_until_ms: int = 1200,
    artifact_id: str = "artifact-01",
    hold_id: str = "hold-01",
    expected_hold_digest: str = "aa" * 32,
    expected_artifact_version: int = 4,
    observed_expires_ms: int = 1000,
) -> ArtifactHoldExpiryCandidate:
    return ArtifactHoldExpiryCandidate.create(
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
    )


DEFAULT_CANDIDATE = make_candidate()


def make_request(
    *, candidate: ArtifactHoldExpiryCandidate | None = None
) -> ArtifactHoldExpiryRequest:
    return ArtifactHoldExpiryRequest.create(candidate=candidate or make_candidate())


def make_state(
    *,
    artifact_id: str = "artifact-01",
    artifact_version: int = 4,
    artifact_status: ArtifactLifecycleStatus = ArtifactLifecycleStatus.AVAILABLE,
    hold_id: str = "hold-01",
    hold_digest: str = "aa" * 32,
    expires_ms: int | None = 1000,
    hold_status: ArtifactHoldStatus = ArtifactHoldStatus.ACTIVE,
    active_hold_ids: frozenset[str] = frozenset({"hold-01", "hold-02"}),
    active_ref_count: int = 2,
    minimum_delete_at_ms: int | None = None,
    due_indexed: bool = True,
    due_score_ms: int | None = 1000,
    candidate: ArtifactHoldExpiryCandidate | None = DEFAULT_CANDIDATE,
) -> ArtifactHoldState:
    return ArtifactHoldState(
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        artifact_status=artifact_status,
        hold_id=hold_id,
        hold_digest=hold_digest,
        hold_status=hold_status,
        expires_ms=expires_ms,
        active_hold_ids=active_hold_ids,
        active_ref_count=active_ref_count,
        minimum_delete_at_ms=minimum_delete_at_ms,
        due_indexed=due_indexed,
        due_score_ms=due_score_ms,
        candidate=candidate,
    )


def record_scan(
    ledger: ArtifactHoldExpiryLedgerState,
    request: ArtifactHoldExpiryScanRequest,
    result: ArtifactHoldExpiryScanResult,
) -> ArtifactHoldExpiryLedgerState:
    previous_fence = ledger.candidate_fence_high_water
    allocations = []
    for candidate in result.candidates:
        allocation_previous = (
            previous_fence
            if candidate.candidate_fencing_token > previous_fence
            else max(0, candidate.candidate_fencing_token - 1)
        )
        allocations.append(
            artifact_hold_contract._issue_scan_allocation(
                request=request,
                candidate=candidate,
                previous_fence_high_water=allocation_previous,
            )
        )
        previous_fence = max(previous_fence, candidate.candidate_fencing_token)
    return ledger._record_scan(
        request,
        result,
        allocations=tuple(allocations),
    )


def make_cas_state(state: ArtifactHoldState) -> ArtifactHoldExpiryCASState:
    ledger = ArtifactHoldExpiryLedgerState.empty()
    if state.candidate is not None:
        scan_request = ArtifactHoldExpiryScanRequest.create(
            scan_operation_id=state.candidate.scan_operation_id,
            max_candidates=1,
        )
        scan_result = ArtifactHoldExpiryScanResult.create(
            request=scan_request,
            candidates=(state.candidate,),
        )
        ledger = record_scan(ledger, scan_request, scan_result)
    return ArtifactHoldExpiryCASState.create(
        hold_state=state,
        candidate_ledger=ledger,
    )


def forge_unclaimed_replay_authority(
    state: ArtifactHoldExpiryCASState,
    *,
    instance_id: str = "hold-reaper-02",
    fencing_token: int = 8,
    server_now_ms: int = 5000,
) -> tuple[ArtifactHoldExpiryCASState, ArtifactHoldExpiryRequest]:
    scan_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id="22" * 32,
        max_candidates=1,
    )
    candidate = make_candidate(
        scan_operation_id=scan_request.scan_operation_id,
        candidate_lease_id=BASE64URL_C,
        candidate_fencing_token=fencing_token,
        candidate_token=BASE64URL_D,
        owner_instance_id=instance_id,
        issued_at_ms=server_now_ms - 100,
        lease_until_ms=server_now_ms + 100,
    )
    scan_result = ArtifactHoldExpiryScanResult.create(
        request=scan_request,
        candidates=(candidate,),
    )
    updated = replace(
        state,
        candidate_ledger=record_scan(
            state.candidate_ledger,
            scan_request,
            scan_result,
        ),
    )
    updated.validate()
    return updated, make_request(candidate=candidate)


def make_replay_claim_request(
    original_request: ArtifactHoldExpiryRequest,
    *,
    base_commit_digest: str,
    candidate_lease_id: str = BASE64URL_C,
    candidate_token: str = BASE64URL_D,
) -> ArtifactHoldExpiryReplayClaimRequest:
    return ArtifactHoldExpiryReplayClaimRequest.create(
        replay_operation_id=artifact_hold_expiry_replay_claim_operation_id(
            original_request.expire_operation_id,
            candidate_lease_id,
        ),
        expire_operation_id=original_request.expire_operation_id,
        base_commit_digest=base_commit_digest,
        artifact_id=original_request.artifact_id,
        hold_id=original_request.hold_id,
        expected_hold_digest=original_request.expected_hold_digest,
        expected_artifact_version=original_request.expected_artifact_version,
        observed_expires_ms=original_request.observed_expires_ms,
        candidate_lease_id=candidate_lease_id,
        candidate_token=candidate_token,
    )


def claim_replay_authority(
    state: ArtifactHoldExpiryCASState,
    original_request: ArtifactHoldExpiryRequest,
    *,
    server_now_ms: int = 5000,
    lease_until_ms: int = 5200,
    instance_id: str = "hold-reaper-02",
    candidate_lease_id: str = BASE64URL_C,
    candidate_token: str = BASE64URL_D,
) -> tuple[ArtifactHoldExpiryCASState, ArtifactHoldExpiryRequest]:
    claim_request = make_replay_claim_request(
        original_request,
        base_commit_digest=state.commits[0].commit_digest,
        candidate_lease_id=candidate_lease_id,
        candidate_token=candidate_token,
    )
    claimed, claim_result = apply_artifact_hold_expiry_replay_claim(
        claim_request,
        state,
        server_now_ms=server_now_ms,
        lease_until_ms=lease_until_ms,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id=instance_id,
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )
    return claimed, ArtifactHoldExpiryRequest.create(candidate=claim_result.candidate)


def apply_cas(
    request: ArtifactHoldExpiryRequest,
    state: ArtifactHoldExpiryCASState,
    *,
    server_now_ms: int = 1000,
    authenticated_principal: str = OWNER_PRINCIPAL,
    authenticated_instance: str = OWNER_INSTANCE,
    authenticated_fencing_token: int = 7,
    authenticated_component_type: str = "artifact-hold-reaper",
    authenticated_subject: str = "a2a.v1.state.artifact.hold.expire",
) -> tuple[ArtifactHoldExpiryCASState, ArtifactHoldExpiryResult]:
    return apply_artifact_hold_expiry(
        request,
        state,
        server_now_ms=server_now_ms,
        authenticated_reaper_principal_id=authenticated_principal,
        authenticated_reaper_instance_id=authenticated_instance,
        authenticated_reaper_fencing_token=authenticated_fencing_token,
        authenticated_component_type=authenticated_component_type,
        authenticated_subject=authenticated_subject,
    )


def require_commit(
    state: ArtifactHoldExpiryCASState,
    request: ArtifactHoldExpiryRequest,
) -> ArtifactHoldExpiryCommit:
    commit = state.commit_for(request.expire_operation_id)
    if commit is None:
        raise AssertionError("first-write CAS did not persist commit evidence")
    return commit


def apply_first_write(
    request: ArtifactHoldExpiryRequest,
    hold_state: ArtifactHoldState,
    *,
    server_now_ms: int = 1000,
    authenticated_principal: str = OWNER_PRINCIPAL,
    authenticated_instance: str = OWNER_INSTANCE,
    authenticated_fencing_token: int = 7,
    authenticated_component_type: str = "artifact-hold-reaper",
    authenticated_subject: str = "a2a.v1.state.artifact.hold.expire",
) -> tuple[ArtifactHoldExpiryCASState, ArtifactHoldExpiryResult]:
    return apply_cas(
        request,
        make_cas_state(hold_state),
        server_now_ms=server_now_ms,
        authenticated_principal=authenticated_principal,
        authenticated_instance=authenticated_instance,
        authenticated_fencing_token=authenticated_fencing_token,
        authenticated_component_type=authenticated_component_type,
        authenticated_subject=authenticated_subject,
    )


def validate_commit(
    commit: ArtifactHoldExpiryCommit,
    request: ArtifactHoldExpiryRequest,
    *,
    authenticated_principal: str = OWNER_PRINCIPAL,
    authenticated_fencing_token: int = 7,
    authenticated_component_type: str = "artifact-hold-reaper",
    authenticated_subject: str = "a2a.v1.state.artifact.hold.expire",
) -> ArtifactHoldExpiryResult:
    return commit._validate_result_for_request(
        request,
        authenticated_reaper_principal_id=authenticated_principal,
        authenticated_reaper_fencing_token=authenticated_fencing_token,
        authenticated_component_type=authenticated_component_type,
        authenticated_subject=authenticated_subject,
    )


def replace_persisted_commit(
    state: ArtifactHoldExpiryCASState,
    **changes: object,
) -> ArtifactHoldExpiryCASState:
    commit = state.commits[0]
    changed_commit = replace(commit, **changes, commit_digest="")
    changed_commit = replace(
        changed_commit,
        commit_digest=artifact_hold_contract._commit_digest(changed_commit),
    )
    consumed_at_ms = changes.get("committed_at_ms", commit.committed_at_ms)
    changed_entries = tuple(
        replace(entry, consumed_at_ms=consumed_at_ms)
        if entry.consumed_by_expire_operation_id == commit.expire_operation_id
        else entry
        for entry in state.candidate_ledger.candidate_entries
    )
    return replace(
        state,
        candidate_ledger=replace(
            state.candidate_ledger,
            candidate_entries=changed_entries,
        ),
        commits=(changed_commit,),
        audit_records=(
            ArtifactHoldExpiryEventRecord.create(
                sink=ArtifactHoldExpiryEventSink.AUDIT,
                commit=changed_commit,
            ),
        ),
        outbox_records=(
            ArtifactHoldExpiryEventRecord.create(
                sink=ArtifactHoldExpiryEventSink.OUTBOX,
                commit=changed_commit,
            ),
        ),
    )


def replace_consumed_candidate(
    state: ArtifactHoldExpiryCASState,
    candidate: ArtifactHoldExpiryCandidate,
) -> ArtifactHoldExpiryCASState:
    scan_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=candidate.scan_operation_id,
        max_candidates=1,
    )
    scan_result = ArtifactHoldExpiryScanResult.create(
        request=scan_request,
        candidates=(candidate,),
    )
    consumed_entry = next(
        entry for entry in state.candidate_ledger.candidate_entries if entry.consumed
    )
    return replace(
        state,
        candidate_ledger=replace(
            state.candidate_ledger,
            scan_requests=(scan_request,),
            scan_results=(scan_result,),
            candidate_entries=(replace(consumed_entry, candidate=candidate),),
        ),
    )


def test_scan_wire_is_closed_and_binds_exact_candidates() -> None:
    scan_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=SCAN_ID,
        max_candidates=10,
    )
    candidate = make_candidate()
    result = ArtifactHoldExpiryScanResult.create(
        request=scan_request,
        candidates=(candidate,),
    )

    assert scan_request.operation is ArtifactHoldExpiryOperation.SCAN
    assert result.candidates == (candidate,)
    assert result.scan_operation_id == scan_request.scan_operation_id
    assert result.request_digest == scan_request.request_digest
    result.validate()


def test_scan_result_cannot_exceed_requested_candidate_limit() -> None:
    scan_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=SCAN_ID,
        max_candidates=1,
    )
    first = make_candidate()
    second = make_candidate(
        candidate_lease_id="C" * 42 + "A",
        candidate_token="D" * 42 + "A",
        artifact_id="artifact-02",
        hold_id="hold-02",
    )
    with pytest.raises(ArtifactHoldExpiryConflict, match="max_candidates"):
        ArtifactHoldExpiryScanResult.create(
            request=scan_request,
            candidates=(first, second),
        )


def test_scan_result_candidate_authority_values_are_unique() -> None:
    request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=SCAN_ID,
        max_candidates=2,
    )
    first = make_candidate()
    shared = {
        "artifact_id": "artifact-02",
        "hold_id": "hold-02",
        "expected_hold_digest": "bb" * 32,
        "expected_artifact_version": 8,
        "observed_expires_ms": 1100,
    }
    duplicates = (
        make_candidate(
            **shared,
            candidate_lease_id=TOKEN_A,
            candidate_token=BASE64URL_C,
            candidate_fencing_token=8,
        ),
        make_candidate(
            **shared,
            candidate_lease_id=BASE64URL_C,
            candidate_token=TOKEN_B,
            candidate_fencing_token=8,
        ),
        make_candidate(
            **shared,
            candidate_lease_id=BASE64URL_C,
            candidate_token=BASE64URL_D,
            candidate_fencing_token=7,
        ),
    )
    for duplicate in duplicates:
        with pytest.raises(ArtifactHoldExpiryConflict, match="duplicate candidate"):
            ArtifactHoldExpiryScanResult.create(
                request=request,
                candidates=(first, duplicate),
            )


def test_scan_result_uses_one_namespace_for_lease_ids_and_tokens() -> None:
    request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id="44" * 32,
        max_candidates=2,
    )
    first = make_candidate(
        scan_operation_id=request.scan_operation_id,
        candidate_lease_id=TOKEN_A,
        candidate_token=TOKEN_B,
        candidate_fencing_token=7,
        artifact_id="artifact-01",
        hold_id="hold-01",
    )
    cross_role_collision = make_candidate(
        scan_operation_id=request.scan_operation_id,
        candidate_lease_id=BASE64URL_C,
        candidate_token=TOKEN_A,
        candidate_fencing_token=8,
        artifact_id="artifact-02",
        hold_id="hold-02",
    )

    with pytest.raises(ArtifactHoldExpiryConflict, match="authority namespace"):
        ArtifactHoldExpiryScanResult.create(
            request=request,
            candidates=(first, cross_role_collision),
        )


def test_candidate_authority_is_globally_unique_across_scan_ledger() -> None:
    first_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=SCAN_ID,
        max_candidates=1,
    )
    first_result = ArtifactHoldExpiryScanResult.create(
        request=first_request,
        candidates=(make_candidate(),),
    )
    ledger = record_scan(
        ArtifactHoldExpiryLedgerState.empty(),
        first_request,
        first_result,
    )
    second_scan_id = "22" * 32
    second_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=second_scan_id,
        max_candidates=1,
    )
    duplicate_authorities = (
        make_candidate(
            scan_operation_id=second_scan_id,
            candidate_lease_id=TOKEN_A,
            candidate_token=BASE64URL_C,
            candidate_fencing_token=8,
        ),
        make_candidate(
            scan_operation_id=second_scan_id,
            candidate_lease_id=BASE64URL_C,
            candidate_token=TOKEN_B,
            candidate_fencing_token=8,
        ),
        make_candidate(
            scan_operation_id=second_scan_id,
            candidate_lease_id=BASE64URL_C,
            candidate_token=BASE64URL_D,
            candidate_fencing_token=7,
        ),
        make_candidate(
            scan_operation_id=second_scan_id,
            candidate_lease_id=TOKEN_B,
            candidate_token=BASE64URL_C,
            candidate_fencing_token=8,
        ),
    )

    for candidate in duplicate_authorities:
        second_result = ArtifactHoldExpiryScanResult.create(
            request=second_request,
            candidates=(candidate,),
        )
        with pytest.raises(
            ArtifactHoldExpiryConflict,
            match="global candidate|allocation proof",
        ):
            record_scan(ledger, second_request, second_result)


def test_scan_ledger_rejects_candidate_without_state_allocation_proof() -> None:
    request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id="55" * 32,
        max_candidates=1,
    )
    result = ArtifactHoldExpiryScanResult.create(
        request=request,
        candidates=(make_candidate(scan_operation_id=request.scan_operation_id),),
    )
    with pytest.raises(ArtifactHoldExpiryConflict, match="State-issued allocation"):
        ArtifactHoldExpiryLedgerState.empty()._record_scan(request, result)
    assert not hasattr(ArtifactHoldExpiryLedgerState, "record_scan")


def test_scan_snapshot_rejects_missing_or_rebound_allocation_proof() -> None:
    state = make_cas_state(make_state())
    ledger = state.candidate_ledger
    allocation = ledger.scan_allocations[0]
    tampered_ledgers = (
        replace(ledger, scan_allocations=()),
        replace(
            ledger,
            scan_allocations=(replace(allocation, _seal=object()),),
        ),
        replace(
            ledger,
            scan_allocations=(
                replace(
                    allocation,
                    previous_fence_high_water=(
                        allocation.previous_fence_high_water + 1
                    ),
                ),
            ),
        ),
    )
    for tampered in tampered_ledgers:
        with pytest.raises(ArtifactHoldExpiryConflict, match="allocation"):
            tampered.validate()


def test_scan_writer_binds_due_state_and_updates_current_projection() -> None:
    request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id="33" * 32,
        max_candidates=1,
    )
    initial = make_cas_state(make_state(candidate=None))

    updated, result = apply_artifact_hold_expiry_scan(
        request,
        initial,
        server_now_ms=1000,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id=OWNER_INSTANCE,
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )

    candidate = result.candidates[0]
    assert candidate.scan_operation_id == request.scan_operation_id
    assert candidate.owner_principal_id == OWNER_PRINCIPAL
    assert candidate.owner_instance_id == OWNER_INSTANCE
    assert candidate.candidate_fencing_token == 1
    assert candidate.issued_at_ms == 1000
    assert candidate.lease_until_ms == 1000 + ARTIFACT_HOLD_CANDIDATE_LEASE_MAX_MS
    assert candidate.artifact_id == "artifact-01"
    assert candidate.hold_id == "hold-01"
    assert candidate.expected_hold_digest == "aa" * 32
    assert candidate.expected_artifact_version == 4
    assert candidate.observed_expires_ms == 1000
    assert updated.hold_state.candidate == candidate
    replayed, replay_result = apply_artifact_hold_expiry_scan(
        request,
        updated,
        server_now_ms=1000,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id=OWNER_INSTANCE,
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )
    assert replayed == updated
    assert replay_result == result

    with pytest.raises(ArtifactHoldExpiryConflict, match="principal"):
        apply_artifact_hold_expiry_scan(
            request,
            updated,
            server_now_ms=1000,
            authenticated_reaper_principal_id="component:other",
            authenticated_reaper_instance_id=OWNER_INSTANCE,
            authenticated_component_type="artifact-hold-reaper",
            authenticated_subject="a2a.v1.state.artifact.hold.expire",
        )


def test_scan_state_allocator_advances_exactly_from_persisted_high_water() -> None:
    existing = make_candidate(candidate_fencing_token=7)
    initial = make_cas_state(make_state(candidate=existing))
    request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id="66" * 32,
        max_candidates=1,
    )

    updated, result = apply_artifact_hold_expiry_scan(
        request,
        initial,
        server_now_ms=1000,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id=OWNER_INSTANCE,
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )

    candidate = result.candidates[0]
    allocation = updated.candidate_ledger.scan_allocations[-1]
    assert candidate.candidate_fencing_token == 8
    assert allocation.previous_fence_high_water == 7
    assert allocation.candidate == candidate
    assert updated.candidate_ledger.candidate_fence_high_water == 8


def test_scan_writer_does_not_accept_caller_candidate_authority() -> None:
    request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id="44" * 32,
        max_candidates=1,
    )
    state = make_cas_state(make_state(candidate=None))
    with pytest.raises(TypeError, match="candidates"):
        apply_artifact_hold_expiry_scan(
            request,
            state,
            server_now_ms=1000,
            candidates=(make_candidate(scan_operation_id=request.scan_operation_id),),
            authenticated_reaper_principal_id=OWNER_PRINCIPAL,
            authenticated_reaper_instance_id=OWNER_INSTANCE,
            authenticated_component_type="artifact-hold-reaper",
            authenticated_subject="a2a.v1.state.artifact.hold.expire",
        )


@pytest.mark.parametrize(
    "case",
    ("before_due", "terminal_hold", "wrong_owner"),
)
def test_scan_writer_rejects_unauthorized_or_non_due_candidates(case: str) -> None:
    request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id="77" * 32,
        max_candidates=1,
    )
    state = make_cas_state(make_state(candidate=None))
    now = 1000
    principal = OWNER_PRINCIPAL
    instance = OWNER_INSTANCE
    if case == "before_due":
        state = replace(
            state,
            hold_state=replace(state.hold_state, due_score_ms=1001),
        )
        now = 1000
    elif case == "terminal_hold":
        state = make_cas_state(
            make_state(
                hold_status=ArtifactHoldStatus.EXPIRED,
                active_hold_ids=frozenset({"hold-02"}),
                due_indexed=False,
                due_score_ms=None,
                candidate=None,
            )
        )
    else:
        principal = "component:other"

    with pytest.raises(ArtifactHoldExpiryConflict):
        apply_artifact_hold_expiry_scan(
            request,
            state,
            server_now_ms=now,
            authenticated_reaper_principal_id=principal,
            authenticated_reaper_instance_id=instance,
            authenticated_component_type="artifact-hold-reaper",
            authenticated_subject="a2a.v1.state.artifact.hold.expire",
        )


def test_scan_ledger_accepts_monotonic_authority_and_exact_replay() -> None:
    first_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=SCAN_ID,
        max_candidates=1,
    )
    first_result = ArtifactHoldExpiryScanResult.create(
        request=first_request,
        candidates=(make_candidate(),),
    )
    ledger = record_scan(
        ArtifactHoldExpiryLedgerState.empty(),
        first_request,
        first_result,
    )
    second_scan_id = "22" * 32
    second_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=second_scan_id,
        max_candidates=1,
    )
    second_candidate = make_candidate(
        scan_operation_id=second_scan_id,
        candidate_lease_id=BASE64URL_C,
        candidate_token=BASE64URL_D,
        candidate_fencing_token=8,
    )
    second_result = ArtifactHoldExpiryScanResult.create(
        request=second_request,
        candidates=(second_candidate,),
    )

    updated = record_scan(ledger, second_request, second_result)

    assert updated.candidate_fence_high_water == 8
    assert len(updated.scan_results) == 2
    assert len(updated.candidate_entries) == 2
    assert all(
        isinstance(entry, ArtifactHoldExpiryCandidateLedgerEntry)
        for entry in updated.candidate_entries
    )
    assert record_scan(updated, second_request, second_result) == updated

    reordered = replace(
        updated,
        scan_requests=updated.scan_requests[::-1],
        scan_results=updated.scan_results[::-1],
        candidate_entries=updated.candidate_entries[::-1],
    )
    with pytest.raises(ArtifactHoldExpiryConflict, match="append order"):
        reordered.validate()


def test_closed_wire_types_roundtrip_canonical_ingress() -> None:
    scan_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=SCAN_ID,
        max_candidates=10,
    )
    candidate = make_candidate()
    scan_result = ArtifactHoldExpiryScanResult.create(
        request=scan_request,
        candidates=(candidate,),
    )
    expire_request = make_request(candidate=candidate)
    committed, expire_result = apply_first_write(
        expire_request,
        make_state(candidate=candidate),
    )
    commit = require_commit(committed, expire_request)

    assert (
        ArtifactHoldExpiryScanRequest.from_canonical_json(
            scan_request.canonical_json()
        )
        == scan_request
    )
    assert (
        ArtifactHoldExpiryCandidate.from_canonical_json(candidate.canonical_json())
        == candidate
    )
    parsed_scan = ArtifactHoldExpiryScanResult.from_canonical_json(
        scan_result.canonical_json()
    )
    parsed_scan.validate_for(scan_request)
    assert parsed_scan == scan_result
    assert (
        ArtifactHoldExpiryRequest.from_canonical_json(expire_request.canonical_json())
        == expire_request
    )
    parsed_commit = ArtifactHoldExpiryCommit.from_canonical_json(
        commit.canonical_json()
    )
    assert validate_commit(parsed_commit, expire_request) == expire_result
    assert parsed_commit == commit


def test_scan_result_must_validate_against_originating_request() -> None:
    scan_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=SCAN_ID,
        max_candidates=10,
    )
    result = ArtifactHoldExpiryScanResult.create(
        request=scan_request,
        candidates=(make_candidate(),),
    )
    payload = result.to_wire_dict()
    payload["requestDigest"] = "22" * 32
    digest_payload = dict(payload)
    del digest_payload["resultDigest"]
    payload["resultDigest"] = hashlib.sha256(
        rfc8785.dumps(digest_payload)
    ).hexdigest()

    parsed = ArtifactHoldExpiryScanResult.from_canonical_json(rfc8785.dumps(payload))
    with pytest.raises(ArtifactHoldExpiryConflict, match="originating request"):
        parsed.validate_for(scan_request)


def test_strict_ingress_rejects_duplicate_noncanonical_extra_and_wrong_type() -> None:
    scan_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=SCAN_ID,
        max_candidates=10,
    )
    extra_scan = scan_request.to_wire_dict() | {"extra": 0}
    wrong_type_scan = scan_request.to_wire_dict() | {"maxCandidates": True}
    malformed = (
        b'{"schemaVersion":"1","schemaVersion":"1"}',
        b" " + scan_request.canonical_json(),
        rfc8785.dumps(extra_scan),
        rfc8785.dumps(wrong_type_scan),
    )
    for wire in malformed:
        with pytest.raises(ArtifactHoldExpiryConflict):
            ArtifactHoldExpiryScanRequest.from_canonical_json(wire)

    candidate_payload = make_candidate().to_wire_dict() | {"extra": 0}
    with pytest.raises(ArtifactHoldExpiryConflict, match="missing or extra"):
        ArtifactHoldExpiryCandidate.from_canonical_json(
            rfc8785.dumps(candidate_payload)
        )

    expiry_payload = make_request().to_wire_dict() | {"extra": 0}
    with pytest.raises(ArtifactHoldExpiryConflict, match="missing or extra"):
        ArtifactHoldExpiryRequest.from_canonical_json(rfc8785.dumps(expiry_payload))

    unknown_operation = scan_request.to_wire_dict() | {"operation": "DELETE"}
    with pytest.raises(ArtifactHoldExpiryConflict, match="unknown"):
        ArtifactHoldExpiryScanRequest.from_canonical_json(
            rfc8785.dumps(unknown_operation)
        )


def test_expiry_request_rejects_reused_lease_and_token_authority() -> None:
    payload = make_request().to_wire_dict()
    payload["candidateToken"] = payload["candidateLeaseId"]

    with pytest.raises(ArtifactHoldExpiryConflict, match="must differ"):
        ArtifactHoldExpiryRequest.from_canonical_json(rfc8785.dumps(payload))


def test_nested_candidate_and_commit_ingress_are_strict() -> None:
    scan_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=SCAN_ID,
        max_candidates=10,
    )
    scan_result = ArtifactHoldExpiryScanResult.create(
        request=scan_request,
        candidates=(make_candidate(),),
    )
    nested_extra = scan_result.to_wire_dict()
    nested_extra["candidates"][0]["extra"] = 0  # type: ignore[index]
    nested_digest_payload = dict(nested_extra)
    del nested_digest_payload["resultDigest"]
    nested_extra["resultDigest"] = hashlib.sha256(
        rfc8785.dumps(nested_digest_payload)
    ).hexdigest()
    with pytest.raises(ArtifactHoldExpiryConflict, match="missing or extra"):
        ArtifactHoldExpiryScanResult.from_canonical_json(
            rfc8785.dumps(nested_extra)
        )

    request = make_request()
    committed, _ = apply_first_write(request, make_state())
    commit = require_commit(committed, request)
    malformed_commits = (
        b'{"schemaVersion":"1","schemaVersion":"1"}',
        b" " + commit.canonical_json(),
        rfc8785.dumps(commit.to_wire_dict() | {"extra": 0}),
        rfc8785.dumps(commit.to_wire_dict() | {"resultJson": 1}),
    )
    for wire in malformed_commits:
        with pytest.raises(ArtifactHoldExpiryConflict):
            ArtifactHoldExpiryCommit.from_canonical_json(wire)


def test_expiry_result_ingress_is_strict() -> None:
    request = make_request()
    _, result = apply_first_write(request, make_state())
    assert (
        ArtifactHoldExpiryResult.from_canonical_json(result.canonical_json())
        == result
    )
    malformed_results = (
        b'{"schemaVersion":"1","schemaVersion":"1"}',
        b" " + result.canonical_json(),
        rfc8785.dumps(result.to_wire_dict() | {"extra": 0}),
        rfc8785.dumps(result.to_wire_dict() | {"retentionLocked": 1}),
    )
    for wire in malformed_results:
        with pytest.raises(ArtifactHoldExpiryConflict):
            ArtifactHoldExpiryResult.from_canonical_json(wire)


def test_invalid_unicode_is_rejected_as_contract_conflict() -> None:
    with pytest.raises(ArtifactHoldExpiryConflict):
        ArtifactHoldExpiryScanRequest.from_canonical_json(
            b'{"schemaVersion":"\\ud800"}'
        )
    with pytest.raises(ArtifactHoldExpiryConflict):
        artifact_hold_expiry_preimage(
            artifact_id="\ud800",
            hold_id="hold-01",
            expected_hold_digest="aa" * 32,
            expected_artifact_version=4,
            observed_expires_ms=1000,
        )


def test_expiry_cas_updates_due_hold_and_retention_once() -> None:
    state = make_state()
    request = make_request()
    committed, result = apply_first_write(request, state)
    updated = committed.hold_state
    commit = require_commit(committed, request)

    assert updated.hold_status is ArtifactHoldStatus.EXPIRED
    assert updated.active_hold_ids == frozenset({"hold-02"})
    assert updated.artifact_version == 5
    assert updated.due_indexed is False
    assert updated.due_score_ms is None
    assert updated.candidate is None
    assert result.retention_locked is True
    assert result.artifact_version == 5
    assert validate_commit(commit, request) == result


def test_fixture_preserves_explicit_absent_candidate_projection() -> None:
    state = make_state(candidate=None)

    assert state.candidate is None
    snapshot = make_cas_state(state)
    assert snapshot.candidate_ledger.candidate_entries == ()


def test_active_hold_requires_latest_candidate_ledger_projection() -> None:
    snapshot = make_cas_state(make_state())
    missing_projection = replace(
        snapshot,
        hold_state=replace(snapshot.hold_state, candidate=None),
    )

    with pytest.raises(ArtifactHoldExpiryConflict, match="projection"):
        missing_projection.validate()

    next_scan = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id="22" * 32,
        max_candidates=1,
    )
    latest_candidate = make_candidate(
        scan_operation_id=next_scan.scan_operation_id,
        candidate_lease_id=BASE64URL_C,
        candidate_fencing_token=8,
        candidate_token=BASE64URL_D,
    )
    latest_result = ArtifactHoldExpiryScanResult.create(
        request=next_scan,
        candidates=(latest_candidate,),
    )
    latest_ledger = record_scan(
        snapshot.candidate_ledger,
        next_scan,
        latest_result,
    )
    stale_projection = replace(snapshot, candidate_ledger=latest_ledger)
    with pytest.raises(ArtifactHoldExpiryConflict, match="latest"):
        stale_projection.validate()
    replace(
        stale_projection,
        hold_state=replace(snapshot.hold_state, candidate=latest_candidate),
    ).validate()


def test_atomic_cas_snapshot_persists_tombstone_commit_audit_and_outbox() -> None:
    request = make_request()
    before = make_cas_state(make_state())

    updated, result = apply_artifact_hold_expiry(
        request,
        before,
        server_now_ms=1000,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id=OWNER_INSTANCE,
        authenticated_reaper_fencing_token=7,
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )

    assert updated.hold_state.hold_status is ArtifactHoldStatus.EXPIRED
    tombstone = updated.candidate_ledger.candidate_entry_for(TOKEN_A)
    assert tombstone.consumed is True
    assert tombstone.consumed_by_expire_operation_id == request.expire_operation_id
    assert len(updated.commits) == 1
    assert len(updated.audit_records) == 1
    assert len(updated.outbox_records) == 1
    assert updated.audit_records[0].sink is ArtifactHoldExpiryEventSink.AUDIT
    assert updated.outbox_records[0].sink is ArtifactHoldExpiryEventSink.OUTBOX
    assert updated.audit_records[0].commit_digest == updated.commits[0].commit_digest
    assert updated.outbox_records[0].commit_digest == updated.commits[0].commit_digest
    updated.validate()

    replayed, replay_result = apply_artifact_hold_expiry(
        request,
        updated,
        server_now_ms=5000,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id=OWNER_INSTANCE,
        authenticated_reaper_fencing_token=7,
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )
    assert replayed == updated
    assert replay_result == result


def test_cas_snapshot_binds_commit_fence_to_consumed_candidate() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    forged = replace_persisted_commit(
        committed,
        authorized_reaper_fencing_token=request.candidate_fencing_token - 1,
    )

    with pytest.raises(ArtifactHoldExpiryConflict, match="commit fence"):
        forged.validate()


@pytest.mark.parametrize(
    "candidate_changes",
    (
        {"owner_instance_id": "hold-reaper-rebound"},
        {"candidate_lease_id": BASE64URL_C},
        {"candidate_token": BASE64URL_D},
        {"scan_operation_id": "44" * 32},
        {"issued_at_ms": 900},
        {"lease_until_ms": 1300},
    ),
)
def test_commit_binds_complete_consumed_candidate(
    candidate_changes: dict[str, object],
) -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    rebound_candidate = make_candidate(**candidate_changes)  # type: ignore[arg-type]
    rebound = replace_consumed_candidate(committed, rebound_candidate)

    assert rebound.commits[0].commit_digest == committed.commits[0].commit_digest
    with pytest.raises(ArtifactHoldExpiryConflict, match="candidate digest|scan authority"):
        rebound.validate()


def test_public_replay_rejects_consumed_candidate_rebinding() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    rebound_candidate = make_candidate(owner_instance_id="hold-reaper-rebound")
    rebound = replace_consumed_candidate(committed, rebound_candidate)

    with pytest.raises(ArtifactHoldExpiryConflict, match="candidate digest|scan authority"):
        apply_cas(
            make_request(candidate=rebound_candidate),
            rebound,
            server_now_ms=5000,
            authenticated_instance="hold-reaper-rebound",
        )


@pytest.mark.parametrize(
    ("committed_at_ms", "message"),
    ((800, "candidate lease"), (950, "observed expiry"), (1200, "candidate lease")),
)
def test_cas_snapshot_binds_commit_time_to_candidate_due_window(
    committed_at_ms: int,
    message: str,
) -> None:
    if committed_at_ms == 950:
        candidate = make_candidate(issued_at_ms=900)
        request = make_request(candidate=candidate)
        state = make_cas_state(make_state(candidate=candidate))
    else:
        request = make_request()
        state = make_cas_state(make_state())
    committed, _ = apply_cas(request, state)
    forged = replace_persisted_commit(
        committed,
        committed_at_ms=committed_at_ms,
    )

    with pytest.raises(ArtifactHoldExpiryConflict, match=message):
        forged.validate()


@pytest.mark.parametrize(
    "changes",
    (
        {"remaining_active_hold_count": 99},
        {"active_ref_count": 99},
        {"minimum_delete_at_ms": 2000},
    ),
)
def test_cas_snapshot_binds_commit_retention_evidence_to_final_state(
    changes: dict[str, object],
) -> None:
    committed, _ = apply_cas(make_request(), make_cas_state(make_state()))
    forged = replace_persisted_commit(committed, **changes)

    with pytest.raises(ArtifactHoldExpiryConflict, match="retention evidence"):
        forged.validate()


def test_atomic_cas_snapshot_rejects_partial_or_reactivated_commit() -> None:
    request = make_request()
    updated, _ = apply_artifact_hold_expiry(
        request,
        make_cas_state(make_state()),
        server_now_ms=1000,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id=OWNER_INSTANCE,
        authenticated_reaper_fencing_token=7,
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )
    consumed = updated.candidate_ledger.candidate_entries[0]
    active_entry = replace(
        consumed,
        consumed_by_expire_operation_id=None,
        consumed_at_ms=None,
    )
    reactivated_ledger = replace(
        updated.candidate_ledger,
        candidate_entries=(active_entry,),
    )
    forged_states = (
        replace(updated, commits=()),
        replace(updated, audit_records=()),
        replace(updated, outbox_records=()),
        replace(updated, candidate_ledger=reactivated_ledger),
    )

    for forged in forged_states:
        with pytest.raises(ArtifactHoldExpiryConflict):
            forged.validate()


def test_public_expiry_writer_requires_snapshot_and_internal_commit_lookup() -> None:
    request = make_request()
    with pytest.raises(ArtifactHoldExpiryConflict, match="atomic CAS snapshot"):
        apply_artifact_hold_expiry(
            request,
            make_state(),  # type: ignore[arg-type]
            server_now_ms=1000,
            authenticated_reaper_principal_id=OWNER_PRINCIPAL,
            authenticated_reaper_instance_id=OWNER_INSTANCE,
            authenticated_reaper_fencing_token=7,
            authenticated_component_type="artifact-hold-reaper",
            authenticated_subject="a2a.v1.state.artifact.hold.expire",
        )

    committed, _ = apply_cas(request, make_cas_state(make_state()))
    commit = committed.commits[0]
    with pytest.raises(TypeError):
        apply_artifact_hold_expiry(
            request,
            committed,
            server_now_ms=1000,
            authenticated_reaper_principal_id=OWNER_PRINCIPAL,
            authenticated_reaper_instance_id=OWNER_INSTANCE,
            authenticated_reaper_fencing_token=7,
            authenticated_component_type="artifact-hold-reaper",
            authenticated_subject="a2a.v1.state.artifact.hold.expire",
            stored_commit=commit,  # type: ignore[call-arg]
        )


def test_consumed_candidate_authority_cannot_be_reissued() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    next_scan_id = "22" * 32
    next_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=next_scan_id,
        max_candidates=1,
    )
    reused = make_candidate(
        scan_operation_id=next_scan_id,
        candidate_fencing_token=8,
        candidate_token=BASE64URL_C,
    )
    next_result = ArtifactHoldExpiryScanResult.create(
        request=next_request,
        candidates=(reused,),
    )

    with pytest.raises(ArtifactHoldExpiryConflict, match="global candidate"):
        record_scan(committed.candidate_ledger, next_request, next_result)


def test_cas_snapshot_rejects_tampered_event_evidence() -> None:
    committed, _ = apply_cas(make_request(), make_cas_state(make_state()))
    tampered_audit = replace(
        committed.audit_records[0],
        commit_digest="bb" * 32,
    )

    with pytest.raises(ArtifactHoldExpiryConflict):
        replace(committed, audit_records=(tampered_audit,)).validate()


def test_minimum_delete_time_remains_part_of_retention_projection() -> None:
    state = make_state(
        active_hold_ids=frozenset({"hold-01"}),
        active_ref_count=0,
        minimum_delete_at_ms=2000,
    )
    committed, result = apply_first_write(
        make_request(),
        state,
        server_now_ms=1000,
    )
    updated = committed.hold_state
    assert updated.retention_locked_at(1000) is True
    assert updated.retention_locked_at(2000) is False
    assert result.retention_locked is True


def test_missing_or_changed_due_index_fails_closed() -> None:
    for state in (
        make_state(due_indexed=False, due_score_ms=None),
        make_state(due_score_ms=999),
    ):
        with pytest.raises(ArtifactHoldExpiryConflict, match="due"):
            apply_first_write(make_request(), state)


def test_candidate_lease_and_authenticated_owner_are_current() -> None:
    request = make_request()
    state = make_state()

    with pytest.raises(ArtifactHoldExpiryConflict, match="fence"):
        apply_first_write(request, state, authenticated_fencing_token=8)
    with pytest.raises(ArtifactHoldExpiryConflict, match="lease"):
        apply_first_write(request, state, server_now_ms=1200)
    with pytest.raises(ArtifactHoldExpiryConflict, match="principal"):
        apply_first_write(request, state, authenticated_principal="component:old-reaper")
    with pytest.raises(ArtifactHoldExpiryConflict, match="instance"):
        apply_first_write(request, state, authenticated_instance="old-instance")


def test_only_hold_reaper_on_literal_subject_can_expire() -> None:
    request = make_request()
    state = make_state()

    with pytest.raises(ArtifactHoldExpiryConflict, match="only artifact-hold-reaper"):
        apply_first_write(
            request,
            state,
            authenticated_component_type="artifact-adapter",
        )
    with pytest.raises(ArtifactHoldExpiryConflict, match="forbidden subject"):
        apply_first_write(
            request,
            state,
            authenticated_subject="a2a.v1.state.artifact.delete",
        )


def test_request_must_match_scan_candidate_ledger_identity() -> None:
    candidate = make_candidate()
    state = make_state(candidate=candidate)
    variants = (
        replace(make_request(), scan_operation_id="22" * 32),
        replace(make_request(), candidate_lease_id=TOKEN_B),
        replace(make_request(), candidate_fencing_token=8),
        replace(make_request(), candidate_token=TOKEN_A),
    )
    for request in variants:
        with pytest.raises(ArtifactHoldExpiryConflict):
            apply_first_write(request, state)


def test_takeover_changes_candidate_authority_but_not_business_identity() -> None:
    old_request = make_request()
    new_candidate = make_candidate(
        scan_operation_id="22" * 32,
        candidate_lease_id=TOKEN_B,
        candidate_fencing_token=8,
        candidate_token=TOKEN_A,
        owner_instance_id="hold-reaper-02",
        issued_at_ms=950,
        lease_until_ms=1300,
    )
    takeover_request = make_request(candidate=new_candidate)
    assert takeover_request.expire_operation_id == old_request.expire_operation_id
    assert takeover_request.request_digest == old_request.request_digest

    state = make_state(candidate=new_candidate)
    committed, result = apply_first_write(
        takeover_request,
        state,
        authenticated_instance="hold-reaper-02",
        authenticated_fencing_token=8,
    )
    updated = committed.hold_state
    assert updated.artifact_version == 5
    assert result.expire_operation_id == old_request.expire_operation_id


def test_stale_unsafe_and_terminal_candidates_are_rejected() -> None:
    cases = (
        (make_request(), make_state(), 999),
        (make_request(), make_state(artifact_version=5), 1000),
        (make_request(), make_state(hold_digest="bb" * 32), 1000),
        (
            make_request(),
            make_state(artifact_status=ArtifactLifecycleStatus.DELETING),
            1000,
        ),
        (
            make_request(),
            make_state(artifact_status=ArtifactLifecycleStatus.DELETED),
            1000,
        ),
        (
            make_request(),
            make_state(
                hold_status=ArtifactHoldStatus.RELEASED,
                active_hold_ids=frozenset({"hold-02"}),
                due_indexed=False,
                due_score_ms=None,
                candidate=None,
            ),
            1000,
        ),
        (
            make_request(),
            make_state(
                expires_ms=None,
                due_indexed=False,
                due_score_ms=None,
                candidate=None,
            ),
            1000,
        ),
    )
    for request, state, now in cases:
        with pytest.raises(ArtifactHoldExpiryConflict):
            apply_first_write(request, state, server_now_ms=now)


def test_commit_validation_requires_authenticated_replay_context() -> None:
    request = make_request()
    committed, _ = apply_first_write(request, make_state())
    commit = require_commit(committed, request)

    assert not hasattr(commit, "validate_for")
    with pytest.raises(TypeError):
        commit._validate_result_for_request(request)  # type: ignore[call-arg]


def test_low_level_higher_fence_rejects_unverified_candidate() -> None:
    request = make_request()
    committed, _ = apply_first_write(request, make_state())
    commit = require_commit(committed, request)
    forged_candidate = make_candidate(
        scan_operation_id="22" * 32,
        candidate_lease_id=BASE64URL_C,
        candidate_fencing_token=8,
        candidate_token=BASE64URL_D,
        owner_instance_id="hold-reaper-02",
        issued_at_ms=4900,
        lease_until_ms=5200,
    )

    with pytest.raises(ArtifactHoldExpiryConflict, match="ledger-verified"):
        commit._validate_result_for_request(
            ArtifactHoldExpiryRequest.create(candidate=forged_candidate),
            authenticated_reaper_principal_id=OWNER_PRINCIPAL,
            authenticated_reaper_fencing_token=8,
            authenticated_component_type="artifact-hold-reaper",
            authenticated_subject="a2a.v1.state.artifact.hold.expire",
            verified_replay_authority=forged_candidate,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"authenticated_principal": "component:other"}, "principal"),
        ({"authenticated_fencing_token": 6}, "fence"),
        ({"authenticated_component_type": "artifact-adapter"}, "only artifact"),
        ({"authenticated_subject": "a2a.v1.state.artifact.delete"}, "subject"),
    ),
)
def test_commit_validation_rejects_wrong_authenticated_context(
    overrides: dict[str, object], message: str
) -> None:
    request = make_request()
    committed, _ = apply_first_write(request, make_state())
    commit = require_commit(committed, request)

    with pytest.raises(ArtifactHoldExpiryConflict, match=message):
        validate_commit(commit, request, **overrides)  # type: ignore[arg-type]


def test_replay_same_fence_requires_owner_and_higher_fence_requires_claim() -> None:
    request = make_request()
    committed, expected = apply_cas(request, make_cas_state(make_state()))

    with pytest.raises(ArtifactHoldExpiryConflict, match="instance"):
        apply_cas(
            request,
            committed,
            authenticated_instance="hold-reaper-02",
            authenticated_fencing_token=7,
        )

    with pytest.raises(ArtifactHoldExpiryConflict, match="candidate"):
        apply_cas(
            request,
            committed,
            server_now_ms=5000,
            authenticated_instance="hold-reaper-02",
            authenticated_fencing_token=8,
        )

    claimed, replay_request = claim_replay_authority(
        committed,
        request,
    )
    replayed, result = apply_cas(
        replay_request,
        claimed,
        server_now_ms=5000,
        authenticated_instance="hold-reaper-02",
        authenticated_fencing_token=8,
    )
    assert replay_request.candidate_fencing_token == 8
    assert replayed == claimed
    assert result == expected

    with pytest.raises(ArtifactHoldExpiryConflict, match="fence"):
        apply_cas(
            request,
            committed,
            server_now_ms=5000,
            authenticated_fencing_token=6,
        )


def test_commit_validator_rejects_bare_higher_fence_without_candidate() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    commit = require_commit(committed, request)

    with pytest.raises(ArtifactHoldExpiryConflict, match="ledger-verified authority"):
        validate_commit(
            commit,
            request,
            authenticated_fencing_token=8,
        )


def test_replay_claim_wire_is_strict_and_exactly_idempotent() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    claim_request = make_replay_claim_request(
        request,
        base_commit_digest=committed.commits[0].commit_digest,
    )

    parsed_request = ArtifactHoldExpiryReplayClaimRequest.from_canonical_json(
        claim_request.canonical_json()
    )
    assert parsed_request == claim_request

    claimed, first = apply_artifact_hold_expiry_replay_claim(
        claim_request,
        committed,
        server_now_ms=5000,
        lease_until_ms=5200,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id="hold-reaper-02",
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )
    parsed_result = artifact_hold_contract.ArtifactHoldExpiryReplayClaimResult.from_canonical_json(
        first.canonical_json()
    )
    parsed_result.validate_for(claim_request)
    assert parsed_result == first

    replayed_state, replayed_result = apply_artifact_hold_expiry_replay_claim(
        claim_request,
        claimed,
        server_now_ms=5100,
        lease_until_ms=5300,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id="hold-reaper-02",
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )
    assert replayed_state == claimed
    assert replayed_result == first
    assert claimed.candidate_ledger.candidate_entry_for(
        first.candidate.candidate_lease_id
    ).consumed is False

    with pytest.raises(ArtifactHoldExpiryConflict):
        ArtifactHoldExpiryReplayClaimRequest.from_canonical_json(
            rfc8785.dumps(claim_request.to_wire_dict() | {"extra": 0})
        )


def test_replay_claim_binds_base_commit_digest() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    claim_request = make_replay_claim_request(
        request,
        base_commit_digest=committed.commits[0].commit_digest,
    )
    forged_request = replace(
        claim_request,
        base_commit_digest="33" * 32,
        request_digest="",
    )
    forged_request = replace(
        forged_request,
        request_digest=artifact_hold_contract._replay_claim_request_digest(
            forged_request
        ),
    )
    with pytest.raises(ArtifactHoldExpiryConflict, match="base commit digest"):
        apply_artifact_hold_expiry_replay_claim(
            forged_request,
            committed,
            server_now_ms=5000,
            lease_until_ms=5200,
            authenticated_reaper_principal_id=OWNER_PRINCIPAL,
            authenticated_reaper_instance_id="hold-reaper-02",
            authenticated_component_type="artifact-hold-reaper",
            authenticated_subject="a2a.v1.state.artifact.hold.expire",
        )

    claimed, claim_result = apply_artifact_hold_expiry_replay_claim(
        claim_request,
        committed,
        server_now_ms=5000,
        lease_until_ms=5200,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id="hold-reaper-02",
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )
    forged_result = replace(
        claim_result,
        base_commit_digest="33" * 32,
        result_digest="",
    )
    forged_result = replace(
        forged_result,
        result_digest=artifact_hold_contract._replay_claim_result_digest(
            forged_result
        ),
    )
    with pytest.raises(ArtifactHoldExpiryConflict, match="originating claim request"):
        replace(
            claimed,
            candidate_ledger=replace(
                claimed.candidate_ledger,
                replay_claim_results=(forged_result,),
            ),
        ).validate()


def test_replay_candidate_must_be_persisted_live_and_exactly_owned() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    forged_candidate = make_candidate(
        scan_operation_id="22" * 32,
        candidate_lease_id=BASE64URL_C,
        candidate_fencing_token=8,
        candidate_token=BASE64URL_D,
        owner_instance_id="hold-reaper-02",
        issued_at_ms=4900,
        lease_until_ms=5200,
    )
    forged_request = ArtifactHoldExpiryRequest.create(candidate=forged_candidate)
    with pytest.raises(ArtifactHoldExpiryConflict, match="candidate"):
        apply_cas(
            forged_request,
            committed,
            server_now_ms=5000,
            authenticated_instance="hold-reaper-02",
            authenticated_fencing_token=8,
        )

    claimed, replay_request = claim_replay_authority(
        committed,
        request,
        lease_until_ms=5050,
    )
    with pytest.raises(ArtifactHoldExpiryConflict, match="lease"):
        apply_cas(
            replay_request,
            claimed,
            server_now_ms=5050,
            authenticated_instance="hold-reaper-02",
            authenticated_fencing_token=8,
        )
    for instance, fence in (("wrong-instance", 8), ("hold-reaper-02", 9)):
        with pytest.raises(ArtifactHoldExpiryConflict, match="persisted candidate"):
            apply_cas(
                replay_request,
                claimed,
                server_now_ms=5000,
                authenticated_instance=instance,
                authenticated_fencing_token=fence,
            )


def test_replay_claim_lease_duration_is_bounded() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))

    with pytest.raises(ArtifactHoldExpiryConflict, match="maximum candidate lease"):
        claim_replay_authority(
            committed,
            request,
            server_now_ms=5000,
            lease_until_ms=5000 + ARTIFACT_HOLD_CANDIDATE_LEASE_MAX_MS + 1,
        )

    claimed, _ = claim_replay_authority(
        committed,
        request,
        server_now_ms=5000,
        lease_until_ms=5000 + ARTIFACT_HOLD_CANDIDATE_LEASE_MAX_MS,
    )
    candidate = claimed.candidate_ledger.replay_claim_results[-1].candidate
    assert candidate.lease_until_ms - candidate.issued_at_ms == (
        ARTIFACT_HOLD_CANDIDATE_LEASE_MAX_MS
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("owner_instance_id", "hold-reaper-rebound"),
        ("lease_until_ms", 5500),
    ),
)
def test_replay_candidate_authority_rebinding_is_rejected(
    field: str,
    value: object,
) -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    claimed, _ = claim_replay_authority(
        committed,
        request,
        server_now_ms=5000,
        lease_until_ms=5200,
        instance_id="hold-reaper-02",
    )
    ledger = claimed.candidate_ledger
    stored_result = ledger.replay_claim_results[-1]
    stored_candidate = stored_result.candidate
    candidate_without_digest = replace(stored_candidate, **{field: value})
    rebound_candidate = replace(
        candidate_without_digest,
        candidate_digest=artifact_hold_contract._canonical_digest(
            artifact_hold_contract._candidate_payload(candidate_without_digest)
        ),
    )
    result_without_digest = replace(stored_result, candidate=rebound_candidate)
    rebound_result = replace(
        result_without_digest,
        result_digest=artifact_hold_contract._replay_claim_result_digest(
            result_without_digest
        ),
    )
    entries = list(ledger.candidate_entries)
    entry_index = next(
        index
        for index, entry in enumerate(entries)
        if entry.candidate == stored_candidate
    )
    entries[entry_index] = replace(entries[entry_index], candidate=rebound_candidate)
    rebound_ledger = replace(
        ledger,
        replay_claim_results=ledger.replay_claim_results[:-1] + (rebound_result,),
        candidate_entries=tuple(entries),
    )
    rebound_state = replace(claimed, candidate_ledger=rebound_ledger)

    with pytest.raises(ArtifactHoldExpiryConflict, match="replay current authority"):
        rebound_state.validate()

    replay_request = ArtifactHoldExpiryRequest.create(candidate=rebound_candidate)
    with pytest.raises(ArtifactHoldExpiryConflict, match="replay current authority"):
        apply_artifact_hold_expiry(
            replay_request,
            rebound_state,
            server_now_ms=5050,
            authenticated_reaper_principal_id=OWNER_PRINCIPAL,
            authenticated_reaper_instance_id=rebound_candidate.owner_instance_id,
            authenticated_reaper_fencing_token=8,
            authenticated_component_type="artifact-hold-reaper",
            authenticated_subject="a2a.v1.state.artifact.hold.expire",
        )


def test_scan_candidate_authority_rebinding_is_rejected() -> None:
    committed = make_cas_state(make_state())
    ledger = committed.candidate_ledger
    scan_result = ledger.scan_results[-1]
    stored_candidate = scan_result.candidates[0]
    candidate_without_digest = replace(
        stored_candidate,
        owner_instance_id="hold-reaper-rebound",
    )
    rebound_candidate = replace(
        candidate_without_digest,
        candidate_digest=artifact_hold_contract._canonical_digest(
            artifact_hold_contract._candidate_payload(candidate_without_digest)
        ),
    )
    rebound_scan_without_digest = replace(
        scan_result,
        candidates=(rebound_candidate,),
    )
    rebound_scan_result = replace(
        rebound_scan_without_digest,
        result_digest=artifact_hold_contract._scan_result_digest(
            rebound_scan_without_digest
        ),
    )
    entries = tuple(
        replace(entry, candidate=rebound_candidate)
        if entry.candidate == stored_candidate
        else entry
        for entry in ledger.candidate_entries
    )
    rebound_ledger = replace(
        ledger,
        scan_results=(rebound_scan_result,),
        candidate_entries=entries,
    )
    rebound_state = replace(
        committed,
        hold_state=replace(committed.hold_state, candidate=rebound_candidate),
        candidate_ledger=rebound_ledger,
    )

    with pytest.raises(ArtifactHoldExpiryConflict, match="scan authority"):
        rebound_state.validate()

    rebound_request = ArtifactHoldExpiryRequest.create(candidate=rebound_candidate)
    with pytest.raises(ArtifactHoldExpiryConflict, match="scan authority"):
        apply_artifact_hold_expiry(
            rebound_request,
            rebound_state,
            server_now_ms=1000,
            authenticated_reaper_principal_id=OWNER_PRINCIPAL,
            authenticated_reaper_instance_id="hold-reaper-rebound",
            authenticated_reaper_fencing_token=7,
            authenticated_component_type="artifact-hold-reaper",
            authenticated_subject="a2a.v1.state.artifact.hold.expire",
        )


def test_live_replay_claim_blocks_new_claim_id_without_advancing_fence() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    first_state, _ = claim_replay_authority(
        committed,
        request,
        lease_until_ms=5200,
    )

    with pytest.raises(ArtifactHoldExpiryConflict, match="current replay authority"):
        claim_replay_authority(
            first_state,
            request,
            server_now_ms=5100,
            lease_until_ms=5300,
            instance_id="hold-reaper-03",
            candidate_lease_id=BASE64URL_E,
            candidate_token=BASE64URL_F,
        )

    assert first_state.candidate_ledger.candidate_fence_high_water == 8
    assert len(first_state.candidate_ledger.replay_claim_results) == 1


def test_newer_replay_claim_supersedes_all_lower_authorities() -> None:
    request = make_request()
    committed, expected = apply_cas(request, make_cas_state(make_state()))
    first_state, first_replay = claim_replay_authority(
        committed,
        request,
        lease_until_ms=5050,
    )
    second_state, second_replay = claim_replay_authority(
        first_state,
        request,
        server_now_ms=5050,
        lease_until_ms=5250,
        instance_id="hold-reaper-03",
        candidate_lease_id=BASE64URL_E,
        candidate_token=BASE64URL_F,
    )

    with pytest.raises(ArtifactHoldExpiryConflict, match="superseded"):
        apply_cas(
            first_replay,
            second_state,
            server_now_ms=5100,
            authenticated_instance="hold-reaper-02",
            authenticated_fencing_token=8,
        )
    with pytest.raises(ArtifactHoldExpiryConflict, match="superseded"):
        apply_cas(
            request,
            second_state,
            server_now_ms=5100,
            authenticated_fencing_token=7,
        )

    replayed, result = apply_cas(
        second_replay,
        second_state,
        server_now_ms=5100,
        authenticated_instance="hold-reaper-03",
        authenticated_fencing_token=9,
    )
    assert replayed == second_state
    assert result == expected


def test_expired_replay_claim_requires_new_id_and_advances_fence() -> None:
    request = make_request()
    committed, expected = apply_cas(request, make_cas_state(make_state()))
    first_claim = make_replay_claim_request(
        request,
        base_commit_digest=committed.commits[0].commit_digest,
    )
    first_state, first_result = apply_artifact_hold_expiry_replay_claim(
        first_claim,
        committed,
        server_now_ms=5000,
        lease_until_ms=5050,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id="hold-reaper-02",
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )

    conflicting_request = make_replay_claim_request(
        request,
        base_commit_digest=committed.commits[0].commit_digest,
        candidate_token=BASE64URL_F,
    )
    with pytest.raises(ArtifactHoldExpiryConflict, match="different request"):
        apply_artifact_hold_expiry_replay_claim(
            conflicting_request,
            first_state,
            server_now_ms=5010,
            lease_until_ms=5200,
            authenticated_reaper_principal_id=OWNER_PRINCIPAL,
            authenticated_reaper_instance_id="hold-reaper-02",
            authenticated_component_type="artifact-hold-reaper",
            authenticated_subject="a2a.v1.state.artifact.hold.expire",
        )

    second_claim = make_replay_claim_request(
        request,
        base_commit_digest=committed.commits[0].commit_digest,
        candidate_lease_id=BASE64URL_E,
        candidate_token=BASE64URL_F,
    )
    second_state, second_result = apply_artifact_hold_expiry_replay_claim(
        second_claim,
        first_state,
        server_now_ms=5050,
        lease_until_ms=5250,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id="hold-reaper-03",
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )
    assert first_result.candidate.candidate_fencing_token == 8
    assert second_result.candidate.candidate_fencing_token == 9
    assert first_claim.replay_operation_id != second_claim.replay_operation_id
    assert second_state.candidate_ledger.candidate_fence_high_water == 9

    with pytest.raises(ArtifactHoldExpiryConflict, match="superseded"):
        apply_cas(
            ArtifactHoldExpiryRequest.create(candidate=first_result.candidate),
            second_state,
            server_now_ms=5050,
            authenticated_instance="hold-reaper-02",
            authenticated_fencing_token=8,
        )
    replayed, replay_result = apply_cas(
        ArtifactHoldExpiryRequest.create(candidate=second_result.candidate),
        second_state,
        server_now_ms=5100,
        authenticated_instance="hold-reaper-03",
        authenticated_fencing_token=9,
    )
    assert replayed == second_state
    assert replay_result == expected


def test_replay_claim_idempotency_returns_exact_expired_evidence() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    claim_request = make_replay_claim_request(
        request,
        base_commit_digest=committed.commits[0].commit_digest,
    )
    claimed, first_result = apply_artifact_hold_expiry_replay_claim(
        claim_request,
        committed,
        server_now_ms=5000,
        lease_until_ms=5050,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id="hold-reaper-02",
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )

    replayed, replay_result = apply_artifact_hold_expiry_replay_claim(
        claim_request,
        claimed,
        server_now_ms=5060,
        lease_until_ms=0,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id="hold-reaper-02",
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )
    assert replayed == claimed
    assert replay_result == first_result


def test_replay_claim_snapshot_tampering_fails_closed() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    claim_request = make_replay_claim_request(
        request,
        base_commit_digest=committed.commits[0].commit_digest,
    )
    claimed, claim_result = apply_artifact_hold_expiry_replay_claim(
        claim_request,
        committed,
        server_now_ms=5000,
        lease_until_ms=5200,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id="hold-reaper-02",
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )

    without_commit = replace(
        claimed,
        commits=(),
        audit_records=(),
        outbox_records=(),
    )
    with pytest.raises(ArtifactHoldExpiryConflict, match="no committed"):
        without_commit.validate()

    consumed_entries = tuple(
        replace(
            entry,
            consumed_by_expire_operation_id="33" * 32,
            consumed_at_ms=5001,
        )
        if entry.candidate == claim_result.candidate
        else entry
        for entry in claimed.candidate_ledger.candidate_entries
    )
    with pytest.raises(ArtifactHoldExpiryConflict, match="tombstones"):
        replace(
            claimed,
            candidate_ledger=replace(
                claimed.candidate_ledger,
                candidate_entries=consumed_entries,
            ),
        ).validate()

    candidate = claim_result.candidate
    wrong_owner_candidate = ArtifactHoldExpiryCandidate.create(
        scan_operation_id=candidate.scan_operation_id,
        candidate_lease_id=candidate.candidate_lease_id,
        candidate_fencing_token=candidate.candidate_fencing_token,
        candidate_token=candidate.candidate_token,
        owner_principal_id="component:other",
        owner_instance_id=candidate.owner_instance_id,
        issued_at_ms=candidate.issued_at_ms,
        lease_until_ms=candidate.lease_until_ms,
        artifact_id=candidate.artifact_id,
        hold_id=candidate.hold_id,
        expected_hold_digest=candidate.expected_hold_digest,
        expected_artifact_version=candidate.expected_artifact_version,
        observed_expires_ms=candidate.observed_expires_ms,
    )
    wrong_owner_result = ArtifactHoldExpiryReplayClaimResult.create(
        request=claim_request,
        candidate=wrong_owner_candidate,
    )
    wrong_owner_entries = tuple(
        replace(entry, candidate=wrong_owner_candidate)
        if entry.candidate == candidate
        else entry
        for entry in claimed.candidate_ledger.candidate_entries
    )
    with pytest.raises(
        ArtifactHoldExpiryConflict,
        match="replay current authority|committed expiry",
    ):
        replace(
            claimed,
            candidate_ledger=replace(
                claimed.candidate_ledger,
                replay_claim_results=(wrong_owner_result,),
                candidate_entries=wrong_owner_entries,
            ),
        ).validate()


def test_replay_claim_rejects_wrong_commit_tuple_and_actor() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    claim_request = make_replay_claim_request(
        request,
        base_commit_digest=committed.commits[0].commit_digest,
    )

    with pytest.raises(ArtifactHoldExpiryConflict, match="due tuple"):
        ArtifactHoldExpiryReplayClaimRequest.create(
            replay_operation_id=claim_request.replay_operation_id,
            expire_operation_id=claim_request.expire_operation_id,
            base_commit_digest=claim_request.base_commit_digest,
            artifact_id="artifact-other",
            hold_id=claim_request.hold_id,
            expected_hold_digest=claim_request.expected_hold_digest,
            expected_artifact_version=claim_request.expected_artifact_version,
            observed_expires_ms=claim_request.observed_expires_ms,
            candidate_lease_id=claim_request.candidate_lease_id,
            candidate_token=claim_request.candidate_token,
        )
    with pytest.raises(ArtifactHoldExpiryConflict, match="principal"):
        apply_artifact_hold_expiry_replay_claim(
            claim_request,
            committed,
            server_now_ms=5000,
            lease_until_ms=5200,
            authenticated_reaper_principal_id="component:other",
            authenticated_reaper_instance_id="hold-reaper-02",
            authenticated_component_type="artifact-hold-reaper",
            authenticated_subject="a2a.v1.state.artifact.hold.expire",
        )


def test_terminal_snapshot_rejects_newer_unconsumed_candidate_authority() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))

    with pytest.raises(ArtifactHoldExpiryConflict, match="terminal.*candidate"):
        forge_unclaimed_replay_authority(committed)


def test_replay_rejects_active_current_state_even_after_version_advance() -> None:
    request = make_request()
    committed, result = apply_cas(request, make_cas_state(make_state()))
    later_version = result.artifact_version + 1
    later_active = replace(
        make_state(artifact_version=later_version),
        candidate=None,
    )
    later_active.validate()
    forged = replace(committed, hold_state=later_active)

    with pytest.raises(ArtifactHoldExpiryConflict, match="committed expiry"):
        apply_cas(request, forged)


def test_replay_uses_immutable_commit_not_mutable_current_state() -> None:
    request = make_request()
    committed, first_result = apply_cas(request, make_cas_state(make_state()))
    later = replace(
        committed,
        hold_state=replace(
            committed.hold_state,
            artifact_version=9,
            artifact_status=ArtifactLifecycleStatus.QUARANTINED,
        ),
    )

    replayed, replay_result = apply_cas(
        request,
        later,
        server_now_ms=5000,
    )
    assert replayed == later
    assert replay_result == first_result

    claimed, replay_request = claim_replay_authority(later, request)
    replayed_takeover, takeover_replay = apply_cas(
        replay_request,
        claimed,
        server_now_ms=5000,
        authenticated_instance="hold-reaper-02",
        authenticated_fencing_token=8,
    )
    assert replayed_takeover == claimed
    assert takeover_replay == first_result
    with pytest.raises(ArtifactHoldExpiryConflict, match="fence"):
        apply_cas(
            request,
            later,
            server_now_ms=5000,
            authenticated_fencing_token=6,
        )


def test_replay_cannot_be_applied_to_a_different_current_resource() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    other_state = ArtifactHoldState(
        artifact_id="artifact-02",
        artifact_version=5,
        artifact_status=ArtifactLifecycleStatus.AVAILABLE,
        hold_id="hold-02",
        hold_digest="bb" * 32,
        hold_status=ArtifactHoldStatus.EXPIRED,
        expires_ms=1000,
        active_hold_ids=frozenset(),
        active_ref_count=0,
        minimum_delete_at_ms=None,
        due_indexed=False,
        due_score_ms=None,
        candidate=None,
    )
    forged = replace(committed, hold_state=other_state)
    forged.validate()

    with pytest.raises(ArtifactHoldExpiryConflict, match="resource"):
        apply_cas(request, forged)


def test_replay_rejects_terminal_expiry_timestamp_drift() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))
    forged = replace(
        committed,
        hold_state=replace(committed.hold_state, expires_ms=1100),
    )

    with pytest.raises(ArtifactHoldExpiryConflict, match="expiry timestamp"):
        forged.validate()


def test_replay_requires_same_hold_incarnation_and_committed_version_floor() -> None:
    request = make_request()
    committed, result = apply_cas(request, make_cas_state(make_state()))

    with pytest.raises(ArtifactHoldExpiryConflict, match="incarnation"):
        apply_cas(
            request,
            replace(
                committed,
                hold_state=replace(committed.hold_state, hold_digest="bb" * 32),
            ),
        )
    with pytest.raises(ArtifactHoldExpiryConflict, match="committed expiry"):
        apply_cas(
            request,
            replace(
                committed,
                hold_state=replace(committed.hold_state, artifact_version=4),
            ),
        )

    later = replace(
        committed,
        hold_state=replace(
            committed.hold_state,
            artifact_version=committed.hold_state.artifact_version + 5,
        ),
    )
    _, replayed = apply_cas(request, later)
    assert replayed == result


def test_cross_resource_commit_cannot_replay_for_another_request() -> None:
    candidate_b = make_candidate(
        artifact_id="artifact-02",
        hold_id="hold-02",
        expected_hold_digest="bb" * 32,
    )
    request_b = make_request(candidate=candidate_b)
    state_b = make_state(
        artifact_id="artifact-02",
        hold_id="hold-02",
        hold_digest="bb" * 32,
        active_hold_ids=frozenset({"hold-02"}),
        candidate=candidate_b,
    )
    committed_b, _ = apply_cas(request_b, make_cas_state(state_b))

    with pytest.raises(ArtifactHoldExpiryConflict):
        apply_cas(make_request(), committed_b)


def test_commit_evidence_binds_request_candidate_authority() -> None:
    request = make_request()
    committed, result = apply_first_write(request, make_state())
    updated = committed.hold_state
    alternate_candidate = make_candidate(
        scan_operation_id="22" * 32,
        candidate_lease_id="C" * 42 + "A",
        candidate_token="D" * 42 + "A",
        candidate_fencing_token=8,
    )
    wrong_before = make_state(candidate=alternate_candidate)
    with pytest.raises(ArtifactHoldExpiryConflict, match="candidate"):
        ArtifactHoldExpiryCommit.create(
            request=request,
            result=result,
            before_state=wrong_before,
            updated_state=updated,
            committed_at_ms=1000,
        )


def test_commit_evidence_allows_only_the_expiry_delta() -> None:
    request = make_request()
    before = make_state()
    committed, result = apply_first_write(request, before)
    updated = committed.hold_state
    forged_updates = (
        replace(updated, artifact_status=ArtifactLifecycleStatus.QUARANTINED),
        replace(updated, hold_digest="bb" * 32),
        replace(updated, expires_ms=1001),
        replace(updated, active_ref_count=3),
        replace(updated, minimum_delete_at_ms=2000),
        replace(updated, active_hold_ids=frozenset()),
    )

    for forged in forged_updates:
        with pytest.raises(ArtifactHoldExpiryConflict, match="transition evidence"):
            ArtifactHoldExpiryCommit.create(
                request=request,
                result=result,
                before_state=before,
                updated_state=forged,
                committed_at_ms=1000,
            )


def test_result_schema_boolean_and_transition_evidence_are_strict() -> None:
    request = make_request()
    committed, result = apply_first_write(request, make_state())
    updated = committed.hold_state

    with pytest.raises(ArtifactHoldExpiryConflict, match="schema"):
        replace(result, schema_version="999").validate()
    with pytest.raises(ArtifactHoldExpiryConflict, match="boolean"):
        replace(result, retention_locked=1).validate()

    forged = ArtifactHoldExpiryResult.create(
        expire_operation_id=result.expire_operation_id,
        artifact_id=result.artifact_id,
        hold_id=result.hold_id,
        request_digest=result.request_digest,
        artifact_version=result.artifact_version,
        retention_locked=False,
    )
    with pytest.raises(ArtifactHoldExpiryConflict, match="retention"):
        ArtifactHoldExpiryCommit.create(
            request=request,
            result=forged,
            before_state=make_state(),
            updated_state=updated,
            committed_at_ms=1000,
        )


def test_candidate_tokens_are_canonical_32_byte_base64url() -> None:
    with pytest.raises(ArtifactHoldExpiryConflict, match="base64url"):
        make_candidate(candidate_token="_" * 43)


def test_artifact_hold_exact_wire_fixture_reproduces_every_byte() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    scan_request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id=SCAN_ID,
        max_candidates=10,
    )
    candidate = make_candidate()
    scan_result = ArtifactHoldExpiryScanResult.create(
        request=scan_request,
        candidates=(candidate,),
    )
    request = make_request(candidate=candidate)
    state = make_state(candidate=candidate)
    committed, result = apply_first_write(request, state)
    commit = require_commit(committed, request)
    claim_request = make_replay_claim_request(
        request,
        base_commit_digest=committed.commits[0].commit_digest,
    )
    _, claim_result = apply_artifact_hold_expiry_replay_claim(
        claim_request,
        committed,
        server_now_ms=5000,
        lease_until_ms=5200,
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id="hold-reaper-02",
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )
    preimage = artifact_hold_expiry_preimage(
        artifact_id="artifact-01",
        hold_id="hold-01",
        expected_hold_digest="aa" * 32,
        expected_artifact_version=4,
        observed_expires_ms=1000,
    )

    assert fixture["duePreimageHex"] == preimage.hex()
    assert fixture["scanRequest"] == scan_request.to_wire_dict()
    assert fixture["scanRequestCanonicalHex"] == scan_request.canonical_json().hex()
    assert fixture["candidate"] == candidate.to_wire_dict()
    assert fixture["candidateCanonicalHex"] == candidate.canonical_json().hex()
    assert fixture["scanResult"] == scan_result.to_wire_dict()
    assert fixture["scanResultCanonicalHex"] == scan_result.canonical_json().hex()
    assert fixture["expireRequest"] == request.to_wire_dict()
    assert fixture["expireRequestCanonicalHex"] == request.canonical_json().hex()
    assert fixture["expireResult"] == result.to_wire_dict()
    assert fixture["expireResultCanonicalHex"] == result.canonical_json().hex()
    assert fixture["commit"] == commit.to_wire_dict()
    assert fixture["commitCanonicalHex"] == commit.canonical_json().hex()
    assert fixture["replayClaimRequest"] == claim_request.to_wire_dict()
    assert (
        fixture["replayClaimRequestCanonicalHex"]
        == claim_request.canonical_json().hex()
    )
    assert fixture["replayClaimResult"] == claim_result.to_wire_dict()
    assert (
        fixture["replayClaimResultCanonicalHex"]
        == claim_result.canonical_json().hex()
    )

    parsed_scan_request = ArtifactHoldExpiryScanRequest.from_canonical_json(
        bytes.fromhex(fixture["scanRequestCanonicalHex"])
    )
    parsed_candidate = ArtifactHoldExpiryCandidate.from_canonical_json(
        bytes.fromhex(fixture["candidateCanonicalHex"])
    )
    parsed_scan_result = ArtifactHoldExpiryScanResult.from_canonical_json(
        bytes.fromhex(fixture["scanResultCanonicalHex"])
    )
    parsed_expire_request = ArtifactHoldExpiryRequest.from_canonical_json(
        bytes.fromhex(fixture["expireRequestCanonicalHex"])
    )
    parsed_expire_result = ArtifactHoldExpiryResult.from_canonical_json(
        bytes.fromhex(fixture["expireResultCanonicalHex"])
    )
    parsed_commit = ArtifactHoldExpiryCommit.from_canonical_json(
        bytes.fromhex(fixture["commitCanonicalHex"])
    )
    parsed_claim_request = ArtifactHoldExpiryReplayClaimRequest.from_canonical_json(
        bytes.fromhex(fixture["replayClaimRequestCanonicalHex"])
    )
    parsed_claim_result = ArtifactHoldExpiryReplayClaimResult.from_canonical_json(
        bytes.fromhex(fixture["replayClaimResultCanonicalHex"])
    )
    assert parsed_scan_request == scan_request
    assert parsed_candidate == candidate
    parsed_scan_result.validate_for(parsed_scan_request)
    assert parsed_scan_result == scan_result
    assert parsed_expire_request == request
    assert parsed_expire_result == result
    assert validate_commit(parsed_commit, parsed_expire_request) == result
    assert parsed_commit == commit
    assert parsed_claim_request == claim_request
    parsed_claim_result.validate_for(parsed_claim_request)
    assert parsed_claim_result == claim_result
    assert set(fixture["expireResult"]) == {
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


def test_nats_actor_subject_graph_is_closed() -> None:
    text = NATS_SPEC.read_text(encoding="utf-8")
    fenced = text.split("`STATE_REQUEST_SUBJECTS_V1` 是以下 literal 集", 1)[1]
    fenced = fenced.split("```text", 1)[1].split("```", 1)[0]
    subjects = re.findall(r"a2a\.v1\.state\.[A-Za-z0-9.-]+", fenced)
    assert len(subjects) == len(set(subjects)) == 89
    assert "a2a.v1.state.artifact.hold.expire" in subjects

    rows = {
        line.split("|", 2)[1].strip(): line
        for line in text.splitlines()
        if line.startswith("| `")
    }
    hold_reaper = rows["`artifact-hold-reaper:<instanceId>`"]
    adapter = rows["`artifact:<instanceId>`"]
    delete_worker = rows["`artifact-delete-worker:<instanceId>`"]
    hold_subject = "a2a.v1.state.artifact.hold.expire"
    delete_subject = "a2a.v1.state.artifact.delete"

    assert hold_subject in hold_reaper
    assert "SCAN\\|EXPIRE\\|REPLAY_CLAIM" in hold_reaper
    assert delete_subject not in hold_reaper
    assert hold_subject not in adapter
    assert delete_subject in adapter and "REQUEST" in adapter
    assert "COMPLETE" not in adapter
    assert delete_subject in delete_worker and "COMPLETE" in delete_worker
    assert hold_subject not in delete_worker
    assert "ACQUIRE|RENEW|SCAN|EXPIRE" not in text


def test_docs_name_separate_hold_expiry_and_physical_delete_actors() -> None:
    artifact = ARTIFACT_SPEC.read_text(encoding="utf-8")
    redis = REDIS_SPEC.read_text(encoding="utf-8")
    config = CONFIG_SPEC.read_text(encoding="utf-8")
    plan = IMPLEMENTATION_PLAN.read_text(encoding="utf-8")

    assert "artifact:holds:due" in artifact
    assert "ArtifactHoldExpiryScanRequestV1" in artifact
    assert "ArtifactHoldExpiryReplayClaimRequestV1" in artifact
    assert "ArtifactHoldExpiryReplayClaimResultV1" in artifact
    assert "baseCommitDigest" in artifact
    assert "authorizedCandidateDigest" in artifact
    assert "leaseUntilMs-issuedAtMs<=300000ms" in artifact
    assert "REPLAY_CLAIM" in artifact
    assert "ArtifactHoldExpiryRequestV1" in artifact
    assert "ArtifactHoldExpiryResultV1" in artifact
    assert "Artifact Hold Reaper" in artifact
    assert "Artifact Delete Worker" in artifact
    assert "Artifact Hold Reaper不得执行物理对象删除" in artifact
    assert "hold-expiry-replay-claim:<replayOperationId>" in redis
    assert "hold-expiry-replay-current:<expireOperationId>" in redis
    assert "baseCommitDigest" in redis
    assert "authorizedCandidateDigest" in redis
    assert "leaseUntilMs-issuedAtMs<=300000ms" in redis
    assert "`artifact-hold-reaper`稳定Principal" in config
    assert "`artifact-delete-worker`稳定Principal" in config
    assert "Artifact Hold Reaper独占artifact.hold.expire" in plan
    assert "candidate lease duration上限`300000ms`" in plan
    assert "真实Redis Function、持久化/重启exact replay仍待C2/C3实现与验收" in plan
    formal_replay_test = "- **TEST-ARTIFACT-HOLD-REPLAY-001**"
    assert formal_replay_test in artifact
    assert formal_replay_test in redis
    assert "TEST-ARTIFACT-HOLD-REPLAY-001" in plan
    assert "89/89 State subject" in plan
