# Konfigurationsreferenz (configs/*.yaml)

Jede Trainings-/Laufkonfiguration liegt als YAML unter `configs/` und besteht aus
genau zwei Abschnitten: `model:` und `train:`. Die Studio-Trainingsansicht listet
die Profile aus diesem Ordner; die CLI lädt eine Datei mit
`python -m godot_coder.train --config configs/<name>.yaml`.

Echte Autotuner-Ergebnisse heißen `configs/autotuned_*.yaml` und sind
maschinenlokal (gitignored). Eine Vorlage zum Abtippen liegt als
`configs/autotuned_night.example.yaml` im Repo.

## Beispiel

```yaml
model:
  max_seq_len: 1024
  n_layers: 12
  d_model: 768
  n_heads: 12
  d_ff: 2048
  dropout: 0.05
  rope_base: 10000.0
  tie_embeddings: true
  gradient_checkpointing: false
train:
  tokenizer_path: artifacts/tokenizer_bpe_godot.json
  data_dir: data/processed/corpus_v06
  output_dir: checkpoints/v06_balanced
  device: auto
  dtype: bfloat16
  batch_size: 6
  learning_rate: 0.00025
  warmup_steps: 250
  weight_decay: 0.1
  max_steps: null
  target_dataset_passes: 4.0
  save_interval: 100
  keep_last_checkpoints: 3
  early_stopping:
    enabled: true
    patience: 4
    min_delta: 0.01
  compile:
    enabled: false
    mode: default
```

## `model:` — Architektur

| Schlüssel | Default | Bedeutung |
|---|---|---|
| `vocab_size` | 269 | Token-IDs. Muss zum trainierten BPE-Tokenizer passen — Fingerprints verhindern stilles Vermischen. |
| `max_seq_len` | 256 | Kontextlänge in Tokens (mindestens 8). |
| `n_layers` | 4 | Transformer-Schichten. |
| `d_model` | 192 | Modellbreite; muss durch `n_heads` teilbar sein. |
| `n_heads` | 6 | Aufmerksamkeitsköpfe; Kopfbreite muss gerade sein (RoPE). |
| `d_ff` | 512 | Feed-Forward-Breite. |
| `dropout` | 0.0 | Dropout in `[0, 1)`. |
| `rope_base` | 10000.0 | RoPE-Basis > 1. |
| `tie_embeddings` | true | Einbettungen teilen (Input/Output). |
| `gradient_checkpointing` | false | Sparen VRAM, langsameres Training. |

## `train:` — Training

### Daten & Ausgabe

| Schlüssel | Default | Bedeutung |
|---|---|---|
| `tokenizer_path` | `artifacts/tokenizer.json` | BPE-Tokenizer-Datei. |
| `data_dir` | `data/processed` | Trainings-/Validierungsdaten. |
| `output_dir` | `checkpoints/tiny` | Checkpoint-Ziel. |
| `device` | `auto` | `auto`, `cuda` oder `cpu`. |
| `dtype` | `float16` | `float32`, `float16` oder `bfloat16`. |
| `seed` | 1337 | Seed für Datenshuffling und Evaluation. |

### Optimierung

| Schlüssel | Default | Bedeutung |
|---|---|---|
| `batch_size` | 8 | Mikrobatch je Optimizer-Schritt. |
| `gradient_accumulation_steps` | 4 | Gradients sammeln; effektiver Batch = `batch_size × accumulation`. |
| `learning_rate` | 4e-4 | Spitzen-Lernrate (> 0). |
| `min_learning_rate` | 4e-5 | Ziel-Lernrate am Ende. |
| `warmup_steps` | 100 | Warmup-Schritte (kleiner als `max_steps`). |
| `weight_decay` | 0.1 | AdamW-Decay. |
| `beta1` / `beta2` | 0.9 / 0.95 | Adam-Betas in `[0, 1)`. |
| `gradient_clip` | 1.0 | Max. Gradientennorm (> 0). |
| `prefetch_batches` | 0 | Vorab geladene Batches. |

### Abbruch

Mindestens eines von `max_steps`, `max_tokens` oder `target_dataset_passes`
**muss** gesetzt sein; was zuerst endet, gewinnt:

| Schlüssel | Default | Bedeutung |
|---|---|---|
| `max_steps` | 1500 | Maximale Optimizer-Schritte. |
| `max_tokens` | — | Maximale Tokens insgesamt. |
| `target_dataset_passes` | — | Gewünschte Epochen (Token-Multiplikator). |

Schutz gegen ständiges Wiederholen desselben Datensatzes:

| Schlüssel | Default | Bedeutung |
|---|---|---|
| `max_dataset_passes_warning` | 8.0 | Ab hier Warnung im Preflight. |
| `max_dataset_passes_block` | 50.0 | Ab hier blockiert der Preflight den Lauf. |
| `allow_excessive_dataset_passes` | false | Block bewusst umgehen (Studio: gelbe Ampel). |

### Evaluation

| Schlüssel | Default | Bedeutung |
|---|---|---|
| `eval_interval` | 100 | Validierung alle N Schritte. |
| `eval_batches` | 20 | Batches je Validierung. |
| `evaluation_mode` | `fixed` | `fixed`, `random` oder `sliding`. |
| `evaluation_seed` | 7331 | Seed der Evaluationsauswahl. |
| `evaluation_stride` | — | Schrittweite im Sliding-Modus. |
| `validation_interval_tokens` | — | Validierung zusätzlich alle N Tokens. |

### Checkpoints & Logging

| Schlüssel | Default | Bedeutung |
|---|---|---|
| `log_interval` | 10 | Log alle N Schritte. |
| `save_interval` | 100 | Checkpoint alle N Schritte. |
| `save_best_only` | false | Nur das beste Modell speichern. |
| `keep_last_checkpoints` | 3 | Aufbewahrte Step-Checkpoints. |

### Early Stopping (YAML-Unterblock)

```yaml
early_stopping:
  enabled: false
  patience: 5
  min_delta: 0.01
```

### torch.compile (YAML-Unterblock)

```yaml
compile:
  enabled: false
  mode: default   # default | reduce-overhead | max-autotune
```

## Profile im Repo

| Datei | Zweck |
|---|---|
| `corpus_balanced_90m.yaml` | Balanced · 91M — empfohlenes Hauptprofil |
| `corpus_starter_30m.yaml` | Kleinster echter Korpuslauf, ohne Activation Checkpointing |
| `corpus_experimental_163m.yaml` | Größtes Profil (RTX-5060-Erkundung), nur nach Hardware-Probe |
| `corpus_small_8gb.yaml` / `corpus_smoke.yaml` | Ältere Korpusläufe (v04) |
| `curriculum_tiny.yaml` | Curriculum-Datensatz |
| `small_8gb.yaml` / `tiny.yaml` / `smoke.yaml` / `tiny_demo.yaml` | Allgemeine Trainingskonfigurationen |
| `autotuned_night.example.yaml` | Autotuner-Vorlage (maschinenlokal, nicht committen) |

## Prüfregeln (Kurzfassung)

Die Validierung beim Laden lehnt ab, wenn z. B. `d_model` nicht durch `n_heads`
teilbar ist, die Kopfbreite ungerade ist, `dtype`/`evaluation_mode`/`compile_mode`
unbekannt sind, `warmup_steps ≥ max_steps`, oder `max_dataset_passes_block`
unter der Warnschwelle liegt. Fehlertexte nennen das betroffene Feld direkt.
