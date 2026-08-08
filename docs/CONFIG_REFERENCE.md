# Configuration reference (configs/*.yaml)

Every training/run configuration lives as YAML under `configs/` and consists of
exactly two sections: `model:` and `train:`. The Studio training view lists
the profiles from this folder; the CLI loads a file with
`python -m godot_coder.train --config configs/<name>.yaml`.

Real autotuner results are named `configs/autotuned_*.yaml` and are
machine-local (gitignored). A template for typing them out is available as
`configs/autotuned_night.example.yaml` in the repo.

## Example

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

## `model:` - architecture

| Key | Default | Meaning |
|---|---|---|
| `vocab_size` | 269 | Token IDs. Must match the trained BPE tokenizer - fingerprints prevent silent mixing. |
| `max_seq_len` | 256 | Context length in tokens (at least 8). |
| `n_layers` | 4 | Transformer layers. |
| `d_model` | 192 | Model width; must be divisible by `n_heads`. |
| `n_heads` | 6 | Attention heads; head width must be even (RoPE). |
| `d_ff` | 512 | Feed-forward width. |
| `dropout` | 0.0 | Dropout in `[0, 1)`. |
| `rope_base` | 10000.0 | RoPE base > 1. |
| `tie_embeddings` | true | Tie embeddings (input/output). |
| `gradient_checkpointing` | false | Save VRAM, slower training. |

## `train:` - training

### Data & output

| Key | Default | Meaning |
|---|---|---|
| `tokenizer_path` | `artifacts/tokenizer.json` | BPE tokenizer file. |
| `data_dir` | `data/processed` | Training/validation data. |
| `output_dir` | `checkpoints/tiny` | Checkpoint target. |
| `device` | `auto` | `auto`, `cuda` or `cpu`. |
| `dtype` | `float16` | `float32`, `float16` or `bfloat16`. |
| `seed` | 1337 | Seed for data shuffling and evaluation. |

### Optimization

| Key | Default | Meaning |
|---|---|---|
| `batch_size` | 8 | Micro-batch per optimizer step. |
| `gradient_accumulation_steps` | 4 | Accumulate gradients; effective batch = `batch_size x accumulation`. |
| `learning_rate` | 4e-4 | Peak learning rate (> 0). |
| `min_learning_rate` | 4e-5 | Target learning rate at the end. |
| `warmup_steps` | 100 | Warmup steps (smaller than `max_steps`). |
| `weight_decay` | 0.1 | AdamW decay. |
| `beta1` / `beta2` | 0.9 / 0.95 | Adam betas in `[0, 1)`. |
| `gradient_clip` | 1.0 | Max. gradient norm (> 0). |
| `prefetch_batches` | 0 | Preloaded batches. |

### Termination

At least one of `max_steps`, `max_tokens` or `target_dataset_passes`
**must** be set; whichever ends first wins:

| Key | Default | Meaning |
|---|---|---|
| `max_steps` | 1500 | Maximum optimizer steps. |
| `max_tokens` | - | Maximum total tokens. |
| `target_dataset_passes` | - | Desired epochs (token multiplier). |

Protection against endlessly repeating the same dataset:

| Key | Default | Meaning |
|---|---|---|
| `max_dataset_passes_warning` | 8.0 | Warning in the preflight from here. |
| `max_dataset_passes_block` | 50.0 | The preflight blocks the run from here. |
| `allow_excessive_dataset_passes` | false | Deliberately bypass the block (Studio: yellow light). |

### Evaluation

| Key | Default | Meaning |
|---|---|---|
| `eval_interval` | 100 | Validate every N steps. |
| `eval_batches` | 20 | Batches per validation. |
| `evaluation_mode` | `fixed` | `fixed`, `random` or `sliding`. |
| `evaluation_seed` | 7331 | Seed of the evaluation selection. |
| `evaluation_stride` | - | Stride in sliding mode. |
| `validation_interval_tokens` | - | Additionally validate every N tokens. |

### Checkpoints & logging

| Key | Default | Meaning |
|---|---|---|
| `log_interval` | 10 | Log every N steps. |
| `save_interval` | 100 | Checkpoint every N steps. |
| `save_best_only` | false | Only save the best model. |
| `keep_last_checkpoints` | 3 | Retained step checkpoints. |

### Early stopping (YAML sub-block)

```yaml
early_stopping:
  enabled: false
  patience: 5
  min_delta: 0.01
```

### torch.compile (YAML sub-block)

```yaml
compile:
  enabled: false
  mode: default   # default | reduce-overhead | max-autotune
```

## Profiles in the repo

| File | Purpose |
|---|---|
| `corpus_balanced_90m.yaml` | Balanced · 91M - recommended main profile |
| `corpus_starter_30m.yaml` | Smallest real corpus run, without activation checkpointing |
| `corpus_experimental_163m.yaml` | Largest profile (RTX-5060 exploration), only after hardware probe |
| `corpus_small_8gb.yaml` / `corpus_smoke.yaml` | Older corpus runs (v04) |
| `curriculum_tiny.yaml` | Curriculum dataset |
| `small_8gb.yaml` / `tiny.yaml` / `smoke.yaml` / `tiny_demo.yaml` | General training configurations |
| `autotuned_night.example.yaml` | Autotuner template (machine-local, do not commit) |

## Validation rules (short version)

The validation on load rejects when e.g. `d_model` is not divisible by `n_heads`,
the head width is odd, `dtype`/`evaluation_mode`/`compile_mode`
are unknown, `warmup_steps >= max_steps`, or `max_dataset_passes_block`
is below the warning threshold. Error texts name the affected field directly.

## Corpus validation behavior (pipeline context, not YAML)

The sections above configure training only. Corpus validation
(`corpus validate`) is pipeline behavior; since v0.10.5 no record is kept
unverified:

- Every `.gd` script is verified by Godot itself — a full project import for
  projects, standalone `--check-only` parses for addon scripts and for any
  record that could not be positively checked inside its project (a
  `context_warning`: the resource did not load, no checker marker was
  produced, or the project import/checker failed or timed out).
- An unambiguous syntax error always becomes a hard exclusion
  (`syntax_error`); incompatible Godot-3 projects are hard-excluded too.
  Everything else is admitted.
- Only a truly missing source file skips the parse, and that is recorded
  explicitly instead of being silent.
- Per-file parses respect the same `GODOT_CODER_VALIDATION_TIMEOUT_SECONDS`
  and idle timeouts as project imports.
- The validation cache (`godot_project_validation_v4.json`) keys decisions
  by validator version, so older v3 decisions are never replayed — the next
  `validate` re-checks everything with the current pipeline.

Details and patch notes: `README.md`, `docs/CHANGELOG_v0.10.4.md`,
`docs/CHANGELOG_v0.10.5.md`.
