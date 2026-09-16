# ha_solarmax — Kontext für Claude Code

Home Assistant HACS-Integration für SolarMax-Wechselrichter (ältere Modelle
mit MaxComm-TCP-Protokoll, lokal, kein Cloud-Zugang). Repo:
`github.com/ludgerbeckmann/ha_solarmax`. Entwickler/Maintainer: Ludger
Beckmann (`@ludgerbeckmann`).

## Herkunft

Ursprünglich ein Fork von `oschick/solarmax-ha-integration`. Fork-Bereinigung
(Codeowner, Doku-Links, Lizenz, Version) wurde in einer vorherigen Chat-Session
durchgeführt — siehe „Entscheidungen" unten für Details, die sich nicht aus
dem Code allein erschließen.

## Architektur (Kurzfassung)

- `custom_components/ha_solarmax/protocol.py` — reine MaxComm-Codec-Logik
  (Framing, Checksum, Skalierung). Kein I/O, keine HA-Abhängigkeiten.
- `custom_components/ha_solarmax/connection.py` — `SolarmaxLink` (persistente
  TCP-Verbindung) + `ConnectionEngine` (Zustandsmaschine: online / offline
  erwartet / offline Fehler, Arming-Tracking für erwartete Abschaltungen).
- `custom_components/ha_solarmax/coordinator.py` — HA-Polling, Sonnenstand-
  Klassifizierung, Repairs.
- `custom_components/ha_solarmax/sensor.py` — Entities, Verfügbarkeit,
  Nachtwert-Policies (zero/hold/unavailable je Register).
- `custom_components/ha_solarmax/config_flow.py` — Setup, Reconfigure,
  Options-Flow mit Endpoint-Konflikt-Erkennung.
- `tools/inverter_emulator.py` — TCP-Server, der einen Wechselrichter für
  manuelles Testen simuliert (kein pytest-Fixture, eigenständiges Skript).

Ausführliches Modell: `docs/architecture.md`.

Codequalität wurde in einer früheren Session gegengelesen (protocol.py,
connection.py, sensor.py, config_flow.py, diagnostics.py) — durchgehend
sorgfältig, keine funktionalen Bugs gefunden.

## Wichtige Entscheidungen (nicht aus dem Code ersichtlich)

- **Keine automatisierten Tests, bewusst.** Kein `tests/`-Verzeichnis, kein
  pytest, keine Coverage-Pflicht. Konsistent mit dem Schwesterprojekt
  `ha_smart_ventilation` (ebenfalls ohne Tests, seit 80+ Commits stabil).
  `protocol.py` wäre am günstigsten zu testen (reine Funktionen), aber der
  Maintainer hat sich bewusst dagegen entschieden — Testinfrastruktur nicht
  ungefragt wieder einführen.
- **Kein volles CI-Merge-Gate.** `CONTRIBUTING.md` wurde bewusst entschlackt
  (kein pre-commit, kein mypy/pytest-Zwang in CI). Nur `script/check` lokal
  (ruff + mypy).
- **Domain/Ordner-Umbenennung `solarmax` → `ha_solarmax`** (Breaking Change,
  Version 0.2.0): Ordnername unter `custom_components` muss exakt der
  `domain` in `manifest.json` entsprechen; `const.py` `DOMAIN`-Konstante
  ebenfalls angepasst. Jede bestehende Installation muss die Integration
  entfernen und neu hinzufügen (Config-Entry/Unique-IDs hängen an der alten
  Domain).
- **LICENSE**: Original-Copyright (Ole Schicketanz) bewusst erhalten, Zeile
  für den Fork-Maintainer ergänzt — nicht ersetzt.
- **Release-Prozess**: Bewusst simpel gehalten, exakt nach dem Vorbild von
  `ha_smart_ventilation` (nicht das ursprünglich komplexere Draft/PR-Modell
  aus einer früheren Zwischenversion). Siehe unten.

## Release-Prozess

`.github/workflows/release.yml` ("Auto Release"): Bei Push auf `main` mit
geänderter `custom_components/ha_solarmax/manifest.json` prüft der Workflow,
ob der Tag `vX.Y.Z` (aus der Manifest-Version) schon existiert. Falls nicht:
baut `ha_solarmax.zip`, erstellt den Tag, veröffentlicht den Release **direkt**
(kein Draft) mit GitHub-automatisch-generierten Release Notes
(`generate_release_notes: true`, nicht aus CHANGELOG.md).

Ablauf für eine neue Version:

```bash
# 1. Unreleased-Abschnitt in CHANGELOG.md befüllen
# 2. Version bumpen (aktualisiert manifest.json, pyproject.toml, CHANGELOG.md):
python3 script/prepare-release vX.Y.Z
# 3. Committen und auf main pushen — Tag + Release entstehen automatisch
```

`script/check-release` validiert Version/Changelog/Archiv-Konsistenz separat
(lokal aufrufbar, nicht Teil des Auto-Release-Workflows).

`.github/workflows/validate.yml` ("Validate"): HACS- + Hassfest-Validierung
bei Push/PR/wöchentlich — unabhängig vom Release-Workflow, braucht keine
Tests.

## Konventionen für diese Session/zukünftige Arbeit

- **Versionsnummer bei jeder Änderung passend hochsetzen** (Unreleased-Eintrag
  in CHANGELOG.md, dann `script/prepare-release`).
- **Zip-Auslieferung nur auf explizite Anfrage**, nicht automatisch nach jeder
  Änderung.
- **Zip-Dateiname**: `ha_solarmax_<version>.zip` (Unterstrich, kein
  Bindestrich).
- Namensinkonsistenzen zwischen `manifest.json` und `hacs.json` vermeiden —
  beide sollen "Solarmax Inverter Integration" heißen.

## Offener Punkt bei Chat-Ende

Es gab Unklarheit, ob nach der Domain-Umbenennung wirklich alle Dateien
(inkl. `.github/workflows/validate.yml`) auf `main` gepusht wurden — die
README-Badges (Validate, GitHub Release) sollten nach einem vollständigen
Push und mindestens einem erfolgreichen Release/Workflow-Lauf wieder korrekt
rendern. Falls die Validate-Badge weiterhin rot/kaputt aussieht: prüfen, ob
`.github/workflows/validate.yml` tatsächlich im Repo liegt.
