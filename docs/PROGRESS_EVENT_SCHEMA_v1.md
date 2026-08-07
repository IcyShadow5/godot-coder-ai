# Godot Coder AI Progress Event Schema v1

## Zweck

Das Schema transportiert beobachtbare Arbeitsereignisse zwischen CLI-Pipeline, Jobverwaltung und Studio. Es ist ausdrücklich kein Gedankenprotokoll. Es enthält nur technische Zustände, Zähler, Pfade, Resultate und Zeitmessungen, die während der realen Ausführung entstehen.

## Transport

CLI-Prozesse schreiben zusätzlich zu normalen Textlogs eine Zeile pro Event:

```text
GCAI_EVENT {"schema":"godot-coder-progress-event", ...}
```

Der Teil nach `GCAI_EVENT ` ist ein JSON-Objekt. Die Jobverwaltung speichert Events zusammen mit normalen Logs als JSONL unter:

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
| `event` | String | Ereignisname, beispielsweise `local_project_progress`. |
| `timestamp` | ISO-8601-String | UTC-Zeitstempel. |
| `job_id` | String oder `null` | Studio-Job-ID, soweit verfügbar. |
| `level` | String | `info`, `warning` oder `error`. |

## Häufige optionale Felder

| Feld | Typ | Bedeutung |
|---|---|---|
| `project_index`, `project_total` | Integer | Aktuelles Projekt und Gesamtzahl. |
| `project_name` | String | Anzeigename des Projekts. |
| `project_status` | String | Projektzustand. |
| `phase`, `phase_label`, `phase_status` | String | Maschinenname, lesbarer Name und Zustand der Phase. |
| `file_index`, `file_total`, `remaining_files` | Integer | Dateifortschritt. |
| `current_file` | String | Aktuelle relative Datei oder aktueller Arbeitsschritt. |
| `scripts_found`, `trainable_scripts` | Integer | Erkannte und trainingsfähige GDScript-Dateien. |
| `passed`, `warnings`, `failed`, `quarantined` | Integer | Ergebniszähler. |
| `addon_files`, `generated_files` | Integer | Ausgeschlossene Add-on- beziehungsweise Cache-/Importdateien. |
| `next_project`, `next_project_scripts`, `next_phase` | gemischt | Vorschau auf den nächsten Arbeitsschritt. |
| `elapsed_seconds` | Zahl | Seit Start verstrichene Zeit. |
| `estimated_remaining_seconds` | Zahl oder `null` | Mittlere Restzeitschätzung. |
| `estimated_remaining_min_seconds`, `estimated_remaining_max_seconds` | Zahl oder `null` | Optionale Schätzspanne. |
| `overall_progress`, `project_progress` | Zahl 0–1 | Gesamt- und Projektfortschritt. |
| `message` | String | Verständliche Statusmeldung. |
| `return_code` | Integer oder `null` | Exitcode eines technischen Unterprozesses. |
| `command`, `parser_output`, `failure_reason` | gemischt | Technische Ansicht; vor Speicherung maskiert. |

Unbekannte Felder bleiben erhalten, damit neue Produzenten zusätzliche Informationen liefern können, ohne alte Konsumenten zu beschädigen. Fehlende optionale Felder werden nicht vorausgesetzt.

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

## Reale Phasen des privaten Imports

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

Eine Restzeit wird erst ausgegeben, wenn ausreichend Messwerte vorliegen. Primär werden beobachtete Dateidauern verwendet; abgeschlossene Projektdauern ergänzen die Schätzung für verbleibende Projekte. Bei variablen Laufzeiten werden Minimum und Maximum als Spanne geliefert. Bis dahin zeigt die UI `Restzeit wird berechnet …`.

## Sicherheit

Vor Ausgabe, Persistenz und Export werden typische API-Schlüssel, AWS-Schlüssel, Bearer-Token, Passwort-/Token-Zuweisungen und PEM-Private-Keys mit `[REDACTED]` ersetzt. Erkennungsresultate dürfen die gefundenen Geheimwerte selbst nicht enthalten.

## Rückwärtskompatibilität

- Normale Textlogs werden unverändert weitergeführt.
- Alte Jobs ohne Events werden weiterhin angezeigt.
- Für bekannte alte Zeilen wie `local_import=2/5` und `local_project=...` existiert ein begrenzter Kompatibilitätsparser.
- Strukturierte Events sind die primäre Fortschrittsquelle, sobald sie vorhanden sind.
- Konsumenten müssen unbekannte Felder ignorieren und fehlende optionale Felder tolerieren.
