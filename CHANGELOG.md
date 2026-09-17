# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.1] - 2026-09-17

### Fixed

- Fixed `Handler OptionsFlow doesn't support step group_members` when
  opening the inverter group's options (0.7.0). Home Assistant's flow
  manager resolves a step by looking up a method literally named
  `async_step_<step_id>` on the handler; the group's member-selection
  step used that `step_id` but was implemented as a private
  `_async_step_group_members()` helper instead, so HA couldn't find it.
  Renamed to `async_step_group_members()` to match.

## [0.7.0] - 2026-09-17

### Added

- An options step for the inverter group (its own gear icon → **Configure**)
  to choose which configured inverters are summed into it. All of them are
  selected by default, matching the group's previous behavior; saving an
  explicit selection limits the sums to just those inverters, and a newly
  added inverter joins automatically only while no explicit selection has
  been saved yet.

## [0.6.5] - 2026-09-17

### Fixed

- The **Last Connection Fault** sensor forgot its recorded timestamp on
  every restart, not just a nightly one -- `EngineDiagnostics.last_fault_
  started` lives only in the connection engine's memory, same as the
  night-value caches fixed in 0.6.3/0.6.4, defeating the point of a
  durable "when did this last happen" signal for monitoring/alerting.
  `SolarmaxLastFaultSensor` now also extends `RestoreSensor` and falls
  back to the restored timestamp until a real new fault (if any) replaces
  it.

## [0.6.4] - 2026-09-17

### Fixed

- Energy today (`KDY`, `HOLD_UNTIL_MIDNIGHT` policy) had the same
  restart gap just fixed for ZERO-policy sensors, in its own "past
  midnight, reset to zero" branch: it checked only the engine's live
  cache, not a restored value, so a restart after midnight but before
  the next successful poll showed unavailable instead of the honest
  zero for the new day.

## [0.6.3] - 2026-09-17

### Fixed

- ZERO-policy night sensors (AC power, DC power, relative power, currents,
  voltages) still showed unavailable after a Home Assistant restart at
  night, the same gap 0.5.4 fixed for HOLD-policy sensors but missed here:
  the ZERO branch returned "unavailable" whenever the engine's cache had
  nothing yet, without checking for a restored value first. It now treats
  a restored value the same way live data is treated -- as proof this
  register exists on this inverter -- and shows the honest synthetic zero
  instead. A register that has never reported anything (unsupported on
  this model, or a brand new entity) is still correctly unavailable.

## [0.6.2] - 2026-09-17

### Changed

- The Auto Release workflow now builds each GitHub release's notes from
  that version's own `CHANGELOG.md` section instead of GitHub's
  `generate_release_notes`. That feature lists merged pull requests since
  the last tag, but every change here goes straight to `main` without a
  PR, so it had nothing to list and every release body was empty except
  for the "Full Changelog" comparison line. The changelog entries are
  already written for this purpose, so the workflow now extracts the
  matching version section and appends the same comparison link itself.

## [0.6.1] - 2026-09-17

### Changed

- Dropped the "(this fork)" annotation from the second copyright line in
  `LICENSE`. It was never required (GitHub already detects the file as
  MIT either way) and is now redundant next to the README's new Credits
  section — a license file's copyright lines read better as plain
  attributions rather than carrying explanatory text.

## [0.6.0] - 2026-09-17

### Added

- A "Credits" section in the README crediting the original project this
  fork started from (`oschick/solarmax-ha-integration` by Ole
  Schicketanz). The repository's early history was uploaded rather than
  created via GitHub's own Fork button, so GitHub shows no automatic
  "forked from" link on the repo page; until now, the only place the
  origin was recorded at all was the copyright line in `LICENSE`, which a
  README/HACS visitor is unlikely to ever open.

## [0.5.5] - 2026-09-17

### Fixed

- The GitHub license badge in the README rendered as a broken image inside
  Home Assistant's own documentation panel (Settings → Devices & Services
  → Solarmax Inverter). Its markdown was `[![...](https://img.shields.io/
  ...)](LICENSE)` — an absolute image URL wrapped in a *relative* link.
  Home Assistant's markdown renderer rewrites relative links to
  `raw.githubusercontent.com/<owner>/<repo>/<version>/<path>`, but mishandled
  this combination and concatenated that prefix onto the already-absolute
  image URL instead of leaving it alone. Every other badge in the README
  already uses an absolute link and was unaffected. Made the license
  badge's link (and the plain `LICENSE` link in the License section)
  absolute too, matching the others.

## [0.5.4] - 2026-09-16

### Fixed

- A HOLD-policy night sensor (Energy yesterday/last month/last year,
  Operating hours, Start count, Installed power, min/max voltage/
  temperature, alarm status) now survives a Home Assistant restart that
  happens overnight. These sensors keep showing the last known reading
  while the inverter is expectedly offline, but that reading lived only in
  the connection engine's in-memory cache -- wiped by any restart. A
  restart at night therefore emptied the cache before the inverter is
  reachable again at sunrise, and the sensor showed unavailable instead of
  its last value for the rest of the night. `SolarmaxSensor` now extends
  Home Assistant's `RestoreSensor`, restoring the last known value (and
  its raw_value/code/active_alarms attributes) on startup and using it as
  a fallback for exactly this gap, until a real poll repopulates the
  cache. Reported as `night_value_source: restored` in the entity's
  attributes so it's distinguishable from a live "hold" reading.

## [0.5.3] - 2026-09-16

### Fixed

- Recolored `custom_components/ha_solarmax/brand/icon.png` and `icon@2x.png`
  from black-on-yellow to the actual SolarMax brand colors (orange/gray),
  matching the mark as printed on the inverters themselves. Both were
  regenerated directly from the mark in `logo.png` rather than hand-picked,
  so the colors match the real logo exactly. This only affects this
  repository's own copy — the icon Home Assistant's UI and HACS actually
  display comes from the separate `home-assistant/brands` repository (see
  `CONTRIBUTING.md`), so getting the corrected icon to show up there needs
  its own PR against that repo.

## [0.5.2] - 2026-09-16

### Changed

- Raised the per-poll timeout budget (`POLL_BUDGET_SECONDS`) from 15s to
  20s. In the worst case, a timeout during both the static-field fetch and
  the hot-field fetch can each trigger the link's own reconnect-and-resend
  plus the engine's one retry, stacking to noticeably more than 15s within
  a single `poll()` call. When that happened, the outer poll timeout could
  fire and classify a slow-but-recovering exchange (e.g. a busy multi-
  inverter RS485 bus) as a fault, even though the link wasn't stuck. The
  larger budget gives that worst-case retry chain room to finish.

## [0.5.1] - 2026-09-16

### Fixed

- Fixed a crash downloading diagnostics for the inverter group entry
  (`AttributeError` on `sun_source`/`device_model`/etc., which only exist
  on a single-inverter `SolarmaxCoordinator`). `diagnostics.py` was never
  updated when the group entry type was added in 0.4.0. It now branches
  on the entry type, same as `__init__.py`, `config_flow.py`, and
  `sensor.py` already do, and returns the group's computed sums instead.

## [0.5.0] - 2026-09-16

### Added

- A new **Last Connection Fault** sensor recording when the most recent
  unexpected daytime connection fault began, kept visible after recovery
  as a monitoring/alerting signal (e.g. "notify me if this changed in the
  last few minutes"). `EngineDiagnostics.last_fault_started` is set once
  per fault episode and never cleared, unlike the transient `fault_since`
  used for repair-issue timing. A startup-grace reconnect and any
  disconnect the engine can already explain (shutdown evidence, or
  darkness below the twilight threshold) never set it, so routine restarts,
  updates, and nightly shutdowns are never recorded as a fault.

## [0.4.4] - 2026-09-16

### Changed

- Enabled by default for newly created entities: Energy yesterday (`KLD`),
  Energy last month (`KLM`), Energy last year (`KLY`), Relative power
  (`PRL`), Operating hours (`KHR`), Start count (`CAC`), and Installed
  power (`PIN`) — previously opt-in. This only affects entities Home
  Assistant has not created yet (a new inverter, or one of these enabled
  for the first time); existing disabled entities keep their current
  state.

## [0.4.3] - 2026-09-16

### Fixed

- Removed the README's "Upgrading and downgrading" section, which
  referenced `v1.4.0`/`v1.3.3` — leftover version numbers from the
  pre-fork upstream project that never occurred in this fork's own
  history (which started at `0.1.0`). The config entry schema version 2
  migration it described has been part of this fork since its first
  release, so there was nothing real left for the section to warn about.

## [0.4.2] - 2026-09-16

### Changed

- Added SolarMax 13MT3 to the confirmed-compatible inverter models in the
  README.

## [0.4.1] - 2026-09-16

### Changed

- Renamed the integration from "Solarmax Inverter Integration" to
  "Solarmax Inverter" in `manifest.json` and `hacs.json` — Home Assistant
  already shows it in an integrations context, so the suffix was
  redundant.
- **Keep sensor values overnight** now defaults to on for newly configured
  inverters, applying the synthetic night policies out of the box instead
  of leaving measurement entities unavailable overnight until enabled.
  Existing entries keep their current setting; this only changes the
  default a new entry starts with.

## [0.4.0] - 2026-09-16

### Added

- An optional inverter group entry (`Add integration` → **Solarmax
  Inverter** → **Add the inverter group**). It creates one virtual device
  with a sum entity for every register reported by at least one configured
  inverter, so a combined AC Power (and similar) is available without
  manually building Home Assistant helper groups per entity. Only one
  group can exist; inverters added or removed later are picked up
  automatically. A register missing or unavailable on one inverter is left
  out of that register's sum rather than invalidating it; a sum entity is
  unavailable only when no inverter currently reports that register.

## [0.3.2] - 2026-09-16

### Changed

- Replaced the per-host:port bus lock (`endpoint_bus_lock`, added in 0.3.0)
  with a shared, reference-counted `SolarmaxLink`
  (`configuration.acquire_endpoint_link()` / `release_endpoint_link()`).
  The bus lock only serialized requests; it did not stop two config entries
  on the same MaxComm gateway from each holding their own persistent TCP
  connection open at the same time. Per this project's own documented
  constraint, a SolarMax endpoint accepts only one TCP client — if that
  holds at the gateway level for inverters sharing one converter, two
  simultaneously open connections could misbehave independent of whether
  requests on them were ever sent at the same instant. Every config entry
  on a given host:port, and `validate_connection()`'s probe, now share one
  connection instead, opened on first use and closed only once the last
  user releases it — serialization falls out of the link's own existing
  request lock, so the separate bus lock is no longer needed.
- `SolarmaxLink.disconnect()` now waits for the request lock before
  aborting the transport, so it can no longer cut off another entry's
  in-flight exchange on a shared link.

## [0.3.1] - 2026-09-16

### Fixed

- Restored the missing `.github/workflows/validate.yml` (HACS + Hassfest
  validation on push/PR/weekly). The `CHANGELOG` had already recorded this
  workflow as added in `0.1.1`, and the README's Validate badge referenced
  it, but the file itself was never actually present in the repository.

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

[Unreleased]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.7.1...HEAD
[0.7.1]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.6.5...v0.7.0
[0.6.5]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.6.4...v0.6.5
[0.6.4]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.5.5...v0.6.0
[0.5.5]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.5.4...v0.5.5
[0.5.4]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.4.4...v0.5.0
[0.4.4]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.2.4...v0.3.0
[0.2.4]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.2.1...v0.2.3
[0.2.1]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/ludgerbeckmann/ha_solarmax/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/ludgerbeckmann/ha_solarmax/releases/tag/v0.1.0
