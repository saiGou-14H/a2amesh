#!/usr/bin/env python3
"""Build and verify the source-distribution inventory and extracted contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import textwrap
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

_REQUIRED_PATHS = (
    ".github/workflows/ci.yml",
    ".gitignore",
    "MANIFEST.in",
    "README.md",
    "docs/archive/README.md",
    "docs/archive/v1.5/README.md",
    "docs/specs/README.md",
    "scripts/run_ci.py",
    "scripts/verify_a2a_fixtures.py",
    "scripts/verify_docs_links.py",
    "scripts/verify_sdist_closure.py",
    "scripts/verify_wheel_resources.py",
    "src/a2amesh/conformance/official_fixtures.py",
    "src/a2amesh/conformance/fixtures/a2a_v1/official_fixture_manifest.json",
    "tests/conformance/test_ci_contract.py",
    "tests/conformance/test_wheel_resources.py",
    "tests/test_architecture_browser_smoke.py",
)
_FORBIDDEN_DUPLICATES = (
    "tests/fixtures/a2a_v1/message_user_text.json",
    "tests/fixtures/a2a_v1/official_agent_card.json",
    "tests/fixtures/a2a_v1/official_artifact_text.json",
    "tests/fixtures/a2a_v1/official_fixture_manifest.json",
    "tests/fixtures/a2a_v1/official_task_artifact_update.json",
    "tests/fixtures/a2a_v1/official_task_completed.json",
    "tests/fixtures/a2a_v1/official_task_not_found_error.json",
    "tests/fixtures/a2a_v1/official_task_status_update.json",
)


class SdistClosureError(RuntimeError):
    """Raised when a built source distribution is not self-consistent."""


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("UV_PROJECT_ENVIRONMENT", None)
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["PIP_NO_INDEX"] = "1"
    return environment


def _run(arguments: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - repository-owned argv, shell is disabled
        tuple(arguments),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_clean_environment(),
    )


def _archive_inventory(archive: Path) -> tuple[str, set[str]]:
    with tarfile.open(archive, mode="r:gz") as bundle:
        members = bundle.getmembers()
    roots: set[str] = set()
    files: set[str] = set()
    for member in members:
        parts = PurePosixPath(member.name).parts
        if not parts:
            continue
        roots.add(parts[0])
        if member.isfile() and len(parts) > 1:
            files.add(PurePosixPath(*parts[1:]).as_posix())
        if member.issym() or member.islnk():
            raise SdistClosureError(f"sdist must not contain links: {member.name}")
    if len(roots) != 1:
        raise SdistClosureError(f"sdist must contain one root directory, found: {sorted(roots)}")
    missing = sorted(set(_REQUIRED_PATHS) - files)
    if missing:
        raise SdistClosureError(f"sdist inventory is missing required files: {missing}")
    duplicates = sorted(set(_FORBIDDEN_DUPLICATES) & files)
    if duplicates:
        raise SdistClosureError(f"sdist contains duplicate official fixtures: {duplicates}")
    return roots.pop(), files


def verify_sdist_closure(repository_root: Path) -> str:
    """Build the current sdist and exercise its extracted conformance closure."""
    repository_root = repository_root.resolve()
    with tempfile.TemporaryDirectory(prefix="a2amesh-sdist-closure-") as temporary:
        root = Path(temporary)
        distribution = root / "dist"
        _run(
            (
                sys.executable,
                "-m",
                "build",
                "--sdist",
                "--no-isolation",
                "--outdir",
                str(distribution),
            ),
            repository_root,
        )
        archives = tuple(distribution.glob("a2amesh-*.tar.gz"))
        if len(archives) != 1:
            raise SdistClosureError(f"expected one sdist archive, found {len(archives)}")
        archive = archives[0]
        archive_root, files = _archive_inventory(archive)

        extracted_parent = root / "extracted"
        extracted_parent.mkdir()
        with tarfile.open(archive, mode="r:gz") as bundle:
            bundle.extractall(extracted_parent, filter="data")
        extracted = extracted_parent / archive_root
        if not extracted.is_dir():
            raise SdistClosureError("sdist root directory was not extracted")

        docs = _run(
            (
                sys.executable,
                "scripts/verify_docs_links.py",
                "--json",
                "README.md",
                "docs/specs",
            ),
            extracted,
        )
        docs_report = json.loads(docs.stdout)
        if docs_report.get("findings") != []:
            raise SdistClosureError(f"sdist documentation links failed: {docs_report}")

        probe = textwrap.dedent(
            """
            import sys
            from pathlib import Path

            root = Path.cwd().resolve()
            sys.path.insert(0, str(root / "src"))
            import a2amesh
            import pytest

            package = Path(a2amesh.__file__).resolve()
            assert package.is_relative_to(root / "src"), package
            raise SystemExit(
                pytest.main(["-q", "-p", "no:cacheprovider", "tests/conformance"])
            )
            """
        )
        conformance = _run((sys.executable, "-I", "-c", probe), extracted)
        last_line = next(
            (line for line in reversed(conformance.stdout.splitlines()) if line.strip()),
            "conformance output missing",
        )
        return f"verified sdist closure: {len(files)} files; {last_line}"


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    try:
        result = verify_sdist_closure(repository_root)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"sdist closure verification failed: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            if exc.stdout:
                print(exc.stdout.rstrip(), file=sys.stderr)
            if exc.stderr:
                print(exc.stderr.rstrip(), file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
