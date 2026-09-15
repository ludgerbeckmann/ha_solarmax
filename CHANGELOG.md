# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-09-15

### Changed

- Replaced the mismatched `auto-release.yml` (which watched another
  integration's manifest) with a `Release` workflow that uses
  `script/prepare-release` / `script/check-release` to validate and publish
  a draft GitHub release with `solarmax.zip` and changelog-based notes.
- Added a lightweight `Validate` workflow (HACS + Hassfest) that needs no
  test suite.
- Added `.github/CODEOWNERS` and structured issue templates
  (bug report, feature request).
- Removed release tooling and dependencies that referenced a non-existent
  `tests/` directory (`script/ci-summary`, pytest/coverage config in
  `pyproject.toml`, `requirements_min.txt`, `requirements_test.txt`) and
  trimmed `CONTRIBUTING.md` to match what the repository actually runs.
- Updated `LICENSE` to credit this fork's maintainer alongside the
  original author.

## [0.1.0] - 2026-09-15

Initial release of this integration under `ludgerbeckmann/ha_solarmax`.

### Added

- Local, read-only communication with SolarMax inverters over the MaxComm
  TCP protocol, with no cloud account required.
- Production, energy totals, operating status, alarms, and diagnostic
  measurements as Home Assistant sensor entities.
- One persistent inverter connection with automatic recovery, faster checks
  after daytime failures, and quiet polling overnight.
- Sun-based classification of expected offline periods (night/shutdown)
  versus unexpected daytime connection faults.
- Optional synthetic sensor values overnight (zero/hold/unavailable
  policies per register) for dashboards and energy statistics.
- Native reconfiguration and repair flows in Home Assistant.
- English, German, and French translations.

[Unreleased]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/ludgerbeckmann/ha_solarmax/releases/tag/v0.1.0
