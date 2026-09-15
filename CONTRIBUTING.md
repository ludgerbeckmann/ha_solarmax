# Contributing

Bug reports, device compatibility results, translations, tests, and code changes are welcome. Check existing issues before opening a new one.

## Local setup

Use Python 3.13 or 3.14 for development. The integration supports the
minimum pair, Python 3.12 with Home Assistant 2024.12.

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements_quality.txt
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy custom_components/solarmax
```

There is currently no automated test suite for this fork. If you add one,
update this section and `pyproject.toml` (`[tool.pytest.ini_options]`,
`[tool.coverage.*]`) accordingly.

## Repository map

| Path | Responsibility |
| --- | --- |
| `custom_components/solarmax/protocol.py` | MaxComm framing, checksum validation, parsing, and scaling |
| `custom_components/solarmax/connection.py` | Persistent TCP link and connection state machine |
| `custom_components/solarmax/coordinator.py` | Home Assistant polling, sun policy, and repairs |
| `custom_components/solarmax/sensor.py` | Entity values, availability, and night policies |
| `custom_components/solarmax/config_flow.py` | Initial setup and options flow |
| `tools/inverter_emulator.py` | Hardware-free MaxComm server |

Read [docs/architecture.md](docs/architecture.md) before changing connection, polling, availability, or night behavior.

## Brand icons

`custom_components/solarmax/brand/` holds the icon/logo shown inside this
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
`ruff check .` and `mypy custom_components/solarmax` locally before opening
a PR.

## Translations

Copy `custom_components/solarmax/translations/en.json` to the target BCP 47 language code. Translate values without changing keys or placeholders such as `{host}`, `{port}`, and `{minutes}`.

## Releases

Maintainers prepare and publish a release with these steps:

1. From the Actions page, run **Release** on `main` and enter the new tag.
2. If the source is not prepared, the workflow creates a release branch and
   provides a link for opening its pull request. Open, review, and merge the
   pull request after its checks pass.
3. The merge creates the validated draft release. If the source already
   matches the requested tag, the first workflow run creates the draft.
4. Inspect the draft, its `solarmax.zip` asset, and its changelog notes.
5. Publish the draft.

The workflow accepts stable versions and SemVer prerelease suffixes, including
`v0.1.0-alpha.1`, `v0.1.0-beta.2`, `v0.1.0-rc.1`, and
`v0.1.0-test`. A stable release moves the Unreleased notes into a dated version
section. A prerelease keeps those notes under Unreleased so later prereleases
and the final stable release retain the complete change summary. The release
workflow uses the Unreleased section for prerelease notes and marks the GitHub
draft as a prerelease.

The workflow validates the source and archive before creating a tag. It also
attests the archive and leaves the GitHub release as a draft. Rerunning the
workflow updates an existing draft only when its tag still points to the same
commit.
