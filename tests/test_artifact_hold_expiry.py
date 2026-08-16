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
    ArtifactHoldExpiryCandidate,
    ArtifactHoldExpiryCandidateLedgerEntry,
    ArtifactHoldExpiryCASState,
    ArtifactHoldExpiryCommit,
    ArtifactHoldExpiryConflict,
    ArtifactHoldExpiryEventRecord,
    ArtifactHoldExpiryEventSink,
    ArtifactHoldExpiryLedgerState,
    ArtifactHoldExpiryOperation,
    ArtifactHoldExpiryRequest,
    ArtifactHoldExpiryResult,
    ArtifactHoldExpiryScanRequest,
    ArtifactHoldExpiryScanResult,
    ArtifactHoldState,
    ArtifactHoldStatus,
    ArtifactLifecycleStatus,
    apply_artifact_hold_expiry,
    apply_artifact_hold_expiry_scan,
    artifact_hold_expiry_preimage,
)

ROOT = Path(__file__).parents[1]
ARTIFACT_SPEC = ROOT / "docs" / "specs" / "A2AMesh_Artifact与对象存储设计_V1.2.md"
NATS_SPEC = ROOT / "docs" / "specs" / "A2AMesh_A2A协议与NATS集成适配设计_V1.6.md"
CONFIG_SPEC = ROOT / "docs" / "specs" / "A2AMesh_受信配置与变更治理设计_V1.2.md"
IMPLEMENTATION_PLAN = ROOT / "docs" / "specs" / "A2AMesh_开发实施计划.md"
FIXTURE = Path(__file__).parent / "fixtures" / "state_contracts" / "artifact_hold_expiry_v1.json"

SCAN_ID = "11" * 32
TOKEN_A = "A" * 43
TOKEN_B = "B" * 42 + "A"
BASE64URL_C = "Q0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0M"
BASE64URL_D = "REREREREREREREREREREREREREREREREREREREREREQ"
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
        ledger = ledger.record_scan(scan_request, scan_result)
    return ArtifactHoldExpiryCASState.create(
        hold_state=state,
        candidate_ledger=ledger,
    )


def add_live_replay_authority(
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
        candidate_ledger=state.candidate_ledger.record_scan(
            scan_request,
            scan_result,
        ),
    )
    updated.validate()
    return updated, make_request(candidate=candidate)


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
    ledger = ArtifactHoldExpiryLedgerState.empty().record_scan(
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
        with pytest.raises(ArtifactHoldExpiryConflict, match="global candidate"):
            ledger.record_scan(second_request, second_result)


def test_scan_writer_binds_due_state_and_updates_current_projection() -> None:
    request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id="33" * 32,
        max_candidates=1,
    )
    candidate = make_candidate(scan_operation_id=request.scan_operation_id)
    initial = make_cas_state(make_state(candidate=None))

    updated, result = apply_artifact_hold_expiry_scan(
        request,
        initial,
        server_now_ms=1000,
        candidates=(candidate,),
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id=OWNER_INSTANCE,
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )

    assert result.candidates == (candidate,)
    assert updated.hold_state.candidate == candidate
    assert updated.candidate_ledger.candidate_entries[0].candidate == candidate
    replayed, replay_result = apply_artifact_hold_expiry_scan(
        request,
        updated,
        server_now_ms=1000,
        candidates=(candidate,),
        authenticated_reaper_principal_id=OWNER_PRINCIPAL,
        authenticated_reaper_instance_id=OWNER_INSTANCE,
        authenticated_component_type="artifact-hold-reaper",
        authenticated_subject="a2a.v1.state.artifact.hold.expire",
    )
    assert replayed == updated
    assert replay_result == result


@pytest.mark.parametrize(
    "case",
    ("before_due", "terminal_hold", "wrong_owner"),
)
def test_scan_writer_rejects_unauthorized_or_non_due_candidates(case: str) -> None:
    request = ArtifactHoldExpiryScanRequest.create(
        scan_operation_id="77" * 32,
        max_candidates=1,
    )
    candidate = make_candidate(scan_operation_id=request.scan_operation_id)
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
            candidates=(candidate,),
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
    ledger = ArtifactHoldExpiryLedgerState.empty().record_scan(
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

    updated = ledger.record_scan(second_request, second_result)

    assert updated.candidate_fence_high_water == 8
    assert len(updated.scan_results) == 2
    assert len(updated.candidate_entries) == 2
    assert all(
        isinstance(entry, ArtifactHoldExpiryCandidateLedgerEntry)
        for entry in updated.candidate_entries
    )
    assert updated.record_scan(second_request, second_result) == updated

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
    latest_ledger = snapshot.candidate_ledger.record_scan(
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
        committed.candidate_ledger.record_scan(next_request, next_result)


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


def test_replay_same_fence_requires_owner_but_higher_fence_can_take_over() -> None:
    request = make_request()
    committed, expected = apply_cas(request, make_cas_state(make_state()))

    with pytest.raises(ArtifactHoldExpiryConflict, match="instance"):
        apply_cas(
            request,
            committed,
            authenticated_instance="hold-reaper-02",
            authenticated_fencing_token=7,
        )

    replayed, result = apply_cas(
        request,
        committed,
        server_now_ms=5000,
        authenticated_instance="hold-reaper-02",
        authenticated_fencing_token=8,
    )
    assert replayed == committed
    assert result == expected

    with pytest.raises(ArtifactHoldExpiryConflict, match="fence"):
        apply_cas(
            request,
            committed,
            server_now_ms=5000,
            authenticated_fencing_token=6,
        )


def test_terminal_snapshot_rejects_newer_unconsumed_candidate_authority() -> None:
    request = make_request()
    committed, _ = apply_cas(request, make_cas_state(make_state()))

    with pytest.raises(ArtifactHoldExpiryConflict, match="terminal.*candidate"):
        add_live_replay_authority(committed)


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

    replayed_takeover, takeover_replay = apply_cas(
        request,
        later,
        server_now_ms=5000,
        authenticated_instance="hold-reaper-02",
        authenticated_fencing_token=8,
    )
    assert replayed_takeover == later
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
    assert parsed_scan_request == scan_request
    assert parsed_candidate == candidate
    parsed_scan_result.validate_for(parsed_scan_request)
    assert parsed_scan_result == scan_result
    assert parsed_expire_request == request
    assert parsed_expire_result == result
    assert validate_commit(parsed_commit, parsed_expire_request) == result
    assert parsed_commit == commit
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
    assert "SCAN|EXPIRE" in hold_reaper
    assert delete_subject not in hold_reaper
    assert hold_subject not in adapter
    assert delete_subject in adapter and "REQUEST" in adapter
    assert "COMPLETE" not in adapter
    assert delete_subject in delete_worker and "COMPLETE" in delete_worker
    assert hold_subject not in delete_worker
    assert "ACQUIRE|RENEW|SCAN|EXPIRE" not in text


def test_docs_name_separate_hold_expiry_and_physical_delete_actors() -> None:
    artifact = ARTIFACT_SPEC.read_text(encoding="utf-8")
    config = CONFIG_SPEC.read_text(encoding="utf-8")
    plan = IMPLEMENTATION_PLAN.read_text(encoding="utf-8")

    assert "artifact:holds:due" in artifact
    assert "ArtifactHoldExpiryScanRequestV1" in artifact
    assert "ArtifactHoldExpiryRequestV1" in artifact
    assert "ArtifactHoldExpiryResultV1" in artifact
    assert "Artifact Hold Reaper" in artifact
    assert "Artifact Delete Worker" in artifact
    assert "Artifact Hold Reaper不得执行物理对象删除" in artifact
    assert "`artifact-hold-reaper`稳定Principal" in config
    assert "`artifact-delete-worker`稳定Principal" in config
    assert "Artifact Hold Reaper独占artifact.hold.expire" in plan
    assert "89/89 State subject" in plan
