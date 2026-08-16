"""Machine-checkable trust-boundary contract for the V1.6 architecture assets."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

ASSET_DIR = Path(__file__).parents[1] / "docs" / "assets"
SVG_PATH = ASSET_DIR / "A2AMesh_V1.6_Architecture.svg"
HTML_PATH = ASSET_DIR / "A2AMesh_V1.6_Architecture.html"
SVG_START = '<svg xmlns="http://www.w3.org/2000/svg"'


def asset_text() -> tuple[str, str, str]:
    svg = SVG_PATH.read_text(encoding="utf-8")
    html = HTML_PATH.read_text(encoding="utf-8")
    start = html.index(SVG_START)
    end = html.index("</svg>", start) + len("</svg>")
    return svg, html, html[start:end]


def normalized_text(element: ET.Element) -> str:
    return " ".join("".join(element.itertext()).split())


def test_standalone_and_embedded_svg_are_parseable_and_byte_identical() -> None:
    svg, _, embedded = asset_text()
    standalone_root = ET.fromstring(svg)  # noqa: S314 - repository-owned fixture
    embedded_root = ET.fromstring(embedded)  # noqa: S314 - repository-owned fixture

    assert standalone_root.tag == "{http://www.w3.org/2000/svg}svg"
    assert embedded_root.tag == standalone_root.tag
    assert embedded == svg.rstrip("\n")
    assert standalone_root.attrib["viewBox"] == "0 0 1800 1120"


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
