"""C0 conformance tests for active Markdown links and compatibility claims."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "specs",
]


def _load_checker() -> ModuleType:
    path = REPO_ROOT / "scripts" / "verify_docs_links.py"
    spec = importlib.util.spec_from_file_location("verify_docs_links", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_active_document_links_and_anchors_are_closed() -> None:
    checker = _load_checker()
    findings = checker.scan_paths(ACTIVE_DOCS)
    assert findings == []


def test_link_checker_reports_missing_file_and_anchor(tmp_path: Path) -> None:
    checker = _load_checker()
    target = tmp_path / "target.md"
    target.write_text("# Present Heading\n", encoding="utf-8")
    source = tmp_path / "source.md"
    source.write_text(
        "[missing](absent.md) [missing anchor](target.md#absent)\n",
        encoding="utf-8",
    )
    findings = checker.scan_paths([source])
    assert [(item.reason, item.target) for item in findings] == [
        ("missing-file", "absent.md"),
        ("missing-anchor", "target.md#absent"),
    ]


def test_link_checker_ignores_code_and_checks_image_targets(tmp_path: Path) -> None:
    checker = _load_checker()
    source = tmp_path / "source.md"
    source.write_text(
        "```markdown\n[ignored](missing.md)\n```\n![missing](missing.png)\n",
        encoding="utf-8",
    )
    findings = checker.scan_paths([source])
    assert [(item.reason, item.target) for item in findings] == [
        ("missing-file", "missing.png"),
    ]


def test_current_document_claims_explicitly_remain_prototype() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    specs = (REPO_ROOT / "docs" / "specs" / "README.md").read_text(encoding="utf-8")
    assert "private A2A-inspired NATS RPC prototype" in readme
    assert "不得据此声明已兼容 A2A v1 或已生产可用" in readme
    assert "仍是 private A2A-inspired NATS prototype" in specs
    assert "G0 正式批准待关闭" in specs


@pytest.mark.parametrize(
    "target", ["http://example.test", "https://example.test", "mailto:a@example.test"]
)
def test_external_links_are_not_local_file_requirements(target: str, tmp_path: Path) -> None:
    checker = _load_checker()
    source = tmp_path / "source.md"
    source.write_text(f"[external]({target})\n", encoding="utf-8")
    assert checker.scan_paths([source]) == []
