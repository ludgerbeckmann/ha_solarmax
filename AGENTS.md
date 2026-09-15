# Repository Agent Guide

Codex and Claude use the same repository context. Keep `AGENTS.md` and `CLAUDE.md` byte-identical; repository tests enforce this contract.

## Start here

- Read `README.md` for user-visible behavior and configuration.
- Read `docs/architecture.md` before changing protocol, connection, polling, availability, or nighttime behavior.
- Read `CONTRIBUTING.md` before changing tests, translations, dependencies, CI, or releases.
- Use `docs/troubleshooting.md` when investigating device or network reports.

## Work contract

1. Inspect the current branch and worktree before editing. Preserve unrelated user changes.
2. Make the smallest change that satisfies the request and keep behavior changes covered by tests.
3. Run focused tests while iterating, then run `script/check` before completion.
4. Update user documentation and the changelog when behavior changes.
5. Keep `AGENTS.md` and `CLAUDE.md` identical.

## System invariants

- SolarMax inverters accept one TCP client. `SolarmaxLink` owns one persistent connection, reuses it across polls, and closes it deterministically.
- `ConnectionEngine.poll()` returns an `EngineSnapshot`; connection and protocol failures are represented as engine state rather than escaping into the Home Assistant coordinator.
- Initial configuration probes the inverter before creating an entry. The options flow validates and reloads without opening a competing connection.
- `EngineState` distinguishes startup uncertainty, online operation, expected offline periods, and genuine faults. Availability, repairs, and adaptive polling consume that state.
- The checksum setting applies to initial setup and runtime requests. Preserve the ability to ignore checksums for devices with non-standard firmware.
- Static and device-identification fields are fetched separately from hot telemetry. Unsupported fields may be omitted by the inverter without failing a poll.
- Entity unique IDs are persistent API. Renaming a sensor key requires a migration in `_UNIQUE_ID_MIGRATIONS`.
- Translation keys must exist in `strings.json` and every file under `custom_components/solarmax/translations/`.

## Verification

`script/check` is the repository-wide gate. A complete change leaves it passing and also passes `git diff --check`.

For a focused test, use the project virtual environment:

```bash
.venv/bin/python -m pytest tests/path.py::test_name -q
```

The emulator in `tools/inverter_emulator.py` provides hardware-free protocol and connection testing. The empirical probe in `tools/probe_connection.py` is for deliberate testing against real hardware and can temporarily occupy the inverter's single client slot.
