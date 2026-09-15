# Contributing

Bug reports, device compatibility results, translations, tests, and code changes are welcome. Check existing issues before opening a new one.

## Local setup

Use Python 3.13 or 3.14 for development. CI also tests the minimum supported pair, Python 3.12 with Home Assistant 2024.12.

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements_dev.txt
script/check
```

`script/check` runs every pre-commit hook, mypy, pytest, and coverage. Some
pre-commit hooks apply safe formatting fixes; review those changes and rerun the
command. Coverage must remain at or above 90 percent.

Run one test while iterating:

```bash
.venv/bin/python -m pytest tests/test_connection_engine.py::test_name -q
```

## Repository map

| Path | Responsibility |
| --- | --- |
| `custom_components/solarmax/protocol.py` | MaxComm framing, checksum validation, parsing, and scaling |
| `custom_components/solarmax/connection.py` | Persistent TCP link and connection state machine |
| `custom_components/solarmax/coordinator.py` | Home Assistant polling, sun policy, and repairs |
| `custom_components/solarmax/sensor.py` | Entity values, availability, and night policies |
| `custom_components/solarmax/config_flow.py` | Initial setup and options flow |
| `tools/inverter_emulator.py` | Hardware-free MaxComm server |
| `tests/emulator.py` | Pytest wrapper for the emulator |

Read [docs/architecture.md](docs/architecture.md) before changing connection, polling, availability, or night behavior.

## Change requirements

- Add focused tests for behavior changes and bug fixes.
- Keep runtime code compatible with Home Assistant 2024.12.
- Add each user-facing string to `strings.json` and every translation file.
- Update `README.md` and `CHANGELOG.md` when users will notice the change.
- Keep `AGENTS.md` and `CLAUDE.md` byte-identical.
- Preserve entity unique IDs. Add a migration when a key must change.

The inverter accepts one TCP client. Tests and probes must close sockets, including failure paths. Do not run the hardware probe while Home Assistant or vendor software holds the inverter connection.

## Pull requests

Keep each pull request focused. Include the problem, the chosen behavior, and the commands you ran. Attach diagnostics or logs for device-specific work after removing private network data.

CI runs one validation workflow for each pull request and for `main`. The
`CI / Merge gate` result covers HACS, Hassfest, workflow linting, and these
Python environments:

| Python | Purpose | Dependencies |
| --- | --- | --- |
| 3.12 | Minimum compatibility with Home Assistant 2024.12 | `requirements_min.txt` |
| 3.13 | Supported-version compatibility | `requirements_test.txt` |
| 3.14 | Formatting, typing, tests, and coverage | `requirements_dev.txt` |

Pull requests must maintain 90 percent coverage both overall and across changed
integration lines. CodeQL reports security findings separately. A weekly canary
tests the newest compatible Home Assistant stack on Python 3.14; maintainers can
also start that workflow manually.

## Translations

Copy `custom_components/solarmax/translations/en.json` to the target BCP 47 language code. Translate values without changing keys or placeholders such as `{host}`, `{port}`, and `{minutes}`.

`tests/test_repository_consistency.py` rejects missing translation keys.

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
`v1.4.0-alpha.1`, `v1.4.0-beta.2`, `v1.4.0-rc.1`, and
`v1.4.0-test`. A stable release moves the Unreleased notes into a dated version
section. A prerelease keeps those notes under Unreleased so later prereleases
and the final stable release retain the complete change summary. The release
workflow uses the Unreleased section for prerelease notes and marks the GitHub
draft as a prerelease.

The workflow validates the source and archive before creating a tag. It also
attests the archive and leaves the GitHub release as a draft. Rerunning the
workflow updates an existing draft only when its tag still points to the same
commit.
