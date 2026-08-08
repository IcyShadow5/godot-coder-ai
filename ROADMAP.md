# Roadmap

## Aktueller stabiler Umfang: v0.10.2

- lizenzierter Godot-Korpus (lokal + katalogisiert)
- BPE-Tokenizer
- drei Hardware-/Trainingsprofile + Hardware-Autotuner
- VRAM-Probe und Abschlussreports
- lokales Studio mit Remote-Zugang (Tailscale, lese-/schreibgeschützt)
- Checkpoint-, Daten- und Validator-Pipeline
- Fast Import Mode (`SKIP_PROJECT_IMPORT`, `FAST_STATIC`) mit Error-Rate-Abort
- Studio-Toggles für den schnellen Import (v0.10.2)
- Secret-Scan läuft garantiert in jedem Modus (v0.10.2 Sicherheitsfix)
- Upgrade-Paket bau- und anwendbar aus `upgrade/` (v0.10.2)

## v0.10.1 – abgeschlossen

- Fast Import Mode: `GODOT_CODER_SKIP_PROJECT_IMPORT` + `GODOT_CODER_FAST_STATIC`
- Error-Rate-Abort für hängende Godot-Imports mit Parser-Fallback
- ETA-Cache-Fix (kein „Restzeit wird berechnet"-Flickern mehr)
- Windows Job Objects in `process_control.py` (keine verwaisten Prozessbäume)
- Encoding-Resilienz, atomares Staging, Memmap-Cleanup, Parser-Crash-Recovery,
  OOM-Notfall-Checkpoint, Audit-Snapshots, UI-Suche/Filter

## v0.6.0 – abgeschlossen

Professional Corpus Audit, dokumentbewusste Shards, deterministische Evaluation,
Tokenbudgets, Early Stopping, Hardware-Autotuner, KV-Cache und Studio-Preflight.

## Erst nach erfolgreicher Messphase

1. Datenqualität und Transfer-Benchmark verbessern.
2. Instruction-, Fill-in-the-Middle- und Reparaturformate ergänzen.
3. Lokale Godot-Dokumentationssuche anbinden.
4. Begrenzte Patch-/Test-/Repair-Agenten mit Vorschau und Freigabe bauen.

Instruction-Tuning und Agents bleiben spätere Meilensteine — erst wenn der
Basiskorpus steht und die Messphase abgeschlossen ist.
