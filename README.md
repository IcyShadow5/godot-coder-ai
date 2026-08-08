# Godot Coder AI v0.10.2

A local training studio for building a compact Godot/GDScript language model from scratch — train your own GPT-style model that actually understands GDScript. No cloud, no API keys, everything runs on your machine.

## What This Is

Three things bundled into one project:

1. **Corpus curation** — imports open-source Godot projects (or your own), scans for secrets, validates syntax through Godot's own parser, deduplicates, and assembles a clean training dataset.
2. **Training** — a from-scratch decoder-only transformer trained on your local GPU (PyTorch/CUDA).
3. **Studio (web UI)** — import projects, monitor training, inspect the corpus. Runs on `127.0.0.1:8765`.

I built this because I wanted a model that knows *my* GDScript style. That's why the whole pipeline is private-first: local projects get their own license entry (`LicenseRef-User-Owned-Private`), are never redistributed, and only you can enable them for training.

## What's New in v0.10.2

A correctness + cleanup release. The v0.10.1 speed work (fast import, error-rate abort, ETA caching) is still here — this release fixes two things that bugged me about it, adds Studio toggles for the fast-import flags, and finally puts the upgrade tooling into the repo where it belongs.

### 🔒 FAST_STATIC no longer skips the secret scan

This one was my fault. `GODOT_CODER_FAST_STATIC=1` was supposed to only skip the slow per-file AST walk, but it actually skipped the **secret scan** too — which is exactly how a stray `.env` or hardcoded API key ends up inside a training corpus. Fixed: secret scan and file-size checks **always run**; fast mode only skips the slow `_static_warnings()` analysis. Security is not a place to take shortcuts.

### 🎛️ Studio toggles for fast import (Wissensaufbau → private import)

The fast-import env vars were CLI-only, which made them annoying to use. The private-import panel in the Studio now has three checkboxes:

- **Godot-Projektimport überspringen** — skips the full `--import`, uses the per-file parser instead
- **Statische AST-Prüfung überspringen** — the fast-static path (secret scan still runs!)
- **Fehler-Abbruch verschärfen** — drops the error-abort threshold from 500 to 60 consecutive lines, useful for broken addon projects

They're forwarded to the import job as environment variables, so no shell needed.

### ⏱️ validate_dataset.py honors the same timeout as the Studio

It used a hardcoded 30s timeout and raw `subprocess.run` — a timed-out Godot check could leave an orphaned process behind on Windows. It now reads `GODOT_CODER_PARSER_FILE_TIMEOUT_SECONDS` (default 10s, same as the import pipeline) and runs through the managed process runner with job-object cleanup.

### 📦 Upgrade packages are built from templates in `upgrade/`

The v0.10.1 upgrade package was built ad-hoc outside the repo and only shipped 12 files — the docs and version metadata never made it to the live install. v0.10.2 fixed that (everything that changed now ships together), and the repo carries the mechanics: `upgrade/` contains **templates** for the apply script and payload builder. A ready-made package is assembled from them per release and distributed with the release — the repo itself stays free of built payloads.

### 🧪 Tests

- `test_eta_preserves_last_estimate_on_zero_remaining` — ETA cache carries through zero
- `test_error_line_re_matches_godot_error_output` — abort regex vs. progress regex
- `test_fast_static_still_scans_for_secrets` — v0.10.2 security fix
- `test_fast_static_skips_static_warnings_and_never_double_processes` — fast mode is fast, exactly once
- `test_skip_project_import_wins_over_fast_static` — skip-import is authoritative
- `test_error_rate_abort_triggers_parser_fallback` — stuck `--import` falls back to per-file parser
- `test_local_import_extra_env_maps_request_flags` — Studio toggle → env var mapping

## v0.10.1 Recap (Fast Import & Error-Rate Abort)

### 🚀 Fast Import Mode
- **`GODOT_CODER_SKIP_PROJECT_IMPORT=1`** — skips Godot `--import` entirely; validates each `.gd` file individually via Godot's per-file parser. **Massive speedup**: ~4s/project vs 13–37s/project. For 800+ projects that cuts import from 5–7 hours to under 1 hour.
- **`GODOT_CODER_FAST_STATIC=1`** — skips the redundant per-file AST walk and byte counting during import. Secret scan, file-size check and dedup still run (see the v0.10.2 fix above).

### 🔴 Error-Rate Abort
Godot's `--import` can get stuck on broken addon projects, producing thousands of error lines without progress — and the old `idle_timeout` never triggered because errors count as output. Now:
- Consecutive error lines are tracked via regex (`ERROR:`, `SCRIPT ERROR:`, `Parse Error:`, `failed to load`, `invalid UID`, …)
- The counter resets on progress markers (`[ XX% ]`, `first_scan_filesystem`)
- Default threshold: **500 consecutive errors** (`GODOT_CODER_ERROR_ABORT_THRESHOLD`)
- On abort: the Godot process tree is killed and the safe per-file parser takes over

### 🐛 v0.10.1 Bug Fixes (still relevant)
- **Per-file loop double-execution** — `_static_warnings()` used to run even with `FAST_STATIC=1`, processing every file twice. Now properly guarded.
- **ETA cache flickering** — the estimator preserves its last estimate when `remaining_files` hits zero, so the UI stops flashing "Restzeit wird berechnet".

## Key Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `GODOT_CODER_SKIP_PROJECT_IMPORT` | unset | Skip Godot `--import`, use the per-file parser directly. Authoritative — works even with `FAST_STATIC=1` |
| `GODOT_CODER_FAST_STATIC` | unset | Skip only the slow AST warning walk. Secret scan + file-size checks still run |
| `GODOT_CODER_ERROR_ABORT_THRESHOLD` | 500 | Consecutive error lines before aborting a stuck Godot import |
| `GODOT_CODER_VALIDATION_TIMEOUT_SECONDS` | 120 | Max time for Godot `--import` per project |
| `GODOT_CODER_VALIDATION_IDLE_TIMEOUT_SECONDS` | 30 | Idle timeout (no output) before aborting Godot |
| `GODOT_CODER_PARSER_FILE_TIMEOUT_SECONDS` | 10 | Per-file timeout for the `--check-only` parser (also used by `validate_dataset.py`) |
| `GODOT_CODER_TRAIN_FILE_LIMIT_BYTES` | 4 MiB | Max script size admitted as training data; bigger files are excluded |
| `GODOT_CODER_VALIDATION_RETRY_TIMEOUT_SECONDS` | 180 | Legacy retry timeout, kept for backward-compatible env config — since v0.7.5 the parser fallback replaces the old retry |
| `GODOT_CODER_JOB_ID` | — | Internal: set by the Studio for job-scoped processes. Don't set this by hand |

### Recommended Combos

```powershell
# Fastest import — skip Godot import entirely, validate per file
$env:GODOT_CODER_SKIP_PROJECT_IMPORT = "1"
.\.venv\Scripts\python.exe -m godot_coder.studio

# Fastest without skipping Godot: keep full project validation, skip the AST walk
$env:GODOT_CODER_FAST_STATIC = "1"
.\.venv\Scripts\python.exe -m godot_coder.studio

# Stuck addon projects? Abort earlier (the Studio toggle uses 60)
$env:GODOT_CODER_ERROR_ABORT_THRESHOLD = "60"
.\.venv\Scripts\python.exe -m godot_coder.studio
```

When you skip the project import, full validation is deferred: `validate_dataset.py` runs a complete Godot pass over the assembled dataset (same env-var timeout, managed process runner). For maximum thoroughness during import, leave the env vars off or use only `FAST_STATIC=1`.

## Configuration Files

Every training run reads a YAML config from `configs/` with two top-level mappings — `model:` and `train:` (the full key reference lives in `docs/CONFIG_REFERENCE.md`). The Studio's training view lists these as profiles:

| Config | Purpose |
|---|---|
| `corpus_balanced_90m.yaml` | **Balanced · 91M** — the recommended main profile (longer context, ~90M params) |
| `corpus_starter_30m.yaml` | Smallest real corpus run, no activation checkpointing — fastest honest run |
| `corpus_experimental_163m.yaml` | Largest profile, for exploring what an RTX 5060 can do — only after a successful hardware probe |
| `corpus_small_8gb.yaml` | Earlier 8 GB VRAM corpus run (v04 dataset) |
| `corpus_smoke.yaml` | Quick corpus smoke test (v04 dataset) |
| `curriculum_tiny.yaml` | Tiny run on the curriculum dataset |
| `small_8gb.yaml` | Generic 8 GB VRAM training config |
| `smoke.yaml` | Generic quick smoke test |
| `tiny.yaml` | Tiny default training config |
| `tiny_demo.yaml` | Tiny demo run |
| `autotuned_night.example.yaml` | Template for the hardware autotuner's output. Real `autotuned_*.yaml` files are machine-local and gitignored |

## Training & Profiles

A run starts from a config and a starting point: random init or a compatible checkpoint. The important model fields are `max_seq_len`, `n_layers`, `d_model`, `n_heads`, `d_ff`, `dropout`, `rope_base`, `tie_embeddings` and `gradient_checkpointing`. The training fields that matter most day to day: `dtype`, `batch_size`, `gradient_accumulation_steps`, `learning_rate`, `warmup_steps`, `weight_decay`, the evaluation block (`eval_interval`, `eval_batches`, `evaluation_mode`) and the checkpoint block (`save_interval`, `keep_last_checkpoints`, `save_best_only`).

You must set at least one stopping criterion — `max_steps`, `max_tokens` or `target_dataset_passes` — and the one that ends first wins. The Studio refuses configs that would re-train the same tokens over and over (`max_dataset_passes_*` thresholds).

Before a real run, work through these in order:

1. **Hardware probe** — `python -m godot_coder.profile_probe` measures what fits on your GPU.
2. **Autotuner** — `python -m godot_coder.autotune` writes a measured config to `configs/autotuned_*.yaml`.
3. **Smoke** — a few-step run (`configs/smoke.yaml`) to prove the pipeline end to end.
4. **Preflight** — the Studio's training view shows a traffic light: red blocks the run, yellow needs a conscious decision, green means the mandatory checks passed.

Checkpoints land in the config's `output_dir` with `best` / `latest` plus step checkpoints, and every checkpoint carries fingerprints so an incompatible tokenizer or dataset is never silently mixed in.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e .
.\.venv\Scripts\python.exe -m godot_coder.doctor
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m godot_coder.studio
```

Then open `http://127.0.0.1:8765`.

## Workflow

1. **Open Studio** and select **Wissensaufbau** (Knowledge Building)
2. **Import sources** — curated catalog sources, or drop your own Godot projects/ZIPs into `data/local_sources/inbox/`
3. **Scan & validate** — each `.gd` file is parsed through Godot's GDScript parser; broken files are flagged
4. **Corpus audit** — dedup, token counting, split into train/val/test
5. **Tokenize** — build token streams for training
6. **Train** — configure and launch a run

The same pipeline as a CLI, in case you prefer a terminal:

```bash
python -m godot_coder.corpus --root . fetch                # download the selected sources
python -m godot_coder.corpus --root . build                # assemble the staging manifest
python -m godot_coder.corpus --root . validate             # Godot validation pass
python -m godot_coder.corpus --root . train-bpe --vocab-size 8192
python -m godot_coder.corpus --root . status               # where things stand
```

## Importing Your Own Projects

1. Open the private import folder from Studio (or use the toggles for big batches — see v0.10.2)
2. Drop ZIP files or project folders into `data/local_sources/inbox/`
3. Start the local import job — each project is scanned for secrets, validated, and admitted to the corpus

Your inbox is only ever read, never modified; a cleaned working copy is built separately.

## Directory Layout

```
data/corpus/downloads/    # per-source downloads (cleaned working copies)
data/local_sources/inbox/ # drop ZIPs/project folders here for the private import
data/raw/                 # raw curriculum/seed data
data/processed/           # tokenized datasets (corpus_v06 etc.)
data/tokenizer/           # tokenizer state
reports/                  # audits, job logs (reports/studio_jobs/), validation work
checkpoints/              # model checkpoints (per config output_dir)
artifacts/                # tokenizer files and similar outputs
.upgrade_backups/         # files replaced by the last upgrade (v0.10.2_<timestamp>)
```

Only runtime data is gitignored (`data/`, `reports/`, `checkpoints/`, `artifacts/`, backups, autotuned configs, build output). Code, docs, tests and configs are tracked — the repo stays clean because the generated stuff never enters it.

## Helper Scripts

No helper scripts are tracked in the repo — everything is a `python -m godot_coder.<module>` command (see below). Remote Tailscale setup runs through `remote_access configure | disable`.

## Command Line

Everything runs as `python -m godot_coder.<module> --help`. The ones you'll actually use:

- `studio` — the web UI (also installed as `godot-coder-studio`)
- `doctor` — environment check (Python, PyTorch, CUDA, Godot)
- `local_sources import --confirm-owned` — private inbox import
- `corpus fetch | build | validate | train-bpe | status` — corpus pipeline steps
- `validate_dataset --input <dir>` — full Godot validation pass over an assembled dataset
- `remote_access configure | disable` — Tailscale Serve setup
- `train`, `autotune`, `profile_probe`, `benchmark`, `evaluate`, `generate`, `prepare_data`, `curriculum`, `corpus_audit`, `scale_plan`, `instruction_data`, `validate_godot`

## Remote Studio (optional)

The Studio binds to `127.0.0.1:8765` by default. For phone/tablet access inside your Tailnet: run `python -m godot_coder.remote_access configure`, start the Studio, and open the printed Tailscale HTTPS address. `python -m godot_coder.remote_access disable` removes it again. Details in STUDIO.md.

## Documentation

- `STUDIO.md` — the Studio areas in detail
- `ROADMAP.md` — what's done and what's next
- `ARCHITECTURE.md` — pipeline and validation paths
- `docs/CONFIG_REFERENCE.md` — YAML config key reference
- `docs/INSTALL_v0.10.2.md` — install/upgrade guide
- `docs/CHANGELOG_v0.10.2.md` — patch notes
- `docs/PROGRESS_EVENT_SCHEMA_v1.md` — the progress event format
- `docs/AUDIT_v0.6.md` — the v0.6 training-core audit (historical record)
- `docs/INSTRUCTION_ROADMAP_v0.7.md` — the planned instruction/agent roadmap (historical draft)
- `docs/archive/` — superseded docs from v0.5–v0.7.9, each marked as archived

## Architecture

```
src/godot_coder/
├── local_sources.py    # Import pipeline: scan, validate, admit projects
├── corpus.py           # Corpus management, dedup, indexing
├── corpus_audit.py     # Audit and token counting
├── data.py             # Dataset/tokenizer preparation
├── train.py            # Training loop
├── model.py            # Model architecture
├── validate_dataset.py # Post-import validation pass
├── progress_events.py  # ETA estimation and progress events
├── studio.py           # FastAPI web server
├── process_control.py  # Managed subprocess handling (Windows job objects)
└── ui/static/          # Web UI (HTML/CSS/JS)
```

## Token Targets

- **5M unique corpus tokens** — first milestone
- **20M unique corpus tokens** — ambitious next goal
- This is not yet a general chat/agent model — instruction following requires additional curated data, response-masked fine-tuning, and verifier training

## Requirements

- Python ≥ 3.10
- Godot 4.x (for GDScript validation)
- CUDA-capable GPU (for training; CPU-only works for corpus work)

## License

This is a private/local training tool. Individual corpus sources retain their original licenses — always check before redistributing trained models. Local imports are marked `LicenseRef-User-Owned-Private` and are never redistributed.
