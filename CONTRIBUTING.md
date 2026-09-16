# Contributing

Bug reports, device compatibility results, translations, tests, and code changes are welcome. Check existing issues before opening a new one.

## Local setup

Use Python 3.13 or 3.14 for development. The integration supports the
minimum pair, Python 3.12 with Home Assistant 2024.12.

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements_quality.txt
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy custom_components/ha_solarmax
```

There is currently no automated test suite for this fork. If you add one,
update this section and `pyproject.toml` (`[tool.pytest.ini_options]`,
`[tool.coverage.*]`) accordingly.

## Repository map

| Path | Responsibility |
| --- | --- |
| `custom_components/ha_solarmax/protocol.py` | MaxComm framing, checksum validation, parsing, and scaling |
| `custom_components/ha_solarmax/connection.py` | Persistent TCP link and connection state machine |
| `custom_components/ha_solarmax/coordinator.py` | Home Assistant polling, sun policy, and repairs |
| `custom_components/ha_solarmax/sensor.py` | Entity values, availability, and night policies |
| `custom_components/ha_solarmax/config_flow.py` | Initial setup and options flow |
| `tools/inverter_emulator.py` | Hardware-free MaxComm server |

Read [docs/architecture.md](docs/architecture.md) before changing connection, polling, availability, or night behavior.

## Brand icons

`custom_components/ha_solarmax/brand/` holds the icon/logo shown inside this
repository, but Home Assistant's UI and the HACS store pull integration
icons from the central [home-assistant/brands](https://github.com/home-assistant/brands)
repository. To have the icon show up there too, submit a PR to that repo
with domain `solarmax` pointing at this integration.

## Change requirements

- Keep runtime code compatible with Home Assistant 2024.12.
- Add each user-facing string to `strings.json` and every translation file.
- Update `README.md` and `CHANGELOG.md` when users will notice the change.
- Preserve entity unique IDs. Add a migration when a key must change.

The inverter accepts one TCP client. Probes must close sockets, including failure paths. Do not run the hardware probe while Home Assistant or vendor software holds the inverter connection.

## Pull requests

Keep each pull request focused. Include the problem, the chosen behavior, and the commands you ran. Attach diagnostics or logs for device-specific work after removing private network data.

There is currently no CI workflow that runs on pull requests. Run
`ruff check .` and `mypy custom_components/ha_solarmax` locally before opening
a PR.

## Translations

Copy `custom_components/ha_solarmax/translations/en.json` to the target BCP 47 language code. Translate values without changing keys or placeholders such as `{host}`, `{port}`, and `{minutes}`.

## Releases

Releases happen automatically:

1. Bump the version and changelog with `script/prepare-release vX.Y.Z`
   (updates `custom_components/ha_solarmax/manifest.json`, `pyproject.toml`,
   and moves the Unreleased changelog notes into a dated section).
2. Commit and push (or merge a PR) to `main`.
3. The `Auto Release` workflow detects the changed `manifest.json`, and if
   no tag for that version exists yet, builds `ha_solarmax.zip`, creates the
   `vX.Y.Z` tag, and publishes a GitHub release with GitHub's automatically
   generated notes (from merged PRs and commits since the last release).

No manual steps on GitHub are required. Pushing a version bump straight to
`main` publishes the release immediately, so only push once you're ready.
