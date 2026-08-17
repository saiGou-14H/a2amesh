#!/usr/bin/env python3
"""Build an isolated wheel and verify its official fixture package resources."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import venv
from collections.abc import Sequence
from pathlib import Path


class WheelResourceVerificationError(RuntimeError):
    """Raised when the built wheel cannot satisfy the resource contract."""


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
        env=_clean_environment(),
    )


def _venv_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def verify_wheel_resources(repository_root: Path) -> str:
    """Build from current files, install without dependencies, and read every resource."""
    repository_root = repository_root.resolve()
    with tempfile.TemporaryDirectory(prefix="a2amesh-wheel-resource-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        shutil.copy2(repository_root / "pyproject.toml", source / "pyproject.toml")
        shutil.copy2(repository_root / "README.md", source / "README.md")
        shutil.copytree(
            repository_root / "src",
            source / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
        )

        distribution = root / "dist"
        _run(
            (
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(distribution),
            ),
            source,
        )
        wheels = tuple(distribution.glob("a2amesh-*.whl"))
        if len(wheels) != 1:
            raise WheelResourceVerificationError(
                f"expected one a2amesh wheel, found {len(wheels)}"
            )

        environment = root / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = _venv_python(environment)
        _run(
            (
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(wheels[0]),
            ),
            root,
        )
        probe = textwrap.dedent(
            """
            from importlib.resources import files

            from a2amesh.conformance import OFFICIAL_FIXTURE_FILES, read_official_fixture

            root = files("a2amesh.conformance").joinpath("fixtures").joinpath("a2a_v1")
            assert len(OFFICIAL_FIXTURE_FILES) == 8
            assert all(root.joinpath(name).is_file() for name in OFFICIAL_FIXTURE_FILES)
            manifest = read_official_fixture("official_fixture_manifest.json")
            assert manifest["specVersion"] == "1.0.1"
            assert manifest["sdkVersion"] == "1.1.2"
            assert len(manifest["fixtures"]) == 7
            for name in OFFICIAL_FIXTURE_FILES:
                assert isinstance(read_official_fixture(name), dict)
            print("verified 8 wheel resources for 7 official A2A fixtures")
            """
        )
        result = _run((str(python), "-I", "-c", probe), root)
        return result.stdout.strip()


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    try:
        result = verify_wheel_resources(repository_root)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"wheel resource verification failed: {exc}", file=sys.stderr)
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
