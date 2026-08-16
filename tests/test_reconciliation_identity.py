"""Executable fixtures for stable Reconciliation due and claim identities."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from a2amesh.state_contracts import (
    ReconciliationClaimOperation,
    ReconciliationDueKind,
    reconciliation_claim_operation_id,
    reconciliation_claim_scope_bytes,
    reconciliation_due_operation_id,
    reconciliation_due_operation_preimage,
    system_claim_identity,
)

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "state_contracts"
    / "reconciliation_operation_identity_v1.json"
)
RECON_SPEC = (
    Path(__file__).parents[1]
    / "docs"
    / "specs"
    / "A2AMesh_人工对账与运维操作设计_V1.2.md"
)


def fixtures() -> list[dict]:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert document["schemaVersion"] == "1"
    return document["fixtures"]


@pytest.mark.parametrize("fixture", fixtures(), ids=lambda item: item["claimOperation"])
def test_reconciliation_system_operation_exact_byte_fixture(fixture: dict) -> None:
    kwargs = {
        "due_kind": fixture["dueKind"],
        "case_id": fixture["caseId"],
        "observed_revision": fixture["observedRevision"],
        "observed_due_ms": fixture["observedDueMs"],
        "observed_claim_fencing_token": fixture["observedClaimFencingToken"],
    }
    preimage = reconciliation_due_operation_preimage(**kwargs)
    due_id = reconciliation_due_operation_id(**kwargs)
    identity = system_claim_identity(
        **kwargs,
        operator_principal_hash=fixture["operatorPrincipalHash"],
    )
    scope = reconciliation_claim_scope_bytes(
        case_id=fixture["caseId"],
        operation=fixture["claimOperation"],
        operator_principal_hash=fixture["operatorPrincipalHash"],
        idempotency_key=due_id,
    )

    assert preimage.hex() == fixture["duePreimageHex"]
    assert due_id == fixture["dueOperationId"]
    assert scope.hex() == fixture["claimScopeHex"]
    assert identity.due_operation_id == fixture["dueOperationId"]
    assert identity.idempotency_key == fixture["idempotencyKey"] == due_id
    assert identity.claim_operation_id == fixture["claimOperationId"]
    assert identity.claim_operation_id != identity.due_operation_id


def test_same_due_candidate_maps_to_same_final_operation_after_scanner_takeover() -> None:
    kwargs = {
        "due_kind": ReconciliationDueKind.CLAIM_EXPIRE,
        "case_id": "case-stable",
        "observed_revision": 11,
        "observed_due_ms": 1_786_842_000_123,
        "observed_claim_fencing_token": 9,
        "operator_principal_hash": "22" * 32,
    }
    first_scanner = system_claim_identity(**kwargs)
    takeover_scanner = system_claim_identity(**kwargs)
    assert takeover_scanner == first_scanner


def test_each_stable_due_tuple_field_changes_operation_identity() -> None:
    baseline = {
        "due_kind": ReconciliationDueKind.CLAIM_EXPIRE,
        "case_id": "case-a",
        "observed_revision": 1,
        "observed_due_ms": 1000,
        "observed_claim_fencing_token": 1,
    }
    expected = reconciliation_due_operation_id(**baseline)
    variants = (
        {**baseline, "case_id": "case-b"},
        {**baseline, "observed_revision": 2},
        {**baseline, "observed_due_ms": 1001},
        {**baseline, "observed_claim_fencing_token": 2},
    )
    assert all(reconciliation_due_operation_id(**item) != expected for item in variants)


def test_generic_claim_formula_uses_domain_separated_scope() -> None:
    kwargs = {
        "case_id": "case-operator",
        "operation": ReconciliationClaimOperation.ACQUIRE,
        "operator_principal_hash": "33" * 32,
        "idempotency_key": "operator-request-01",
    }
    operation_id = reconciliation_claim_operation_id(**kwargs)
    assert len(operation_id) == 64
    assert operation_id != reconciliation_due_operation_id(
        due_kind=ReconciliationDueKind.ESCALATE,
        case_id="case-operator",
        observed_revision=1,
        observed_due_ms=1000,
        observed_claim_fencing_token=None,
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "due_kind": ReconciliationDueKind.ESCALATE,
                "case_id": "case-a",
                "observed_revision": 1,
                "observed_due_ms": 1000,
                "observed_claim_fencing_token": 1,
            },
            "null",
        ),
        (
            {
                "due_kind": ReconciliationDueKind.CLAIM_EXPIRE,
                "case_id": "case-a",
                "observed_revision": True,
                "observed_due_ms": 1000,
                "observed_claim_fencing_token": 1,
            },
            "integer",
        ),
    ],
)
def test_invalid_due_identity_inputs_fail_closed(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        reconciliation_due_operation_id(**kwargs)


def test_design_contract_has_one_noncontradictory_operation_identity_rule() -> None:
    text = RECON_SPEC.read_text(encoding="utf-8")
    assert "claimOperationId不得等于dueOperationId" in text
    assert "claimOperationId和Idempotency-Key都必须逐字节等于" not in text
