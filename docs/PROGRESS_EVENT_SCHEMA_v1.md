# Godot Coder AI Progress Event Schema v1

Das Schema transportiert beobachtbare Arbeitsereignisse zwischen CLI-Pipeline,
Jobverwaltung und Studio. Es ist ausdrücklich kein Gedankenprotokoll: Es enthält
nur technische Zustände, Zähler, Pfade, Resultate und Zeitmessungen, die während
der realen Ausführung entstehen. Die Studio-UI zeichnet daraus ihre Fortschritts-
und Restzeit-Anzeigen; die Jobverwaltung speichert die Events für späteren Abruf.

## Transport

CLI-Prozesse schreiben zusätzlich zu normalen Textlogs eine Zeile pro Event:

```text
GCAI_EVENT {"schema":"godot-coder-progress-event", ...}
```

Der Teil nach `GCAI_EVENT ` ist ein JSON-Objekt. Die Jobverwaltung speichert
Events zusammen mit normalen Logs als JSONL unter:

```text
reports/studio_jobs/<job_id>.log.jsonl
```

Ein wiederherstellbarer Snapshot liegt unter:

```text
reports/studio_jobs/<job_id>.snapshot.json
```

Der vollständige menschenlesbare Textlog liegt unter:

```text
reports/studio_jobs/<job_id>.log.txt
```

## Pflichtfelder nach Normalisierung

| Feld | Typ | Bedeutung |
|---|---|---|
| `schema` | String | Aktuell `godot-coder-progress-event`. |
| `schema_version` | Integer | Aktuell `1`. |
| `event` | String | Ereignisname, z. B. `local_project_progress`. |
| `timestamp` | ISO-8601-String | UTC-Zeitstempel, immer mit `Z`. |
| `job_id` | String oder `null` | Studio-Job-ID, soweit verfügbar. |
| `level` | String | `info`, `warning` oder `error`. |

## Häufige optionale Felder

| Feld | Typ | Bedeutung |
|---|---|---|
| `project_index`, `project_total` | Integer | Aktuelles Projekt und Gesamtzahl. |
| `project_name` | String | Anzeigename des Projekts. |
| `project_status` | String | Projektzustand (siehe Zustände). |
| `phase`, `phase_label`, `phase_status` | String | Maschinenname, lesbarer Name und Zustand der Phase. |
| `file_index`, `file_total`, `remaining_files` | Integer | Dateifortschritt. |
| `current_file` | String | Aktuelle relative Datei oder aktueller Arbeitsschritt. |
| `scripts_found`, `trainable_scripts` | Integer | Erkannte und trainingsfähige GDScript-Dateien. |
| `passed`, `warnings`, `failed`, `quarantined` | Integer | Ergebniszähler. |
| `addon_files`, `generated_files` | Integer | Ausgeschlossene Add-on- bzw. Cache-/Importdateien. |
| `accepted` | Integer | Akzeptierte Einträge, z. B. bei der Corpusprüfung. |
| `bytes_received`, `bytes_total` | Integer | Download-Fortschritt in Byte (Remote-Quellen). |
| `next_project`, `next_project_scripts`, `next_phase` | gemischt | Vorschau auf den nächsten Arbeitsschritt. |
| `job_status` | String | Job-Endzustand, z. B. `completed` (Remote-Quellen). |
| `source_url`, `source_name` | String | Gekürzte URL bzw. Dateiname der Remote-Quelle (immer maskiert). |
| `elapsed_seconds` | Zahl | Seit Start verstrichene Zeit. |
| `estimated_remaining_seconds` | Zahl oder `null` | Mittlere Restzeitschätzung. |
| `estimated_remaining_min_seconds`, `estimated_remaining_max_seconds` | Zahl oder `null` | Optionale Schätzspanne. |
| `overall_progress`, `project_progress` | Zahl 0–1 | Gesamt- und Projektfortschritt. |
| `message` | String | Verständliche Statusmeldung. |
| `return_code` | Integer oder `null` | Exitcode eines technischen Unterprozesses. |
| `command`, `parser_output`, `failure_reason` | gemischt | Technische Ansicht; vor Speicherung maskiert. |

Unbekannte Felder bleiben erhalten, damit neue Produzenten zusätzliche
Informationen liefern können, ohne alte Konsumenten zu beschädigen. Fehlende
optionale Felder werden nicht vorausgesetzt. Zahlenfelder werden normalisiert
(negativ oder nicht numerisch → `null`), `overall_progress`/`project_progress`
werden auf den Bereich 0–1 begrenzt.

## Zustände

Unterstützte Projekt-/Phasenzustände:

```text
waiting
running
passed
passed_with_warnings
failed
quarantined
disabled
skipped
completed
stopped
```

## Phasen

### Privater Import (local_sources.py)

| Maschinenname | Anzeige |
|---|---|
| `input_detection` | ZIP oder Ordner erkennen |
| `secure_extract` | Sicher entpacken |
| `project_detection` | `project.godot` erkennen |
| `inventory` | Dateien inventarisieren |
| `cache_exclusion` | Cache- und Importdateien ausschließen |
| `addon_classification` | Add-ons klassifizieren |
| `secret_scan` | Secret-Prüfung |
| `file_size_check` | Dateigrößenprüfung |
| `static_analysis` | Statische GDScript-Prüfung |
| `deduplication` | Quelle deduplizieren |
| `corpus_admission` | Bereinigte Arbeitskopie aufnehmen |
| `godot_validation` | Godot-Projektimport und projektweite Parserprüfung |
| `quarantine_decision` | Quarantäneentscheidung |
| `registry_update` | Corpus-Registry aktualisieren |
| `report_writing` | Abschlussbericht schreiben |

### Corpusprüfung (corpus.py)

| Maschinenname | Anzeige |
|---|---|
| `corpus_validation` | Projektbezogene Corpusprüfung |

### Remote-Quellen (remote_sources.py)

| Maschinenname | Anzeige |
|---|---|
| `remote_link_validation` | Remote-Link sicher prüfen |
| `remote_download` | Quelle auf dem PC herunterladen |

## Ereignisnamen

| Produzent | Ereignisse |
|---|---|
| `local_sources.py` | `local_import_started`, `local_import_plan`, `local_import_item`, `local_import_completed`, `local_import_failed`, `local_import_registry`, `local_import_report`, `local_project_started`, `local_project_phase`, `local_project_progress`, `local_project_completed`, `local_project_failed`, `local_validation_cleanup`, `local_validation_recovery` |
| `corpus.py` | `corpus_validation_progress`, `corpus_validation_completed` |
| `remote_sources.py` | `remote_source_started`, `remote_source_progress`, `remote_source_completed` |

Konsumenten dürfen nur auf `event` + `phase` + `phase_status` + Zähler bauen;
die genaue Feldkombination je Ereignis kann sich erweitern.

## Beispiel

```json
{
  "schema": "godot-coder-progress-event",
  "schema_version": 1,
  "event": "local_project_progress",
  "timestamp": "2026-08-05T14:42:00Z",
  "job_id": "local-source-import-abc123",
  "level": "info",
  "project_index": 2,
  "project_total": 5,
  "project_name": "Embercore",
  "project_status": "running",
  "phase": "static_analysis",
  "phase_status": "running",
  "file_index": 14,
  "file_total": 18,
  "current_file": "scripts/player.gd",
  "passed": 13,
  "warnings": 1,
  "failed": 0,
  "quarantined": 0,
  "elapsed_seconds": 102.0,
  "estimated_remaining_seconds": 145.0,
  "estimated_remaining_min_seconds": 116.0,
  "estimated_remaining_max_seconds": 174.0
}
```

## Restzeitschätzung

Eine Restzeit wird erst ausgegeben, wenn ausreichend Messwerte vorliegen
(mindestens drei Dateidauern oder eine abgeschlossene Projektdauer). Primär
werden beobachtete Dateidauern verwendet; abgeschlossene Projektdauern ergänzen
die Schätzung für verbleibende Projekte. Bei variablen Laufzeiten werden
Minimum und Maximum als Spanne geliefert. Bis dahin zeigt die UI
`Restzeit wird berechnet …`.

Wichtig: Bleibt zwischen zwei Projekten keine Restzeit übrig (`remaining_files`
fällt auf 0), wird die letzte bekannte Schätzung beibehalten statt auf
„wird berechnet“ zurückzufallen. Sonst flackert die Anzeige bei jedem
Projektwechsel zwischen zwei Zuständen.

## Sicherheit

Vor Ausgabe, Persistenz und Export werden typische API-Schlüssel, AWS-Schlüssel,
Bearer-Token, Passwort-/Token-Zuweisungen und PEM-Private-Keys mit `[REDACTED]`
ersetzt. Erkennungsresultate dürfen die gefundenen Geheimwerte selbst nicht
enthalten. Das gilt auch für Remote-Quellen: URLs werden vor dem Loggen auf
Host + Pfad gekürzt (`safe_url_for_log`), Query-Parameter und Fragmente werden
nicht ausgegeben.

## Rückwärtskompatibilität

- Normale Textlogs werden unverändert weitergeführt.
- Alte Jobs ohne Events werden weiterhin angezeigt.
- Für bekannte alte Zeilen wie `local_import=2/5` und `local_project=...`
  existiert ein begrenzter Kompatibilitätsparser in der Jobverwaltung.
- Strukturierte Events sind die primäre Fortschrittsquelle, sobald sie
  vorhanden sind.
- Konsumenten müssen unbekannte Felder ignorieren und fehlende optionale
  Felder tolerieren.
