# Godot Coder AI

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Godot](https://img.shields.io/badge/Godot-4.x-purple.svg)](https://godotengine.org)
[![CI](https://github.com/IcyShadow5/godot-coder-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/IcyShadow5/godot-coder-ai/actions/workflows/ci.yml)

A local training studio for building a compact Godot/GDScript language model from scratch — train your own GPT-style model that actually understands GDScript. No cloud, no API keys, everything runs on your machine.

## What This Is

Three things bundled into one project:

1. **Corpus curation** — imports open-source Godot projects (or your own), scans for secrets, validates syntax through Godot's own parser, deduplicates, and assembles a clean training dataset. Every script is verified by Godot itself — via a full project import or a standalone per-file parse — so nothing is kept unverified.
2. **Training** — a from-scratch decoder-only transformer trained on your local GPU (PyTorch/CUDA).
3. **Studio (web UI)** — import projects, monitor training, inspect the corpus. Runs on `127.0.0.1:8765`.

I built this because I wanted a model that knows *my* GDScript style. That's why the whole pipeline is private-first: local projects get their own license entry (`LicenseRef-User-Owned-Private`), are never redistributed, and only you can enable them for training.


## First Results (so far)

Honest numbers from the first real training run, so I can measure progress instead of guessing:

- **Model:** 91M params, from scratch (12 layers, d=768, 12 heads, 8192-token BPE, 1024 context) — toy scale on purpose, built to prove the pipeline.
- **Data:** corpus_v06 — ~32M tokens of verified GDScript from ~830 imported Godot projects.
- **Run:** 2,460 of ~10,300 planned steps on a 8 GB RTX 5060 (~22 min, bf16, CUDA), then early-stopped at patience 4. Best validation loss **1.78** (val perplexity **5.96**); training loss fell from 7.33 to ~1.5 over the run.
- **The honest part:** a 91M model after roughly one pass over the data does *not* write working GDScript yet. The benchmark that checks whether generated completions parse as valid GDScript scores **6.25%** (1 of 16 prompts) — a baseline, not a result. What it *did* learn is telling: it reproduces the exact task-header format of the training data, which means the pipeline, tokenizer and training loop are real.
- **What this run bought:** an end-to-end proof (import → validate → tokenize → train → generate → benchmark) plus a measurable baseline to beat. The levers from here are more data (the registry fetch now covers ~1,260 sources, ~830 of them ready), more training time, and finally the instruction-tuning stage that turns "continues code" into "answers prompts".

## Recent Changes

I keep the full history in `CHANGELOG.md` (per-version detail in
`docs/CHANGELOG_v0.10.x.md`), so here is just the short version - the last
few releases in one breath:

- **v0.10.12** - maintenance from the deep code review. The Windows
  job-object cleanup never actually engaged (the struct was too small, so
  creation failed silently and every managed run fell back to taskkill) - it
  now works, which finally explains the frozen orphaned processes. Chat
  validation cleans up its tree, the chat no longer echoes your prompt back
  as "AI output", empty completions show a hint, and warmup validation is
  resume-aware. 41 new tests (228 total) after the security-gap review pass.
- **v0.10.11** - chat samples actually get verified. Failed generations
  saved from the Studio chat were staged into the corpus but never parsed
  by Godot (the validator looked in the wrong folder), so a broken sample
  could slip into training data; they now resolve correctly and syntax
  errors are hard exclusions. The Studio UI was also split into modules
  (api.js, remote.js, app.js) and the README gained a first-results
  section. 3 new tests (198 total).
- **v0.10.10** - training-start fix. Profiles driven by
  `target_dataset_passes` (autotuned, balanced) set `max_steps: null`;
  the train endpoint crashed on `int(None)` with an HTTP 500, so "Start
  training" failed silently. Null/absent `max_steps` is now treated as a
  passes-driven run and the job starts. 2 regression tests added
  (195 total).
- **v0.10.9** - docs voice pass + smoother upgrades. A review pass removed
  leftover translation artifacts ("resp.", "Comprehensible", "in principle")
  and bumped stale version stamps; the upgrade template now applies
  non-interactively (no "Type JA" prompt) and ships a .bat wrapper template.
  No product code changed.
- **v0.10.8** - AMD GPUs and a clean exit. ROCm (HIP) builds are detected and
  reported ("ROCm ready" in the Studio, `device: rocm` in configs), and
  prepare_data stops printing the 10MB manifest to stdout - the reason a
  finished tokenization job used to sit frozen instead of exiting.
- **v0.10.7** - runs everywhere. The full test suite now runs on Windows, Linux
  and macOS in CI, Apple Silicon gets real MPS acceleration instead of a
  silent CPU fallback, and macOS/BSD process handling was hardened (zombie
  detection, untruncated command lines).
- **v0.10.6** - preflight correctness. The validator-version check stopped
  being hardcoded, the freshness check only judges what a dataset actually
  depends on, and token minimums apply to corpus profiles only. Also made
  the repo publish-ready (CI, community files).
- **v0.10.5** - no record is ever kept unverified: scripts that could only
  get a context warning inside their project now also get a standalone
  per-file Godot parse.
- **v0.10.4** - hard syntax errors can't slip through the classifier
  anymore, and the per-file checker survives strict projects like gdUnit4.
- **v0.10.3** - corpus validation can no longer hang forever on large Mono
  projects (managed processes + a Studio stall watchdog).
- **v0.10.2** - fast mode no longer skips the secret scan, and the Studio
  got toggles for the fast-import flags.
- **v0.10.1** - fast import mode and error-rate abort: ~4s per project
  instead of 13-37s, and stuck Godot imports fall back to the per-file
  parser.

The fast-import flags and their recommended combos are in
[Key Environment Variables](#key-environment-variables) below.

## Key Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `GODOT_CODER_SKIP_PROJECT_IMPORT` | unset | Skip Godot `--import`, use the per-file parser directly. Authoritative — works even with `FAST_STATIC=1` |
| `GODOT_CODER_FAST_STATIC` | unset | Skip only the slow AST warning walk. Secret scan + file-size checks still run |
| `GODOT_CODER_ERROR_ABORT_THRESHOLD` | 500 | Consecutive error lines before aborting a stuck Godot import |
| `GODOT_CODER_JOB_STALL_TIMEOUT_SECONDS` | 1200 | Seconds a Studio job may be silent before its process tree is killed and the job is marked failed (0 disables) |
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
..\.venv\Scripts\python.exe -m godot_coder.studio

# Fastest without skipping Godot: keep full project validation, skip the AST walk
$env:GODOT_CODER_FAST_STATIC = "1"
..\.venv\Scripts\python.exe -m godot_coder.studio

# Stuck addon projects? Abort earlier (the Studio toggle uses 60)
$env:GODOT_CODER_ERROR_ABORT_THRESHOLD = "60"
..\.venv\Scripts\python.exe -m godot_coder.studio
```

When you skip the project import, full validation is deferred: `validate_dataset.py` runs a complete Godot pass over the assembled dataset (same env-var timeout, managed process runner). For maximum thoroughness during import, leave the env vars off or use only `FAST_STATIC=1`. Either way nothing is kept unverified: since v0.10.5, any script that could not be positively checked inside its project (a `context_warning`) is additionally parsed standalone per file.

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
# PyTorch is not a pip dependency on purpose (CUDA builds are
# environment-specific). Pick your build at pytorch.org/get-started
# and install it into the venv, e.g. for CUDA 12.x:
.\.venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu124
.\.venv\Scripts\pip install -e .
.\.venv\Scripts\python.exe -m godot_coder.doctor
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m godot_coder.studio
```

## Workflow

1. **Open Studio** and select **Knowledge Building**
2. **Import sources** — curated catalog sources, or drop your own Godot projects/ZIPs into `data/local_sources/inbox/`
3. **Scan & validate** — each `.gd` file is verified by Godot: a full project import, or a standalone `--check-only` parse for scripts that could not be checked in project context. Unambiguous syntax errors and incompatible Godot-3 projects are hard-excluded; everything else is admitted
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

The repo tracks a few small Windows launchers (`start_studio.bat`, `start_studio.ps1`, `CHECK_INSTALLATION.bat`, …) for convenience; everything else runs as a `python -m godot_coder.<module>` command (see below). Remote Tailscale setup runs through `remote_access configure | disable`.

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
- `docs/INSTALL_v0.10.12.md` — install/upgrade guide (latest)
- `docs/CHANGELOG_v0.10.12.md` — patch notes (latest; earlier `CHANGELOG_v0.10.x.md` files stay for history)
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
- PyTorch for training — pick the build that matches your machine (see
  [Platforms & GPUs](#platforms--gpus))
- A GPU is recommended for training; a CPU-only build works fine for
  corpus work

## Platforms & GPUs

The code runs on Windows, Linux and macOS, and the test suite is exercised
on all three in CI (the full test suite, CPU build). The training device is
picked automatically at run time (`device: auto` in the configs):

| Hardware | Device | Notes |
|---|---|---|
| NVIDIA GPU | `cuda` | fp16/bf16 mixed precision, the primary path |
| AMD GPU (ROCm build) | `cuda` | ROCm exposes AMD GPUs as `cuda` devices; `device: rocm` maps onto it |
| Apple Silicon (M-series) | `mps` | picked automatically when no CUDA is present |
| Anything else | `cpu` | slow but works; corpus work is fine on CPU |

You can also force a device with `device: cuda`, `device: rocm`,
`device: mps` or `device: cpu` in a config — the preflight and trainer will
refuse to start if the requested device is not actually available.

**Why there is no GPU in CI:** GitHub-hosted runners have no GPU, so
continuous integration runs the full suite on CPU builds only. GPU
verification stays local — that is exactly what the three-step hardware
gate is for:

1. `python -m godot_coder.doctor` — reports the torch build, CUDA/MPS
   availability and runs a real tensor smoke test on the GPU
2. `python -m godot_coder.profile_probe` — measures what actually fits on
   your GPU (VRAM, context length, batch)
3. `python -m godot_coder.autotune` — writes a measured
   `configs/autotuned_*.yaml` from the probe results

The Studio surfaces the same information on the system panel (a green
"CUDA ready" / "ROCm ready" / "MPS ready" label, or CPU mode when none
of the backends is present).

## License

The code is MIT-licensed (see `LICENSE`). Two things stay separate from the code:

- **Corpus sources** keep their original licenses — always check before redistributing trained models. Local imports are marked `LicenseRef-User-Owned-Private` and are never redistributed.
- **Trained model weights** are released separately, under their own terms — the code and the models are not the same license.

If you train a model on your own projects, it is yours. If you train on third-party sources, the source licenses decide what you may share.
