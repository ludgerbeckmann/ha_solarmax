"""Repository metadata and localization consistency checks."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from packaging.requirements import Requirement
from packaging.version import Version

from script.release_common import parse_release_tag

_ROOT = Path(__file__).resolve().parent.parent
_INTEGRATION = _ROOT / "custom_components" / "solarmax"
_MARKDOWN_LINK = re.compile(r"!?\[[^]]*\]\(([^)]+)\)")


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _leaf_paths(value: object, prefix: str = "") -> set[str]:
    """Return dotted paths for every leaf in a nested mapping."""
    if not isinstance(value, dict):
        return {prefix}

    paths: set[str] = set()
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.update(_leaf_paths(child, path))
    return paths


def _project_version() -> str:
    with (_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def _manifest_version() -> str:
    return _load_json(_INTEGRATION / "manifest.json")["version"]


def _active_requirement(path: str, name: str, python_version: str) -> Requirement:
    matches = []
    for line in (_ROOT / path).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith(("#", "-r ")):
            continue
        requirement = Requirement(line)
        if requirement.name == name and (
            requirement.marker is None
            or requirement.marker.evaluate({"python_version": python_version})
        ):
            matches.append(requirement)
    assert len(matches) == 1
    return matches[0]


def _exact_version(requirement: Requirement) -> Version:
    pins = [item.version for item in requirement.specifier if item.operator == "=="]
    assert len(pins) == 1
    return Version(pins[0])


def _copy_release_fixture(tmp_path: Path) -> Path:
    """Copy the files consumed by the release checker."""
    root = tmp_path / "repository"
    integration = root / "custom_components" / "solarmax"
    scripts = root / "script"
    integration.mkdir(parents=True)
    scripts.mkdir()

    shutil.copy2(_ROOT / "pyproject.toml", root / "pyproject.toml")
    shutil.copy2(_ROOT / "CHANGELOG.md", root / "CHANGELOG.md")
    shutil.copy2(_INTEGRATION / "manifest.json", integration / "manifest.json")
    shutil.copy2(_ROOT / "script" / "check-release", scripts / "check-release")
    shutil.copy2(_ROOT / "script" / "release_common.py", scripts / "release_common.py")
    return root


def _write_release_archive(
    path: Path, *extra_paths: str, include_strings: bool = True
) -> None:
    """Write a minimal release archive with optional extra entries."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("__init__.py", "")
        archive.writestr(
            "manifest.json",
            json.dumps({"version": _manifest_version()}),
        )
        if include_strings:
            archive.writestr("strings.json", "{}")
        for extra_path in extra_paths:
            archive.writestr(extra_path, "unexpected")


def _run_release_checker(
    root: Path, *arguments: str | Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, root / "script" / "check-release", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_source_versions(
    root: Path, *, manifest_version: str, project_version: str
) -> None:
    manifest_path = root / "custom_components" / "solarmax" / "manifest.json"
    manifest = _load_json(manifest_path)
    manifest["version"] = manifest_version
    manifest_path.write_text(f"{json.dumps(manifest, indent=2)}\n", encoding="utf-8")

    project_path = root / "pyproject.toml"
    project = project_path.read_text(encoding="utf-8")
    project_path.write_text(
        project.replace(
            f'version = "{_project_version()}"', f'version = "{project_version}"'
        ),
        encoding="utf-8",
    )


def test_translation_files_have_the_same_keys() -> None:
    """Adding a UI string without every locale must fail validation."""
    expected = _leaf_paths(_load_json(_INTEGRATION / "strings.json"))

    for path in sorted((_INTEGRATION / "translations").glob("*.json")):
        actual = _leaf_paths(_load_json(path))
        assert actual == expected, (
            f"{path.name}: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def test_translations_declare_required_reconfiguration_paths() -> None:
    """Catalogs must include every native reconfiguration result path."""
    required_paths = {
        "config.step.reconfigure.title",
        "config.step.reconfigure.description",
        "config.step.reconfigure.data.host",
        "config.step.reconfigure.data.port",
        "config.step.reconfigure.data.address",
        "config.step.reconfigure.data.device_name",
        "config.step.reconfigure.data_description.host",
        "config.step.reconfigure.data_description.port",
        "config.step.reconfigure.data_description.address",
        "config.step.reconfigure.data_description.device_name",
        "config.error.cannot_connect",
        "config.error.reload_failed",
        "config.abort.reconfigure_successful",
        "config.abort.already_configured",
        "options.error.reload_failed",
        "issues.connection_issues.fix_flow.step.init.data.host",
        "issues.connection_issues.fix_flow.step.init.data.port",
        "issues.connection_issues.fix_flow.error.cannot_connect",
        "issues.connection_issues.fix_flow.error.unknown",
        "issues.connection_issues.fix_flow.error.reload_failed",
        "issues.connection_issues.fix_flow.abort.entry_missing",
        "issues.connection_issues.fix_flow.abort.already_configured",
        "issues.connection_issues.fix_flow.abort.repair_pending_verification",
        "issues.connection_issues.fix_flow.abort.issue_missing",
    }

    for path in [
        _INTEGRATION / "strings.json",
        *sorted((_INTEGRATION / "translations").glob("*.json")),
    ]:
        actual = _leaf_paths(_load_json(path))
        assert required_paths <= actual, (
            f"{path.name}: missing required translation paths "
            f"{sorted(required_paths - actual)}"
        )


def test_manifest_and_project_versions_match() -> None:
    """Python metadata must represent the manifest's SemVer version."""
    assert _project_version() == parse_release_tag(f"v{_manifest_version()}").project


def test_hacs_zip_settings_are_coherent() -> None:
    """HACS must install the same archive that the release workflow validates."""
    hacs = _load_json(_ROOT / "hacs.json")

    assert hacs.get("zip_release") is True
    assert hacs.get("filename") == "solarmax.zip"


def test_hassfest_step_does_not_pass_unsupported_inputs() -> None:
    """The pinned Hassfest action declares no configurable inputs."""
    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "validate.yml").read_text()
    )
    hassfest_steps = workflow["jobs"]["hassfest"]["steps"]
    hassfest_step = next(
        step
        for step in hassfest_steps
        if step.get("uses", "").startswith("home-assistant/actions/hassfest@")
    )

    assert "with" not in hassfest_step


def test_release_workflow_prepares_changes_through_a_pull_request() -> None:
    """A release request must preserve review and protected-main checks."""
    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "release.yml").read_text()
    )
    triggers = workflow[True]
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["push"]["paths"] == ["custom_components/solarmax/manifest.json"]

    prepare = workflow["jobs"]["prepare"]
    assert prepare["permissions"] == {"contents": "write"}
    prepare_commands = "\n".join(
        step["run"] for step in prepare["steps"] if "run" in step
    )
    assert "script/prepare-release" in prepare_commands
    assert "git push" in prepare_commands
    assert "compare/main..." in prepare_commands
    assert "git remote set-url" not in prepare_commands
    assert "x-access-token" not in prepare_commands
    assert "GIT_CONFIG_KEY_0=http.https://github.com/.extraheader" in prepare_commands

    release = workflow["jobs"]["release"]
    assert release["needs"] == "prepare"
    assert "needs.prepare.outputs.ready" in release["if"]
    release_commands = "\n".join(
        step["run"] for step in release["steps"] if "run" in step
    )
    assert "--prefix=solarmax/" not in release_commands
    assert '"$RELEASE_ROOT/solarmax"' in release_commands


def test_supported_python_versions_use_distinct_home_assistant_stacks() -> None:
    """Minimum, current, and newest Python lanes must not collapse together."""
    minimum = _active_requirement(
        "requirements_min.txt", "pytest-homeassistant-custom-component", "3.12"
    )
    current = _active_requirement(
        "requirements_test.txt", "pytest-homeassistant-custom-component", "3.13"
    )
    newest = _active_requirement(
        "requirements_test.txt", "pytest-homeassistant-custom-component", "3.14"
    )

    assert _exact_version(minimum) < _exact_version(current) < _exact_version(newest)


def test_quality_chardet_constraint_matches_requests() -> None:
    """Quality tooling may use chardet 5 but must exclude unsupported major 6+."""
    requirement = _active_requirement("requirements_quality.txt", "chardet", "3.14")

    assert requirement.specifier.contains("5.2.0")
    assert not requirement.specifier.contains("6.0.0")


def test_actionlint_runs_once_in_validation() -> None:
    """The dedicated workflow job owns actionlint in CI."""
    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "validate.yml").read_text()
    )
    workflow_lint = workflow["jobs"]["workflow-lint"]
    assert any("actionlint" in step.get("run", "") for step in workflow_lint["steps"])

    quality_check = next(
        step
        for step in workflow["jobs"]["quality"]["steps"]
        if step.get("run") == "script/check"
    )
    assert "actionlint" in quality_check.get("env", {}).get("SKIP", "").split(",")


def test_agent_guides_are_identical() -> None:
    """Codex and Claude must receive the same repository guidance."""
    assert (_ROOT / "AGENTS.md").read_bytes() == (_ROOT / "CLAUDE.md").read_bytes()


def test_local_documentation_links_exist() -> None:
    """Local links in maintained documentation must resolve."""
    documents = [*_ROOT.glob("*.md"), *(_ROOT / "docs").glob("*.md")]
    for document in documents:
        for target in _MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            parsed = urlsplit(target.strip("<>"))
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            linked_path = document.parent / unquote(parsed.path)
            assert linked_path.exists(), f"{document}: missing link target {target}"


def test_release_version_checker_accepts_matching_tag() -> None:
    """A release tag matching both source versions must pass."""
    result = _run_release_checker(_ROOT, f"v{_manifest_version()}")

    assert result.returncode == 0, result.stderr


def test_release_version_checker_rejects_mismatching_tag() -> None:
    """Publishing a tag that disagrees with source metadata must fail."""
    result = _run_release_checker(_ROOT, "v9.9.9")

    assert result.returncode != 0
    assert f"does not match source version {_manifest_version()}" in result.stderr


def test_release_checker_writes_matching_changelog_section(tmp_path: Path) -> None:
    """Release notes must come from the matching changelog section."""
    root = _copy_release_fixture(tmp_path)
    _write_source_versions(
        root,
        manifest_version="1.4.0",
        project_version="1.4.0",
    )
    (root / "CHANGELOG.md").write_text(
        """\
# Changelog

## [1.4.0] - 2026-09-04

### Fixed

- Expected release note.

## [0.0.1] - 2020-01-01

- Older note.
""",
        encoding="utf-8",
    )
    notes = tmp_path / "release-notes.md"

    result = _run_release_checker(
        root,
        "v1.4.0",
        "--notes-output",
        notes,
    )

    assert result.returncode == 0, result.stderr
    content = notes.read_text(encoding="utf-8")
    assert "Expected release note" in content
    assert "Older note" not in content


def test_release_checker_uses_unreleased_notes_for_prerelease(
    tmp_path: Path,
) -> None:
    """Prereleases use Unreleased notes without requiring a versioned section."""
    root = _copy_release_fixture(tmp_path)
    _write_source_versions(
        root,
        manifest_version="1.4.0-test",
        project_version="1.4.0.dev0+test",
    )
    (root / "CHANGELOG.md").write_text(
        """\
# Changelog

## [Unreleased]

### Added

- Preview release note.

## [1.3.3] - 2026-08-11

- Older note.
""",
        encoding="utf-8",
    )
    notes = tmp_path / "release-notes.md"

    result = _run_release_checker(
        root,
        "v1.4.0-test",
        "--notes-output",
        notes,
    )

    assert result.returncode == 0, result.stderr
    content = notes.read_text(encoding="utf-8")
    assert "Preview release note" in content
    assert "Older note" not in content


def test_release_checker_reports_prerelease_to_github(tmp_path: Path) -> None:
    """The workflow receives an explicit prerelease classification."""
    root = _copy_release_fixture(tmp_path)
    _write_source_versions(
        root,
        manifest_version="1.4.0-rc.1",
        project_version="1.4.0.dev0+rc.1",
    )
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n- Preview release note.\n",
        encoding="utf-8",
    )
    output = tmp_path / "github-output"

    result = _run_release_checker(
        root,
        "v1.4.0-rc.1",
        "--github-output",
        output,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == (
        "version=1.4.0-rc.1\nprerelease=true\n"
    )


def test_release_checker_reports_stable_release_to_github(tmp_path: Path) -> None:
    """A stable version must not be marked as a GitHub prerelease."""
    root = _copy_release_fixture(tmp_path)
    _write_source_versions(
        root,
        manifest_version="1.4.0",
        project_version="1.4.0",
    )
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [1.4.0] - 2026-09-04\n\n- Release note.\n",
        encoding="utf-8",
    )
    output = tmp_path / "github-output"

    result = _run_release_checker(
        root,
        "v1.4.0",
        "--github-output",
        output,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == ("version=1.4.0\nprerelease=false\n")


def test_release_checker_rejects_missing_changelog_section(tmp_path: Path) -> None:
    """A release without user-facing notes must fail before publishing."""
    root = _copy_release_fixture(tmp_path)
    _write_source_versions(
        root,
        manifest_version="1.4.0",
        project_version="1.4.0",
    )
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.0.1] - 2020-01-01\n\n- Older note.\n",
        encoding="utf-8",
    )

    result = _run_release_checker(root, "v1.4.0")

    assert result.returncode != 0
    assert "changelog has no section for 1.4.0" in result.stderr


def test_release_checker_accepts_expected_archive_layout(tmp_path: Path) -> None:
    """HACS-ready archives contain integration files directly at their root."""
    archive = tmp_path / "solarmax.zip"
    _write_release_archive(archive)

    result = _run_release_checker(
        _ROOT, f"v{_manifest_version()}", "--archive", archive
    )

    assert result.returncode == 0, result.stderr


def test_release_checker_rejects_nested_integration_directory(tmp_path: Path) -> None:
    """A wrapped archive would install as solarmax/solarmax through HACS."""
    archive = tmp_path / "solarmax.zip"
    with zipfile.ZipFile(archive, "w") as packaged:
        packaged.writestr("solarmax/__init__.py", "")
        packaged.writestr(
            "solarmax/manifest.json",
            json.dumps({"version": _manifest_version()}),
        )
        packaged.writestr("solarmax/strings.json", "{}")

    result = _run_release_checker(
        _ROOT, f"v{_manifest_version()}", "--archive", archive
    )

    assert result.returncode != 0
    assert "archive contains forbidden path: solarmax/__init__.py" in result.stderr


def test_release_checker_rejects_forbidden_archive_path(tmp_path: Path) -> None:
    """Caches and bytecode must not enter a public release asset."""
    archive = tmp_path / "solarmax.zip"
    _write_release_archive(archive, "__pycache__/const.cpython-314.pyc")

    result = _run_release_checker(
        _ROOT, f"v{_manifest_version()}", "--archive", archive
    )

    assert result.returncode != 0
    assert "archive contains forbidden path" in result.stderr


def test_release_checker_requires_complete_archive_layout(tmp_path: Path) -> None:
    """A release without user-facing strings must fail inspection."""
    archive = tmp_path / "solarmax.zip"
    _write_release_archive(archive, include_strings=False)

    result = _run_release_checker(
        _ROOT, f"v{_manifest_version()}", "--archive", archive
    )

    assert result.returncode != 0
    assert "archive is missing required files: strings.json" in result.stderr


def test_release_checker_rejects_invalid_packaged_json(tmp_path: Path) -> None:
    """Malformed translation JSON must fail before the archive is tagged."""
    archive = tmp_path / "solarmax.zip"
    _write_release_archive(archive, "translations/en.json")

    result = _run_release_checker(
        _ROOT, f"v{_manifest_version()}", "--archive", archive
    )

    assert result.returncode != 0
    assert "archive contains invalid JSON: translations/en.json" in result.stderr
