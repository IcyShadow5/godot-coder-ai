# Architektur v0.10.2

```text
Lizenzierte Godot-Quellen
  -> Git-Ref + Commit + Lizenzmanifest
  -> Filterung / Deduplizierung / repositoryweise Splits
  -> Godot-Validierung
  -> Byte-BPE-Tokenizer
  -> versionierte Tokenströme mit Hashes
  -> Decoder-only Transformer
  -> Training + Validation + Checkpoints
  -> verifizierte Token/VRAM/Timing-Reports
  -> Generierung -> Godot-Prüfung
```

## Trennung der Schichten

- `corpus.py`, `data.py`, `tokenizer.py`: reproduzierbare Datenpipeline.
- `model.py`, `train.py`, `checkpoint.py`: Modell und Training.
- `generate.py`, `evaluate.py`, `benchmark.py`: Inferenz und Messung.
- `ui/`: lokale Oberfläche; sie verwendet denselben Kern und besitzt keine zweite Modelllogik.
- `jobs.py`: isolierte, stoppbare Prozesse für Training, Git und Hardwareproben.

Tokenizer- oder Architekturänderungen benötigen neue Gewichte, aber keinen
Neubau des Studios. Checkpoints speichern Fingerprints, damit inkompatible
Daten/Tokenizer nicht still vermischt werden.

## Validierungspfade (local_sources.py)

Es gibt zwei Godot-Prüfmodi, die je nach Umgebung und Fehlerlage umschalten:

- **`project_import`** – voller headless `--import` in einer isolierten
  Arbeitskopie. Wird durch Timeout, Idle-Timeout oder den Error-Rate-Abort
  (>500 Fehlerzeilen ohne Fortschritt) kontrolliert beendet.
- **`gdscript_check`** – dateiweise `--check-only`-Parserprüfung. Wird als
  Parser-Fallback nach einem abgebrochenen Import genutzt und ist der Modus bei
  `GODOT_CODER_SKIP_PROJECT_IMPORT=1`. Statisch unauffällige Skripte werden
  übersprungen, nur Warnungs-verdächtige einzeln geprüft.

`validate_dataset.py` ist der CLI-Nachprüfpass für das fertige Dataset: gleiche
per-file Parserprüfung, gleiches Timeout aus `GODOT_CODER_PARSER_FILE_TIMEOUT_SECONDS`,
gleicher Managed-Process-Runner mit Job-Object-Aufräumung.

## Fast-Static (GODOT_CODER_FAST_STATIC=1)

Überspringt ausschließlich die langsame AST-Warnungsanalyse
(`_static_warnings()`). Der Secret-Scan, die Dateigrößenprüfung, das
Byte-/Zeilen-Zählen und die Deduplizierung laufen in jedem Modus — ein
Sicherheits- und Korrektheitsversprechen seit v0.10.2.

## v0.6 Professional Training Core

`licensed sources → cached validation → corpus audit/quarantine → audited documents → BPE → sharded token dataset → deterministic evaluation → token-budgeted training → best checkpoint → cached inference`
