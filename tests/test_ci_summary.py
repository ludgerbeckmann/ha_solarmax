"""Tests for the GitHub Actions test-summary helper."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_ci_summary_reports_tests_duration_and_coverage(tmp_path: Path) -> None:
    """CI output must expose the evidence used by the merge gate."""
    (tmp_path / "junit.xml").write_text(
        """\
<testsuites>
  <testsuite tests="12" failures="1" errors="0" skipped="2" time="3.25" />
</testsuites>
""",
        encoding="utf-8",
    )
    (tmp_path / "coverage.xml").write_text(
        '<coverage line-rate="0.9561" />', encoding="utf-8"
    )

    result = subprocess.run(
        [sys.executable, _ROOT / "script" / "ci-summary", tmp_path],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Python: `" in result.stdout
    assert "Home Assistant: `" in result.stdout
    assert "Test plugin: `" in result.stdout
    assert "Tests: **12**" in result.stdout
    assert "failures: **1**" in result.stdout
    assert "skipped: **2**" in result.stdout
    assert "Duration: **3.25s**" in result.stdout
    assert "Coverage: **95.61%**" in result.stdout


def test_ci_summary_allows_test_jobs_without_coverage(tmp_path: Path) -> None:
    """Compatibility jobs must summarize JUnit without a coverage report."""
    (tmp_path / "junit.xml").write_text(
        '<testsuite tests="5" failures="0" errors="0" skipped="0" time="1.5" />',
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, _ROOT / "script" / "ci-summary", tmp_path],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Tests: **5**" in result.stdout
    assert "Coverage:" not in result.stdout
