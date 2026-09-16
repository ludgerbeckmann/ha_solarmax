# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-09-16

### Added

- A cross-entry lock (`configuration.endpoint_bus_lock()`) serializing wire
  access per host:port. Multiple inverters reached through the same MaxComm
  TCP gateway (same host:port, different address) sit on one shared bus
  behind it; each config entry previously polled on its own independent
  schedule with no coordination between entries, so two inverters could end
  up exchanging requests on that bus at the same moment. `ConnectionEngine`
  and `validate_connection()` (config flow and repairs) now share this lock
  for any entries pointing at the same host:port.

## [0.2.4] - 2026-09-16

### Fixed

- Fixed a `KeyError: 'host'` crash when opening the "Repair inverter
  connection" dialog from Repairs. Home Assistant's flow manager re-passes
  the repair flow's creation payload (`{"issue_id": ...}`) as `user_input`
  on the very first call to `async_step_init`, which the flow mistook for a
  submitted host/port form and tried to process directly. The initial step
  now unconditionally hands off to a dedicated `async_step_confirm` step
  before ever looking at submitted data.

## [0.2.3] - 2026-09-16

### Changed

- Inverter address field in the config/options flow now uses a typeable
  number box with +/- steppers instead of a slider, making it easier to
  pick an exact address in the 1-249 range.

## [0.2.1] - 2026-09-16

### Changed

- Aligned `manifest.json`'s `name` with `hacs.json` ("Solarmax Inverter
  Integration") so the integration shows the same name everywhere.

## [0.2.0] - 2026-09-16

### Changed

- **Breaking:** renamed the integration folder and domain from `solarmax` to
  `ha_solarmax` (`custom_components/solarmax` → `custom_components/ha_solarmax`,
  `manifest.json` `domain` and the `DOMAIN` constant updated to match). Any
  existing installation must remove and re-add the integration; entity unique
  IDs and the config entry are tied to the old domain and cannot migrate
  automatically.
- Updated the release workflow, `hacs.json`, and documentation to build and
  reference `ha_solarmax.zip` instead of `solarmax.zip`.

## [0.1.2] - 2026-09-15

### Changed

- Replaced the draft/PR-based `Release` workflow with a simple `Auto Release`
  workflow matching the `ha_smart_ventilation` pattern: pushing a version
  bump to `main` tags and publishes the release directly, using GitHub's
  automatically generated release notes instead of the changelog text.

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

[Unreleased]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.2.4...v0.3.0
[0.2.4]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.2.1...v0.2.3
[0.2.1]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/ludgerbeckmann/ha_solarmax/releases/tag/v0.1.0
