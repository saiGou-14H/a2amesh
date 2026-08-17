"""Machine-checkable trust-boundary contract for the V1.6 architecture assets."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from a2amesh.config_slots import required_slot_set
from a2amesh.state_contracts import ArtifactHoldExpiryCASState

ASSET_DIR = Path(__file__).parents[1] / "docs" / "assets"
ANALYSIS_PATH = (
    Path(__file__).parents[1]
    / "docs"
    / "specs"
    / "A2AMesh_最新架构全量分析_V1.6.md"
)
SVG_PATH = ASSET_DIR / "A2AMesh_V1.6_Architecture.svg"
HTML_PATH = ASSET_DIR / "A2AMesh_V1.6_Architecture.html"
EXPECTED_MACHINE_EDGE_IDS = {
    "config-controller-to-state",
    "reconciliation-to-state",
    "artifact-reaper-to-state",
    "artifact-adapter-to-object-store",
    "artifact-delete-worker-to-object-store",
    "state-redis-authority",
}
EXPECTED_PATH_COUNT = 53
EXPECTED_PATH_SIGNATURE_SHA256 = (
    "7678eb87225928330523c8adfbef7de74d8c4986b1868f5d00f59f42f626db58"
)
SVG_START = '<svg xmlns="http://www.w3.org/2000/svg"'


def asset_text() -> tuple[str, str, str]:
    svg = SVG_PATH.read_text(encoding="utf-8")
    html = HTML_PATH.read_text(encoding="utf-8")
    start = html.index(SVG_START)
    end = html.index("</svg>", start) + len("</svg>")
    return svg, html, html[start:end]


def normalized_text(element: ET.Element) -> str:
    return " ".join("".join(element.itertext()).split())


def path_signature_digest(root: ET.Element) -> tuple[int, str]:
    signature_keys = (
        "id",
        "class",
        "data-source",
        "data-target",
        "data-permission",
        "d",
    )
    signatures = [
        {key: element.get(key) for key in signature_keys}
        for element in root.iter()
        if element.tag.endswith("path")
    ]
    canonical = json.dumps(
        signatures,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(signatures), hashlib.sha256(canonical).hexdigest()


def test_standalone_and_embedded_svg_are_parseable_and_byte_identical() -> None:
    svg, _, embedded = asset_text()
    standalone_root = ET.fromstring(svg)  # noqa: S314 - repository-owned fixture
    embedded_root = ET.fromstring(embedded)  # noqa: S314 - repository-owned fixture

    assert standalone_root.tag == "{http://www.w3.org/2000/svg}svg"
    assert embedded_root.tag == standalone_root.tag
    assert embedded == svg.rstrip("\n")
    assert standalone_root.attrib["viewBox"] == "0 0 1800 1120"


def test_html_wrapper_is_a_real_standards_document_with_live_controls() -> None:
    _, html, embedded = asset_text()
    lines = html.splitlines()
    assert html.startswith("<!doctype html>\n<html lang=\"zh-CN\">")
    assert not any(re.match(r"^\d+\|", line) for line in lines)
    assert "<head>" in html and "</head>" in html
    assert "<body>" in html and "</body>" in html
    assert html.count("<svg ") == 1
    assert embedded == html[html.index(SVG_START) : html.index("</svg>") + len("</svg>")]
    assert "document.getElementById('minus').onclick" in html
    assert "document.getElementById('plus').onclick" in html
    assert "r.onclick=()=>" in html


def test_every_peer_shows_protected_ipc_and_single_writer_workspace_boundary() -> None:
    svg, _, _ = asset_text()
    root = ET.fromstring(svg)  # noqa: S314 - repository-owned fixture
    by_id = {element.get("id"): element for element in root.iter() if element.get("id")}

    for peer_id in ("peer-windows-a", "peer-windows-b", "peer-linux"):
        peer_text = normalized_text(by_id[peer_id])
        assert "Peer Binding / Supervisor → App Core: PROTECTED LOCAL IPC" in peer_text
        assert "Core NKey" in peer_text
        assert "RuntimeUNTRUSTED" in peer_text
        assert "private worktree" in peer_text
        assert "Merge Broker" in peer_text
        assert "shared root" in peer_text
        assert "Runtime has no root ACL" in peer_text

    assert svg.count("PROTECTED LOCAL IPC") == 3
    assert svg.count("Merge Broker") == 4  # description plus one per peer
    assert svg.count("shared root") == 4  # description plus one per peer


def test_recovery_and_worm_roles_are_not_collapsed_or_bypassed() -> None:
    svg, _, _ = asset_text()
    root = ET.fromstring(svg)  # noqa: S314 - repository-owned fixture
    text = normalized_text(root)

    assert "Recovery Roles + Manifest" in text
    assert "Orchestrator · Verifier · Compactor" in text
    assert "separate Principal / NKey · fenced writers" in text
    assert "signed WORM manifest · dual release" in text
    assert "AUDIT RELAY (WORKERS ONLY) → WORM" in text
    assert "NO WORM PUBLISH" in text
    assert "AUDIT ONLY · relay receipt required" in text

    forbidden = (
        "State Service → WORM",
        "Private Object Store → WORM",
        "Object Store → WORM",
        "Config Controller → WORM",
    )
    assert all(value not in text for value in forbidden)


def test_artifact_hold_reaper_and_delete_worker_are_distinct_components() -> None:
    svg, _, _ = asset_text()
    root = ET.fromstring(svg)  # noqa: S314 - repository-owned fixture
    by_id = {element.get("id"): element for element in root.iter() if element.get("id")}

    adapter = normalized_text(by_id["artifact-adapter"])
    hold_reaper = normalized_text(by_id["artifact-hold-reaper"])
    delete_worker = normalized_text(by_id["artifact-delete-worker"])
    artifact_roles = [
        by_id[role]
        for role in ("artifact-adapter", "artifact-hold-reaper", "artifact-delete-worker")
    ]
    principals = {role.get("data-principal") for role in artifact_roles}
    nkeys = {role.get("data-nkey") for role in artifact_roles}
    assert len(principals) == 3
    assert len(nkeys) == 3
    assert all(principals)
    assert all(nkeys)

    assert by_id["artifact-adapter"].get("data-role") == "artifact-adapter"
    assert by_id["artifact-adapter"].get("data-operations") == "REQUEST"
    assert by_id["artifact-adapter"].get("data-provider-delete-credential") == "false"
    assert "Artifact Adapter" in adapter
    assert "REQUEST only" in adapter
    assert "no delete credential" in adapter

    assert by_id["artifact-hold-reaper"].get("data-role") == "artifact-hold-reaper"
    assert (
        by_id["artifact-hold-reaper"].get("data-operations")
        == "SCAN|EXPIRE|REPLAY_CLAIM"
    )
    assert (
        by_id["artifact-hold-reaper"].get("data-provider-delete-credential")
        == "false"
    )
    assert "Artifact Hold Reaper" in hold_reaper
    assert "SCAN · EXPIRE" in hold_reaper
    assert "REPLAY_CLAIM" in hold_reaper
    assert "no Object Store cred" in hold_reaper

    assert by_id["artifact-delete-worker"].get("data-role") == "artifact-delete-worker"
    assert by_id["artifact-delete-worker"].get("data-operations") == "COMPLETE"
    assert (
        by_id["artifact-delete-worker"].get("data-provider-delete-credential")
        == "true"
    )
    assert "Delete Worker" in delete_worker
    assert "COMPLETE only" in delete_worker
    assert "provider delete cred" in delete_worker

    expected_edges = {
        "artifact-adapter-to-object-store": (
            "artifact-adapter",
            "object-store",
            "REQUEST · upload/finalize",
        ),
        "artifact-delete-worker-to-object-store": (
            "artifact-delete-worker",
            "object-store",
            "COMPLETE · provider physical delete",
        ),
        "artifact-reaper-to-state": (
            "artifact-hold-reaper",
            "state-service",
            "SCAN|EXPIRE|REPLAY_CLAIM · no provider credential",
        ),
    }
    for edge_id, (source, target, permission) in expected_edges.items():
        edge = by_id[edge_id]
        assert edge.get("data-source") == source
        assert edge.get("data-target") == target
        assert edge.get("data-permission") == permission
        assert source in by_id
        assert target in by_id

    assert (
        by_id["artifact-delete-worker-to-object-store"].get("class")
        == "flowDelete"
    )
    assert (
        by_id["artifact-delete-edge-label"].get("data-edge-for")
        == "artifact-delete-worker-to-object-store"
    )
    assert "DELETE COMPLETE → BLOB" in normalized_text(
        by_id["artifact-delete-edge-label"]
    )
    assert (
        by_id["artifact-hold-edge-label"].get("data-edge-for")
        == "artifact-reaper-to-state"
    )
    assert "HOLD CAS ONLY → STATE" in normalized_text(
        by_id["artifact-hold-edge-label"]
    )

    machine_flows = [
        element
        for element in root.iter()
        if element.get("data-source") is not None
        or element.get("data-target") is not None
    ]
    assert machine_flows
    for flow in machine_flows:
        assert flow.get("data-source") in by_id
        assert flow.get("data-target") in by_id

    hold_reaper_targets = {
        element.get("data-target")
        for element in root.iter()
        if element.get("data-source") == "artifact-hold-reaper"
    }
    assert "object-store" not in hold_reaper_targets
    assert "artifact-hold-state-call" not in by_id
    assert "Artifact Reaper · independent NKey" not in svg
    assert "ordered outbox · PubAck · sweepers" not in svg


def test_machine_edges_are_exactly_allowlisted_and_deny_extra_artifact_routes() -> None:
    svg, _, _ = asset_text()
    root = ET.fromstring(svg)  # noqa: S314 - repository-owned fixture
    path_count, path_digest = path_signature_digest(root)
    assert path_count == EXPECTED_PATH_COUNT
    assert path_digest == EXPECTED_PATH_SIGNATURE_SHA256

    machine_flows = {
        element.get("id"): element
        for element in root.iter()
        if element.tag.endswith("path")
        and (
            element.get("data-source") is not None
            or element.get("data-target") is not None
        )
    }

    assert set(machine_flows) == EXPECTED_MACHINE_EDGE_IDS
    assert all(
        element.get("data-source")
        and element.get("data-target")
        and element.get("data-source") != element.get("data-target")
        for element in machine_flows.values()
    )
    assert {
        element.get("data-target")
        for element in machine_flows.values()
        if element.get("data-source") == "artifact-adapter"
    } == {"object-store"}
    assert "artifact-adapter-to-recovery" not in machine_flows


def test_latest_config_and_artifact_contracts_are_visible_without_overclaim() -> None:
    svg, html, _ = asset_text()
    root = ET.fromstring(svg)  # noqa: S314 - repository-owned fixture
    by_id = {element.get("id"): element for element in root.iter() if element.get("id")}

    config = normalized_text(by_id["config-controller"])
    artifact_contract = normalized_text(by_id["artifact-hold-cas-contract"])
    capability = normalized_text(by_id["capability-boundary"])
    state = normalized_text(by_id["state-service"])
    redis = normalized_text(by_id["redis-state-plane"])

    assert "RequiredSlotSetV1 · READY/NACK" in config
    assert "JWS · GateEvidence · active CAS" in config
    assert "ARTIFACT HOLD · SINGLE-CAS SNAPSHOT" in artifact_contract
    assert "ACTIVE → EXPIRED" in artifact_contract
    assert "current authority" in artifact_contract
    assert "immutable commit · audit · outbox" in artifact_contract
    assert "Artifact CAS" in state
    assert "candidate ledger · commit · tombstone" in redis
    assert "EXECUTABLE PURE CONTRACT ≠ REDIS / NATS INTEGRATION" in capability
    assert "ArtifactHoldExpiryCASState" in html
    assert "RequiredSlotSetV1" in html
    assert "Redis Function/Lua 尚未实现" in html
    assert "PRODUCTION READY" not in svg
    assert "REDIS IMPLEMENTED" not in svg


def test_authority_and_audit_flows_preserve_component_ownership() -> None:
    svg, _, _ = asset_text()
    root = ET.fromstring(svg)  # noqa: S314 - repository-owned fixture
    by_id = {element.get("id"): element for element in root.iter() if element.get("id")}

    audit_flow = by_id["audit-relay-to-worm"]
    state_flow = by_id["state-redis-authority"]
    assert audit_flow.get("data-source") == "workers"
    assert audit_flow.get("data-target") == "worm-audit-sink"
    assert "AUDIT RELAY (WORKERS ONLY) → WORM" in normalized_text(audit_flow)
    assert state_flow.get("data-source") == "state-service"
    assert state_flow.get("data-target") == "redis-state-plane"
    assert "NO WORM PUBLISH" in normalized_text(by_id["state-service"])
    assert "WORM" not in normalized_text(by_id["artifact-hold-reaper"])


def test_architecture_evidence_maps_to_importable_code_and_analysis_boundaries() -> None:
    analysis = ANALYSIS_PATH.read_text(encoding="utf-8")

    assert callable(required_slot_set)
    assert hasattr(ArtifactHoldExpiryCASState, "__dataclass_fields__")
    assert "`RequiredSlotSetV1`和`ArtifactHoldExpiryCASState`已形成确定性纯状态合同" in analysis
    assert "它仍不是Redis Function、真实NATS ingress或Object Store删除集成" in analysis
    assert "它不等于Config Controller、signed READY/NACK ingress" in analysis
    assert "不能作为Redis/NATS部署、持久化或生产就绪证据" in analysis
