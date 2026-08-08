# Roadmap

## Current stable scope: v0.10.6

- licensed Godot corpus (local + cataloged)
- BPE tokenizer
- three hardware/training profiles + hardware autotuner
- VRAM probe and completion reports
- local Studio with remote access (Tailscale, read/write protected)
- checkpoint, data and validator pipeline
- Fast Import Mode (`SKIP_PROJECT_IMPORT`, `FAST_STATIC`) with error-rate abort
- Studio toggles for the fast import (v0.10.2)
- secret scan guaranteed in every mode (v0.10.2 security fix)
- upgrade package buildable and applicable from `upgrade/` (v0.10.2)
- corpus validation via job-object runner (no more Mono hangs) + job stall watchdog (v0.10.3)
- no record is ever kept unverified: hard error list extended, line-only classification, strict-project checker, per-file verification of context warnings (v0.10.4/v0.10.5)
- preflight correctness: validator revision check, dataset-aware freshness, corpus-only token gate (v0.10.6)

## v0.10.1 - completed

- Fast Import Mode: `GODOT_CODER_SKIP_PROJECT_IMPORT` + `GODOT_CODER_FAST_STATIC`
- error-rate abort for hanging Godot imports with parser fallback
- ETA cache fix (no more "Calculating remaining time" flicker)
- Windows job objects in `process_control.py` (no orphaned process trees)
- encoding resilience, atomic staging, memmap cleanup, parser crash recovery,
  OOM emergency checkpoint, audit snapshots, UI search/filter

## v0.6.0 - completed

Professional Corpus Audit, document-aware shards, deterministic evaluation,
token budgets, early stopping, hardware autotuner, KV cache and Studio preflight.

## Only after a successful measurement phase

1. Improve data quality and transfer benchmark.
2. Add instruction, fill-in-the-middle and repair formats.
3. Connect local Godot documentation search.
4. Build limited patch/test/repair agents with preview and approval.

Instruction tuning and agents remain later milestones - only when the
base corpus is in place and the measurement phase is complete.
