# Godot Coder AI v0.10.1

A local training studio for building a compact Godot/GDScript language model from scratch — train your own GPT-style model that understands GDScript code.

## What This Is

Godot Coder AI is a Python application that:
1. **Curates a GDScript corpus** — downloads open-source Godot projects, scans for secrets, validates syntax via Godot's parser, deduplicates, and assembles a clean training dataset
2. **Trains a small language model** — from-scratch transformer training on your local GPU (PyTorch/CUDA)
3. **Provides a web UI** (Studio) — manage imports, monitor training, inspect the corpus

## v0.10.1 Improvements

This release includes significant performance and reliability fixes:

### 🚀 Fast Import Mode
- **`GODOT_CODER_SKIP_PROJECT_IMPORT=1`** — Skips Godot `--import` entirely during validation. Instead validates each `.gd` file individually via Godot's per-file parser. **Massive speedup**: ~4s/project vs 13-37s/project. For 800+ projects this cuts import from 5-7 hours to under 1 hour.
- **`GODOT_CODER_FAST_STATIC=1`** — Skips redundant per-file `_static_warnings()` analysis and byte counting during import. The static analysis phases (secret scan, file size check, dedup) still run; only the slow per-file AST walk is skipped.

### 🔴 Error-Rate Abort
Godot's `--import` can get stuck on broken addon projects, producing thousands of error lines without progress. Previously, the `idle_timeout` never triggered because errors = output. Now:
- Tracks consecutive error lines via regex (`ERROR:`, `SCRIPT ERROR:`, `Parse Error:`, `failed to load`, `invalid UID`, etc.)
- Resets counter on progress markers (`[ XX% ]`, `first_scan_filesystem`)
- Default threshold: **500 consecutive errors** (configurable via `GODOT_CODER_ERROR_ABORT_THRESHOLD`)
- On abort: kills Godot process, falls back to safe per-file parser

### 🐛 Bug Fixes
- **Per-file loop double-execution** — The `_static_warnings()` loop ran unconditionally even with `FAST_STATIC=1`, effectively processing every file twice (once in the "skipped" path and once in the main loop). Fixed by properly guarding with `if not fast_static:`.
- **ETA cache flickering** — The ETA estimator now preserves `_last_estimate` between projects when `remaining_files` hits zero, preventing the UI from flashing "Restzeit wird berechnet" (calculating remaining time).

### 🧪 New Tests
- `test_eta_preserves_last_estimate_on_zero_remaining` — verifies ETA cache fallback
- `test_error_rate_abort_patterns` — validates regex patterns match known Godot error formats without false-positives on progress lines

## Key Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `GODOT_CODER_SKIP_PROJECT_IMPORT` | unset | Skip Godot `--import`, use per-file parser directly |
| `GODOT_CODER_FAST_STATIC` | unset | Skip per-file AST warnings during import |
| `GODOT_CODER_ERROR_ABORT_THRESHOLD` | 500 | Consecutive error lines before aborting Godot |
| `GODOT_CODER_VALIDATION_TIMEOUT_SECONDS` | 120 | Max time for Godot `--import` per project |
| `GODOT_CODER_VALIDATION_IDLE_TIMEOUT_SECONDS` | 30 | Idle timeout (no output) before aborting Godot |

### Recommended for Large Imports

```powershell
# Fastest import — skips Godot import entirely, validates per-file
$env:GODOT_CODER_SKIP_PROJECT_IMPORT = "1"
.\.venv\Scripts\python.exe -m godot_coder.studio
```

Validation is deferred to training time via `validate_dataset.py`, which runs a full Godot pass on the assembled dataset. For maximum thoroughness during import, omit the env vars or use only `FAST_STATIC=1`.

## Quick Start

```powershell
# Install
python -m venv .venv
.\.venv\Scripts\pip install -e .

# Verify installation
.\.venv\Scripts\python.exe -m godot_coder.doctor
.\.venv\Scripts\python.exe -m pytest -q

# Launch Studio
.\.venv\Scripts\python.exe -m godot_coder.studio
```

Then open `http://127.0.0.1:8765` in your browser.

## Workflow

1. **Open Studio** and select **Wissensaufbau** (Knowledge Building)
2. **Import sources** — use curated catalog sources or add your own Godot projects as ZIPs to `data/local_sources/inbox/`
3. **Scan & validate** — each `.gd` file is parsed through Godot's GDScript parser; broken files are flagged
4. **Corpus audit** — deduplication, token counting, split into train/val/test
5. **Tokenize** — build token streams for training
6. **Train** — configure and launch a training run

## Importing Your Own Projects

1. Open the private import folder from Studio
2. Drop ZIP files or project folders into `data/local_sources/inbox/`
3. Start the local import job — each project is scanned for secrets, validated, and admitted to the corpus

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
├── process_control.py  # Managed subprocess handling
└── ui/static/          # Web UI (HTML/CSS/JS)
```

## Token Targets

- **5M unique corpus tokens** — first milestone
- **20M unique corpus tokens** — ambitious next goal
- This is not yet a general chat/agent model — instruction following requires additional curated data, response-masked fine-tuning, and verifier training

## Requirements

- Python ≥ 3.10
- Godot 4.x (for GDScript validation)
- CUDA-capable GPU (for training; CPU-only for corpus work)

## License

This is a private/local training tool. Individual corpus sources retain their original licenses — always check before redistributing trained models.
