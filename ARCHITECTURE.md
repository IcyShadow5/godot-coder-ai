# Architektur v0.10.1

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

Tokenizer- oder Architekturänderungen benötigen neue Gewichte, aber keinen Neubau des Studios. Checkpoints speichern Fingerprints, damit inkompatible Daten/Tokenizer nicht still vermischt werden.

## v0.6 Professional Training Core

`licensed sources → cached validation → corpus audit/quarantine → audited documents → BPE → sharded token dataset → deterministic evaluation → token-budgeted training → best checkpoint → cached inference`
