# Audit v0.6 – Professional Training Core

> Historisches Dokument: Das ist der Audit, den ich vor dem v0.6-Umbau geschrieben
> habe. Er beschreibt den Stand von v0.5.2 und was v0.6 daraus gemacht hat.
> Was seitdem passiert ist, steht im `docs/CHANGELOG_v0.10.2.md` — der Audit selbst
> wird nicht laufend aktualisiert.

## Ausgangsarchitektur v0.5.2

Godot Coder AI bestand aus einem Decoder-only Transformer mit RoPE, RMSNorm,
SwiGLU, PyTorch SDPA, BF16/FP16-Autocast, AdamW, Gradient Accumulation,
optionalem Activation Checkpointing, Checkpoints, einer FastAPI-Studiooberfläche
und einer lizenzierten Godot-Corpus-Pipeline.

## Festgestellte Risiken

1. **Datenformat:** `train.bin` und `val.bin` waren fortlaufende Tokenströme
   ohne maschinenlesbaren Dokumentindex. Zufällige Fenster konnten unbemerkt
   Dokumentgrenzen überschreiten.
2. **Evaluation:** Validation-Fenster wurden während jeder Auswertung erneut
   zufällig gezogen. Modellvergleiche waren dadurch nicht exakt reproduzierbar.
3. **Trainingsbudget:** Profile wurden primär durch feste Schrittzahlen
   gesteuert. Bei kleinen Corpora führte das zu extrem vielen
   Datensatzdurchläufen und Überanpassung.
4. **Early Stopping:** Es gab keine automatische Beendigung nach ausbleibender
   Validation-Verbesserung.
5. **Corpus-Qualität:** Exakte Inhaltsduplikate wurden entfernt, aber
   normalisierte und nahe Duplikate, Split-Leaks und unvollständige Fragmente
   wurden nicht als eigener Audit ausgewiesen.
6. **Cache:** Downloads waren gepinnt, aber Godot-Parserprüfungen und feste
   Evaluation-Samples waren nicht fingerprint-basiert wiederverwendbar.
7. **Hardwaremessung:** Drei feste Profile wurden gemessen. Kontextvarianten,
   Checkpointing an/aus, Batches 1/2/4/6/8 und optionales `torch.compile`
   wurden nicht als vollständige Matrix verglichen.
8. **Generierung:** Jedes neue Token berechnete den gesamten Kontext erneut;
   ein KV-Cache fehlte.
9. **Studio:** Es gab keine zentrale Preflight-Ampel mit Blockierungsgründen
   vor einem Nachtlauf.
10. **Benutzerprofile:** Eine syntaktisch gültige YAML-Datei mit falschem
    Wurzeltyp konnte die gesamte Profilauflistung abbrechen.

## v0.6-Migration

- Alte v1-Tokenströme und Checkpoints bleiben lesbar.
- Neue Datensätze verwenden Format v2 mit Shards und Dokumentindex.
- Bestehende Downloads, `.venv`, CUDA/PyTorch, Checkpoints und Reports werden
  nicht gelöscht.
- Der neue Corpus-Audit schreibt in `data/corpus/audited`; das bisherige
  `prepared` bleibt als Fallback erhalten.
- Neue Hauptprofile schreiben in eigene `checkpoints/v06_*`-Ordner.

## Corpus-Pipeline v0.6

`Download → Scan → Godot-Validierung mit Cache → Professional Audit → BPE →
dokumentbewusste Shards`

Der Audit erfasst Quelle, Commit, Ref, SPDX-Lizenz, Attribution, Inhalts- und
normalisierten Hash, SimHash, Projektgruppe, Split, Parserstatus und
Qualitätsstatus. Dateien mit unbekannter Lizenz, Parserfehler, beschädigter
Kodierung, unvollständigen Delimitern oder Split-Leakage werden quarantänisiert.

## Datenformat v2

Jeder Split enthält ein oder mehrere `split-00000.bin`-Shards. `manifest.json`
führt pro Dokument Pfad, Shard, Offset, globale Position, Tokenzahl und Hash.
Der Standard `packed_with_file_sep` erlaubt dokumentübergreifende Fenster nur,
weil jedes Dokument ausdrücklich mit BOS/EOS und `<file_sep>` gerahmt wird.
Der Dokumentindex bleibt für spätere strikte Dokument-Samples verfügbar.

## Training v0.6

Unterstützt werden:

- `max_steps`
- `max_tokens`
- `target_dataset_passes`
- `validation_interval_tokens`
- feste oder Sliding-Window-Evaluation
- Early Stopping mit Patience und Mindestverbesserung
- Warn-/Blockgrenzen für übermäßige Datensatzdurchläufe
- `save_best_only`
- optionales `torch.compile` mit Eager-Fallback
- CPU-Prefetch mit gepinnten CUDA-Transfers

## Hardware

Der Autotuner testet 91M/1024, 91M/2048, 163M/1024 und 163M/2048, jeweils
Checkpointing an/aus, Micro-Batches 1/2/4/6/8 und im vollständigen Modus Eager
gegen `torch.compile`. Jeder Versuch läuft als isolierter Prozess. Empfohlen
werden nur Ergebnisse unter 90 % reserviertem VRAM.

## Inferenz

Ein optionaler KV-Cache wurde ergänzt. Der bisherige vollständige Recompute
bleibt als Fallback erhalten, insbesondere wenn Prompt plus Ausgabe den
konfigurierten Kontext überschreitet.

## Verbleibende Grenzen

- Der Buildrechner besitzt keine RTX 5060 und keine Godot-4.7-Binary.
  CUDA-Autotuning und der reale Audit der lokal bereits heruntergeladenen
  Quellen müssen deshalb auf dem Zielrechner laufen.
- Near-Duplicate-Erkennung ist konservativ und dient als Warnsignal, nicht als
  automatischer Beweis identischen Inhalts.
- Instruction-Tuning und Agenten sind weiterhin ausdrücklich außerhalb von
  v0.6 — siehe `docs/INSTRUCTION_ROADMAP_v0.7.md` und `ROADMAP.md`.
