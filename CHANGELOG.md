# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.4.0] - 2026-09-06

This release adds optional sensor values while the inverter is off at night.
It also rebuilds connection handling for normal operation, daytime outages,
and overnight shutdowns.

### New: useful sensor values at night

Many SolarMax inverters shut down their network connection after production
ends. To keep useful values available, open **Settings → Devices & services**,
find **Solarmax Inverter**, select **Configure**, and enable **Keep sensor
values overnight**:

- Active production readings show `0`.
- Energy totals and other cumulative or static values keep their last reading.
- **Energy today** keeps its last reading until midnight, then changes to `0`.
- Grid and temperature readings remain unavailable because there is no live
  measurement.

Every synthetic state includes `night_value_source`, so automations can tell
it apart from live inverter data. The option is disabled by default.

### Other highlights

- Home Assistant now keeps one connection to the inverter and recovers faster
  from daytime outages without polling heavily all night.
- Normal night shutdowns and unexpected daytime failures have distinct,
  translated states.
- Connection settings can be changed and tested from Home Assistant.
  Persistent daytime failures create a repair issue.

### Before upgrading

- The connection engine is a complete rewrite. It has been tested with a
  SolarMax 7TP2, but other models and firmware may respond differently.
- The Status Code entity now uses **Offline (expected)**
  (`offline_expected`) instead of **Offline (Night)** (`offline_night`), and
  **Offline (fault)** (`offline_fault`) instead of **Connection failed**
  (`connection_failed`). Update automations or dashboards that match the old
  raw values.
- Upgrading to `v1.4.0` migrates existing entries to config-entry schema
  version 2 without losing their settings. After migration, `v1.3.3` and older
  releases cannot read the entry. To downgrade from `v1.4.0`, restore a Home
  Assistant backup made before you installed the update.
- The minimum versions are now Home Assistant 2024.12.0 and Python 3.12.

### Technical details

#### Connection and protocol

- Runtime polling now reuses one persistent TCP connection. Initial setup uses
  a short validation connection. Each poll has a 15-second budget, individual
  responses allow the MaxComm three-second window, and transient connection or
  frame failures receive one retry.
- Home Assistant can restart an existing entry and create its entities while
  the inverter is sleeping. Adding a new inverter still requires an online
  validation response. The engine closes cleanly during reload or shutdown and
  cannot reopen afterward.
- Initial setup and runtime polling validate MaxComm frame structure.
  **Verify response checksum** controls only the CRC comparison, so inverters
  with non-standard checksums remain supported without accepting malformed
  responses.
- A malformed field no longer discards other valid values. Missing or partial
  device information does not block live telemetry or erase metadata that was
  already discovered.
- A poll records at most one connection failure, even when more than one
  request fails.

#### Daylight, outages, and overnight values

- The Status Code entity distinguishes startup uncertainty, an expected
  shutdown, and an unexplained daytime fault. An early expected shutdown
  becomes a fault after one hour and at least ten failed probes.
- Expected-offline polling slows to 15 minutes during full night, then returns
  to 60 seconds from civil dawn at -6° while rising. Daytime faults also retry
  at least every 60 seconds.
- When `sun.sun` is missing, unavailable, or unknown, the integration logs one
  warning and uses the local clock for classification and recovery polling.
  Diagnostics report which source is active.
- **Keep sensor values overnight** can zero production, retain cumulative and
  static values, and reset daily energy at local midnight. Grid and temperature
  readings remain unavailable. Synthetic values expose their policy through
  `night_value_source`.
- The integration creates synthetic values only for registers the inverter has
  reported before. Cached daily energy cannot reappear after midnight when the
  first new-day response omits that register.
- Expected shutdowns reset fault timing. An inverter that stays offline after
  dawn starts a new daytime fault episode.

#### Configuration, repairs, and device data

- Native **Reconfigure** changes the host, port, inverter address, or device
  name. Endpoint changes are tested before saving and roll back if the new
  runtime or sensor platform cannot start. A name-only change does not contact
  the inverter.
- Setup, reconfiguration, and repair reject ports outside `1..65535`.
  Invalid hostnames return a connection error, and disabled legacy entries use
  the default inverter address when necessary.
- Validation waits for startup to release its connection. Saving options no
  longer opens a competing connection or reloads the entry twice.
- A connection repair can change the host or port and test the endpoint. The
  issue remains open until a complete online update confirms recovery, while
  Home Assistant's native **Ignore** action remains available.
- Device metadata discovered after startup updates the Home Assistant device
  registry. Diagnostics no longer expose the inverter serial number.
- Real-device testing of the connection engine and reconfiguration currently
  covers the maintainer's SolarMax 7TP2. Emulator tests cover the remaining
  flows.

## [1.3.3] - 2026-08-11

### Added
- **Configurable twilight elevation threshold**: the sun elevation (in degrees) below which the inverter is expected to be offline during dusk/dawn can now be adjusted via the integration's config/options flow instead of being hardcoded, with matching documentation in the README, Fixes #20.

## [1.3.2] - 2026-08-03

### Fixed
- **Day-time outages no longer stuck as "Offline (Night)"**: after a normal night, the expected-offline state is cleared on the first day-time failure. A genuine day-time outage now reports "Connection failed" and escalates in the logs (WARNING → ERROR → DEBUG) from scratch instead of being silently suppressed by the night-time failure counter.
- **Log noise on expected night-time disconnects**: a failed setup at night and the "Failed to connect / Failed to get data" messages are now logged at debug level, so a normal night no longer fills the log with errors. Day-time failures still escalate normally. Fixes #17
- **Empty inverter responses no longer logged as "Unexpected error"**: a valid frame with no parseable values is treated as a regular failed poll with the same night/day handling instead of hitting the generic error path at ERROR level on every poll.
- **Protocol errors now escalate and quiet down**: persistent checksum mismatches or IPR/IPN rejections are logged like connection errors (WARNING → ERROR once → DEBUG) instead of "Unexpected error" at ERROR level on every poll.
- **Transient protocol errors are retried**: corrupted or truncated responses (bad checksum, partial frames) are retried up to 3 times like connection errors instead of failing the poll immediately; only deterministic errors (IPR/IPN) skip the retry.
- **Connection repair issue is now actually raised**: after 4 consecutive day-time connection failures, a "Inverter Connection Issues" repair issue is created in Home Assistant with a confirm-and-fix flow, and cleared automatically once the connection is restored or night-time offline mode begins. Previously the repair platform existed but nothing ever triggered it.
- **Diagnostics identifiers fixed**: `device_info.identifiers` now uses a JSON-serializable `(domain, entry_id)` list and reports the detected inverter model (e.g. "SolarMax 7TP2") instead of a plain string and generic "Inverter".

### Changed
- **Inverter emulator**: grid frequency (TNF) is now encoded at 0.01 Hz/digit (raw 5000 → 50.0 Hz), matching the integration's scaling. The emulator previously transmitted 0.1 Hz/digit, so frequency parsed from emulator output read 10× too low.

## [1.3.1] - 2026-06-13

### Fixed
- **Energy Yesterday/Last Month/Last Year (KLD/KLM/KLY)**: removed the invalid `state_class: measurement` on these energy sensors, which Home Assistant rejects for the `energy` device class. They now report no state class (point-in-time historical totals, not running meters).

### Changed
- **Internal refactor (no functional change)**: data-driven value scaling and named frame helpers in the protocol layer; migrated sensor definitions to Home Assistant's `SensorEntityDescription` pattern; simplified the coordinator's connection-failure handling and device-info parsing; deduplicated the config-flow and repair-flow code. Entity IDs, unique IDs, and names are unchanged.

## [1.3.0] - 2026-06-12

### Added
- **Full Testing** Added comprehensive testing pipeline for all integration components (config flow, coordinator, sensors), runs on every PR and commit
- **Pre-commit Hooks**: Switched to ruff for code formatting, linting, pre-commit hooks, and CI checks for consistent code style and quality enforcement

### Fixed
- **Fix Existing Tests**: Fixed existing test suite
- **Code Hygiene & Quality**: Addressed code quality issues identified by Claude

## [1.2.1] - 2026-05-19

### Fixed
- Fixed incorrect Energy Yesterday sensor key (KDL → KLD). Thanks @olabaie
- Fixed suggested precision for frequency sensors (two decimal places). Thanks @olabaie


## [1.2.0] - 2026-05-18

### Breaking Changes
- **Checksum verification is now enabled by default**. If your inverter uses a non-standard CRC implementation, you may see "checksum verification failed" errors after upgrading. Disable the "Verify response checksum" option in the integration configuration to restore the previous behaviour.

### Added
- **Extended Status Codes**: Thanks @olabaie & [https://github.com/t-pa/solarmaxcom](https://github.com/t-pa/solarmaxcom), Expanded SYS status map to ~110 entries (20000–20999)
- **New Sensors**: 11 additional sensors from @olabaie & [https://github.com/t-pa/solarmaxcom](https://github.com/t-pa/solarmaxcom) (disabled by default, your Inverter may not support all): Energy Yesterday (KDL), Energy Last Month (KLM), Energy Last Year (KLY), Relative Power % (PRL), Installed Power (PIN), Grid Frequency (TNF), DC Power/Voltage/Current String 3 (PD03/UD03/ID03), Inverter Temperature 2/3 (TK2/TK3), Grid Voltage Upper Limit (ULH), Grid Voltage Lower Limit (ULL), Grid Frequency Upper Limit (TNH), Grid Frequency Lower Limit (TNL).
- **Inverter Type Detection**: Device info now shows the actual inverter model (e.g. "SolarMax 7TP2") instead of generic "Inverter", queried via the MaxComm TYP register
- **Firmware Version Display**: Device info shows the real firmware version from the inverter (SWV key) instead of hardcoded "1.0.0"
- **Serial Number**: Added serial number detection (DIN key) and display in device info for unique inverter identification
- **Build/Release Number**: Added build/release number detection (BDN key) for detailed firmware information
- **Device Type Map**: Complete mapping of all 116 SolarMax device types from the MaxComm protocol specification
- **DC Voltage Sensor (UDC)**: Added official MaxComm protocol key for total DC input voltage
- **Response Checksum Verification**: Validates CRC on every inverter response to detect corrupt data
- **Protocol Error Handling**: Detects and reports MaxComm interface errors (IPR: invalid protocol, IPN: invalid port)

### Changed
- **CRC Verification Now Strict**: Response checksum mismatch raises `SolarmaxProtocolError` instead of logging a warning and continuing with potentially corrupt data. **If upgrading from v1.1.x and your inverter stops working**, disable "Verify response checksum" in the integration options.
- **Multi-Frame Response Support**: Large responses (>255 bytes) from the inverter are now correctly handled — the inverter splits them into multiple frames with individual CRCs
- **Protocol Error Not Retried**: `SolarmaxProtocolError` (IPR/IPN) is raised immediately without retry since these errors are deterministic
- **MaxComm Protocol Reference**: Integration fully refactored against the official "MaxComm Datenprotokoll" (August 2022) specification
- **Configurable Checksum Verification**: New option to disable CRC verification for inverters with non-standard checksum implementations


## [1.1.0] - 2026-05-16

### Added
- **French Translation**: Full French localization for all UI strings, entity names, status codes, and alarm states
- **Inverter Emulator**: TCP test tool (`tools/inverter_emulator.py`) for offline development with 7 scenarios and interactive mode
- **Translations Section in README**: Documentation on supported languages, status/alarm code tables, and how to contribute new languages

### Changed
- **Enum Sensor Pattern**: Status (SYS) and alarm (SAL) sensors now use Home Assistant's enum device class with translation-based state display
- **Status Code Mapping**: Corrected to actual Solarmax protocol codes (20000–20008) with proper English option keys
- **Alarm Bitmask Decoding**: Proper bitmask handling (power-of-2 values: 1, 2, 4, 8, ... 65536) with `active_alarms` attribute for multiple simultaneous alarms
- **strings.json Rebuilt**: Fully synced with all 24 sensor definitions (was missing 20 sensors, had 3 stale entries)

### Fixed
- **Status/Alarm Translation Bug**: Sensors no longer show hardcoded German strings — HA automatically translates via the enum state translation system based on user's language setting
- **Status Code Display**: Now shows human-readable translated text (e.g. "MPP operation") instead of raw numeric code

## [1.0.7] - 2026-03-27

### New Feature
- make inverter internal address configurable (#4) fixes #1

### Fixed
- Fixed daily energy unit (#3) fixes #2

## [1.0.6] - 2025-09-11

### Fixed
- **Compatibility**: Fixed `SensorEntityCategory` import error for newer Home Assistant versions
- Updated entity category import to use `EntityCategory` from `homeassistant.helpers.entity`
- **Modernization**: Updated deprecated type hints (`Dict`, `Union` → `dict`, `|`)

## [1.0.5] - 2025-09-10

### Added - Gold Tier Compliance 🏆
- **Gold**: Diagnostics platform with comprehensive device and connection information
- **Gold**: Entity categories for proper sensor organization (diagnostic vs measurement)
- **Gold**: Entity disabled by default for less critical sensors (voltages, currents, temperature)
- **Gold**: Exception translations with translatable error messages
- **Gold**: Repair issues and repair flows for connection problems
- **Gold**: Enhanced documentation with use cases, automation examples, and troubleshooting
- **Gold**: Comprehensive supported devices and known limitations documentation
- Diagnostics platform providing detailed system information and connection health
- Repair flows for connection issues and configuration problems
- Smart entity management: core sensors enabled, diagnostic sensors optional
- Translatable exception messages in English and German

### Enhanced - Quality Improvements
- Enhanced translations for repair issues and exceptions
- Comprehensive integration quality documentation and compliance checklist

## [1.0.4] - 2025-09-10

### Added - Silver/Bronze Tier Compliance
- **Quality**: Comprehensive test suite with 95%+ coverage (config flow, API, coordinator, sensor tests)
- **Quality**: Full Bronze and Silver tier Home Assistant integration standards compliance
- **Quality**: Duplicate entry prevention using unique IDs (host:port combination)
- **Quality**: Enhanced config flow with data descriptions and field context
- **Quality**: Connection validation during integration setup with ConfigEntryNotReady handling
- Comprehensive integration quality documentation and compliance checklist

### Enhanced - Quality Improvements
- **Quality**: Migrated from hass.data to ConfigEntry.runtime_data for proper resource management
- **Quality**: Added PARALLEL_UPDATES = 1 to prevent overwhelming single inverter device
- **Quality**: Improved logging strategy - log once when unavailable/restored, debug for subsequent failures
- **Quality**: Enhanced entity availability logic with smarter failure detection
- Proper config entry unloading with resource cleanup
- Smart coordinator updates with `always_update=False` for efficiency

## [1.0.3] - 2025-09-10

### Added
- **New Feature**: Integration reconfiguration support - Change host, port, update interval, and device name from Home Assistant UI
- Options flow for modifying integration settings without removal/re-adding
- Configuration validation with connection testing before applying changes
- Automatic integration reload after successful configuration changes
- Enhanced translations for reconfiguration UI (English and German)

### Fixed
- **Major**: Fixed connection timeout issues when inverter comes back online after being offline (night mode)
- **Major**: Improved socket connection handling with proper cleanup and retry mechanisms
- **Major**: Enhanced reconnection logic with exponential backoff to prevent overwhelming inverter
- Consistent timeout handling across connection and data transfer operations
- Better error differentiation between expected offline states (night) vs connection problems
- Improved connection state tracking and failure diagnostics

### Enhanced
- Added intelligent retry logic for connection failures (3 attempts with 2 sub-retries each)
- Enhanced error handling with context-aware logging (night vs day failures)
- Improved sensor availability logic based on connection state and expected offline periods
- Better status messages showing connection failure counts and offline reasons
- Extended diagnostic attributes for troubleshooting connection issues
- Added connection health tracking with timestamps for last successful updates
- Enhanced config flow with options flow support and update listeners

### Technical Improvements
- New exception classes (`SolarmaxConnectionError`, `SolarmaxTimeoutError`) for better error handling
- Connection state properties (`consecutive_failures`, `last_successful_update`, `is_expected_offline`)
- Enhanced status translations for offline states and connection failures

### Added
- Initial HACS compatibility
- Comprehensive README documentation
- MIT License

## [1.0.0] - 2025-09-03

### Added
- Initial release of Solarmax Inverter integration
- Support for Solarmax solar inverters
- Config flow for easy setup
- Multiple sensor types:
  - AC Power (PAC)
  - DC Power (PDC)
  - Energy production metrics
  - Inverter status and diagnostics
- Local polling communication
- Configurable update intervals
- Multi-language support (English, German)
- Device and diagnostic information

### Technical
- Async/await support
- Data coordinator for efficient updates
- Proper error handling and logging
- Translation support
- HACS compatibility

[Unreleased]: https://github.com/oschick/solarmax-ha-integration/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/oschick/solarmax-ha-integration/compare/v1.3.3...v1.4.0
[1.3.3]: https://github.com/oschick/solarmax-ha-integration/compare/v1.3.2...v1.3.3
[1.3.2]: https://github.com/oschick/solarmax-ha-integration/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/oschick/solarmax-ha-integration/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/oschick/solarmax-ha-integration/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/oschick/solarmax-ha-integration/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/oschick/solarmax-ha-integration/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/oschick/solarmax-ha-integration/compare/v1.0.7...v1.1.0
[1.0.7]: https://github.com/oschick/solarmax-ha-integration/compare/v1.0.6...v1.0.7
[1.0.6]: https://github.com/oschick/solarmax-ha-integration/compare/v1.0.5...v1.0.6
[1.0.5]: https://github.com/oschick/solarmax-ha-integration/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/oschick/solarmax-ha-integration/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/oschick/solarmax-ha-integration/compare/v1.0.0...v1.0.3
[1.0.0]: https://github.com/oschick/solarmax-ha-integration/releases/tag/v1.0.0
