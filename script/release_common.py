"""Shared release-version parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SEMVER = re.compile(
    r"^v(?P<base>"
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r")"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


@dataclass(frozen=True)
class ReleaseVersion:
    """A release tag and its source-metadata representations."""

    tag: str
    manifest: str
    project: str
    prerelease: str | None

    @property
    def is_prerelease(self) -> bool:
        """Return whether the version has a SemVer prerelease component."""
        return self.prerelease is not None


def _project_local_label(*parts: str | None) -> str:
    value = ".".join(part for part in parts if part)
    return re.sub(r"[^0-9A-Za-z]+", ".", value).strip(".").lower()


def parse_release_tag(tag: str) -> ReleaseVersion:
    """Parse a v-prefixed SemVer tag and derive valid Python metadata."""
    match = _SEMVER.fullmatch(tag)
    if match is None:
        raise ValueError(f"invalid release tag: {tag}")

    prerelease = match.group("prerelease")
    if prerelease and any(
        part.isdigit() and len(part) > 1 and part.startswith("0")
        for part in prerelease.split(".")
    ):
        raise ValueError(f"invalid release tag: {tag}")

    base = match.group("base")
    build = match.group("build")
    if prerelease:
        project = f"{base}.dev0+{_project_local_label(prerelease, build)}"
    elif build:
        project = f"{base}+{_project_local_label(build)}"
    else:
        project = base

    return ReleaseVersion(
        tag=tag,
        manifest=tag.removeprefix("v"),
        project=project,
        prerelease=prerelease,
    )
