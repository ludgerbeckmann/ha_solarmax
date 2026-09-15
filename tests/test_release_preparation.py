"""Release-preparation behavior."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PREPARER = _ROOT / "script" / "prepare-release"
_CHANGELOG = """\
# Changelog

## [Unreleased]

### Added

- Prepared release note.

## [1.3.3] - 2026-08-11

- Older note.

[Unreleased]: https://github.com/oschick/solarmax-ha-integration/compare/v1.3.3...HEAD
[1.3.3]: https://github.com/oschick/solarmax-ha-integration/compare/v1.3.2...v1.3.3
"""


def _release_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    integration = root / "custom_components" / "solarmax"
    integration.mkdir(parents=True)
    shutil.copy2(_ROOT / "pyproject.toml", root / "pyproject.toml")
    shutil.copy2(
        _ROOT / "custom_components" / "solarmax" / "manifest.json",
        integration / "manifest.json",
    )
    (root / "CHANGELOG.md").write_text(_CHANGELOG, encoding="utf-8")
    return root


def _run_preparer(root: Path, tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            _PREPARER,
            tag,
            "--date",
            "2026-09-04",
            "--root",
            root,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _versions(root: Path) -> tuple[str, str]:
    manifest = json.loads(
        (root / "custom_components" / "solarmax" / "manifest.json").read_text()
    )
    with (root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    return manifest["version"], project["project"]["version"]


def test_prepare_stable_release_consumes_unreleased_notes(tmp_path: Path) -> None:
    """A stable release creates a dated section and a fresh Unreleased section."""
    root = _release_fixture(tmp_path)

    result = _run_preparer(root, "v1.4.0")

    assert result.returncode == 0, result.stderr
    assert _versions(root) == ("1.4.0", "1.4.0")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]\n\n## [1.4.0] - 2026-09-04" in changelog
    assert changelog.count("Prepared release note") == 1
    assert (
        "[Unreleased]: "
        "https://github.com/oschick/solarmax-ha-integration/compare/v1.4.0...HEAD"
    ) in changelog
    assert (
        "[1.4.0]: "
        "https://github.com/oschick/solarmax-ha-integration/compare/v1.3.3...v1.4.0"
    ) in changelog


@pytest.mark.parametrize(
    ("suffix", "project_version"),
    [
        ("alpha.1", "1.4.0.dev0+alpha.1"),
        ("beta.2", "1.4.0.dev0+beta.2"),
        ("rc.1", "1.4.0.dev0+rc.1"),
        ("dev.3", "1.4.0.dev0+dev.3"),
        ("test", "1.4.0.dev0+test"),
        ("preview-build.4", "1.4.0.dev0+preview.build.4"),
    ],
)
def test_prepare_prerelease_preserves_unreleased_notes(
    tmp_path: Path, suffix: str, project_version: str
) -> None:
    """SemVer prereleases keep cumulative notes available for the final release."""
    root = _release_fixture(tmp_path)

    result = _run_preparer(root, f"v1.4.0-{suffix}")

    assert result.returncode == 0, result.stderr
    assert _versions(root) == (f"1.4.0-{suffix}", project_version)
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == _CHANGELOG


def test_prepare_release_rejects_invalid_tag_without_writes(tmp_path: Path) -> None:
    """Malformed SemVer must not partially update release metadata."""
    root = _release_fixture(tmp_path)
    before = {
        path: path.read_bytes()
        for path in (
            root / "pyproject.toml",
            root / "custom_components" / "solarmax" / "manifest.json",
            root / "CHANGELOG.md",
        )
    }

    result = _run_preparer(root, "v1.4.0-01")

    assert result.returncode != 0
    assert "invalid release tag" in result.stderr
    assert {path: path.read_bytes() for path in before} == before
