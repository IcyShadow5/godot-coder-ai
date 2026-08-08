# Changelog

Patch notes, so ich nachher noch weiß, was ich mir dabei gedacht habe.

## v0.10.2 (2026-08-08)

### Behoben

- **FAST_STATIC hat den Secret-Scan übersprungen.** Das war der gefährlichste
  Punkt: `GODOT_CODER_FAST_STATIC=1` sollte nur die langsame AST-Warnungsanalyse
  sparen, hat aber auch den Secret-Scan komplett ausgelassen. Eine einzelne
  `.env`-Datei mit API-Key wäre so direkt ins Trainingskorpus gewandert. Jetzt
  laufen Secret-Scan und Dateigrößenprüfung **immer**; schnell ist nur die
  statische Analyse (`_static_warnings`).
- **`GODOT_CODER_SKIP_PROJECT_IMPORT` wurde ignoriert, wenn `FAST_STATIC=1`
  gesetzt war.** Die Skip-Entscheidung hing an `skip_import and not fast_static` —
  beides gesetzt bedeutete: trotzdem der volle `--import`. Jetzt ist der Skip
  allein maßgeblich; `FAST_STATIC` hat damit nichts zu tun.
- **`validate_dataset.py` hatte ein hartkodiertes 30s-Timeout** und nutzte rohes
  `subprocess.run`. Timeouts konnten auf Windows verwaiste Godot-Prozesse
  hinterlassen. Liest jetzt `GODOT_CODER_PARSER_FILE_TIMEOUT_SECONDS`
  (Standard 10s, wie der Import-Pipeline) und läuft über den Managed-Process-Runner
  mit Job-Object-Aufräumung.
- **Doppelte `encoding_damage`-Warnung** in normalen Imports (einmal im
  Secret-Scan, einmal im Datei-Loop) — jetzt nur noch einmal.
- **Testname im README stimmte nicht**: `test_eta_preserves_last_estimate_on_zero_remaining`
  hieß im Code anders. Umbenannt, damit README und Tests wieder zusammenpassen.
- **`.gitignore` ignorierte `*.bat`/`*.ps1`**, obwohl die Start-/Upgrade-Skripte
  getrackt sind. Entfernt — Skripte sind Quellcode, kein Build-Artefakt.

### Neu

- **Studio-Toggles für den schnellen Import** (Wissensaufbau → Eigene Projekte):
  Projektimport überspringen, AST-Prüfung überspringen, Fehler-Abbruch
  verschärfen (500 → 60 Zeilen). Werden über `extra_env` an den Import-Job
  durchgereicht — kein Shell-Zugriff mehr nötig.
- **`upgrade/`-Ordner im Repo**: `APPLY_V0102_UPGRADE.ps1/.bat`,
  `build_v0102_payload.ps1` und eine README. Damit ist das Upgrade-Paket nicht
  mehr nur extern, sondern reproduzierbar aus dem Repo baubar. Das v0.10.1-Paket
  hatte Dokumentation, Version und `progress_events.py` nie zur Live-Installation
  gebracht — das ist jetzt korrigiert.
- **`configs/autotuned_night.example.yaml`** — Vorlage für Autotuner-Ergebnisse.
  Echte `configs/autotuned_*.yaml` bleiben lokal (jeder PC misst andere Timings)
  und sind gitignored.
- **`docs/INSTALL_v0.10.2.md`** — aktuelle Install-/Upgrade-Anleitung
  (ersetzt `docs/archive/INSTALL_v0.7.9.md`).
- **Historische Dokumente aufgefrischt**: `docs/PROGRESS_EVENT_SCHEMA_v1.md`
  beschreibt jetzt alle 18 Phasen samt Ereignisnamen und Download-Feldern
  (`bytes_received`/`bytes_total`, `accepted`) und den ETA-Cache.
  `docs/AUDIT_v0.6.md` und `docs/INSTRUCTION_ROADMAP_v0.7.md` sind als
  historische Aufzeichnungen markiert und in die neue Dokumentationssprache
  überführt.

### Version

- `__init__.py` und `pyproject.toml` auf `0.10.2` angehoben.

## v0.10.1 (2026-08-07)

### Neu / Behoben

- **Fast Import Mode**: `GODOT_CODER_SKIP_PROJECT_IMPORT=1` (per-file Parser statt
  vollem `--import`, ~4s/Projekt statt 13–37s) und `GODOT_CODER_FAST_STATIC=1`
  (AST-Walk überspringen).
- **Error-Rate Abort**: hängende `--import`-Läufe mit >500 aufeinanderfolgenden
  Fehlerzeilen werden abgebrochen und auf die sichere dateiweise Parserprüfung
  umgestellt (`GODOT_CODER_ERROR_ABORT_THRESHOLD`).
- **ETA-Cache-Fix**: Die Restzeit-Schätzung bleibt zwischen Projekten stabil,
  statt auf „Restzeit wird berechnet" zurückzufallen.
- **Windows Job Objects** in `process_control.py`: beendete Godot-Prozessbäume
  (inkl. Mono-Kinder) können keine Waisen mehr hinterlassen.
- **Per-file Loop-Doppelverarbeitung** behoben (Dateien wurden im Fast-Modus
  doppelt gezählt/geprüft).
- Encoding-resiliente Lese-/Schreibpfade, atomares Staging, Memmap-Cleanup,
  Parser-Crash-Recovery, OOM-Notfall-Checkpoint, Audit-Snapshots und diverse
  UI-Fixes (Suchergebnisse, Filter, Advanced-Toggle, Service-Worker-Cache).

## v0.7.x und älter

Historische Notizen liegen in `docs/archive/` (z. B. `INSTALL_v0.7.9.md`,
`PARSER_FALLBACK_v0.7.5.md`). Änderungen vor v0.7.4 sind nicht mehr einzeln
dokumentiert.
