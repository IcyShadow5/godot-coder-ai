# Changelog

## v0.10.24 (2026-08-11)

A quality release. No new headline feature this time - the chat gains its
Stop button and the "generation stopped" badge, chat history becomes
persistent sessions, the checkpoint survives graceful interrupts, and the
verify round from the deep reviews lands with a read-only falsification
mode. Plus the license is finally Apache-2.0 (with NOTICE for the
separately-released weights). 694 tests.

### New

- **Chat Stop button.** `POST /api/chat/stop` calls the generation
  service's `unload()`; the composer shows a Stop button while a
  generation runs (enabled only once the stream is live, so clicking
  during model load is a no-op instead of a silent cancel). `unload()`
  writes a cancel marker the active stream sees at its next token step,
  so the answer stops quickly and is marked with a **"generation
  stopped" badge** - live in the stream and in stored history - instead
  of looking like a complete (short) answer.
- **Persistent chat sessions.** The chat store keeps sessions with
  metadata (message counts, checkpoints); the session list in the Studio
  lets you switch and delete conversations. Turns survive a server
  restart.
- **Context provenance in the chat.** Before tokens arrive, the server
  sends a breakdown of where the prompt came from (tokens per source,
  what was trimmed) and the UI renders it as a collapsible panel.
  Truncation is head-preserving and there is a strict mode that raises
  instead of trimming.
- **Task checkpoint + resume state.** `train.py` writes a task checkpoint
  with resume state; an interrupt via the stop file is graceful, and the
  Studio shows a "resume peek" for interrupted runs.
- **Verify mode: read-only falsification checks.** `verify.py` grows a
  large read-only adversarial mode (mutation variants, parse-marker
  checks) exposed as a Studio job with its own UI.
- **Tokenizer: versioned builds.** Tokenizer builds carry a version so
  old checkpoints stay loadable when the tokenizer changes; `train_bpe`
  warns on checkpoint tokenizer drift.
- **Orphaned checkpoints** show up in the Studio overview and preflight.
- **`start_studio.bat` / `.ps1`** check dependencies before launch.
- **License: Apache-2.0** (MIT out), NOTICE for the separately-released
  weights.

### Fixed

- **Helper-file leak in corpus validation.** `validate_and_finalize`
  dropped one `reports/corpus_validation_helpers/project_check_<key>.gd`
  per run and never removed it; now it cleans the whole directory up
  front (before any early raise) and again in a `try/finally` around the
  project loop - gone on success, on a mid-loop exception, and on
  pre-loop failures (Godot missing, manifest missing).
- **Corpus `original_path` escape.** A corrupted manifest with `../` in
  `original_path` crashed validation with an unguarded `relative_to`
  ValueError; now guarded like the per-file path (record kept with a
  context warning).
- **Data catalog path traversal.** `_resolve_document_path` strips `.`
  and `..` segments and rejects absolute manifest paths, so a corrupt or
  hand-edited manifest can neither escape `data/` nor crash the catalog
  with a `relative_to` 500.
- **Secret leaks in logs.** `mask_secrets` now redacts a bare
  `-----BEGIN PRIVATE KEY-----` header (lines are masked one at a time)
  and generic `token=` assignments, not just the full block and
  `password`/`api_key`-style keys.
- **GET endpoints no longer write.** `list_configs` no longer rewrites
  the autotuned config on every listing; the metadata repair runs once
  at server startup (`create_app`).
- **`generate()` cancel semantics.** A cancelled non-stream generation
  returns its partial text with `cancelled=True` (the flag was already
  in the endpoint; now the non-stream path is locked in by a test).
- **Verify service cache + graceful checkpoint failures + retry
  cleanup.** The verify path caches services, handles failed checkpoints
  gracefully and cleans up retries.
- **Deep-review round:** seed dead parameter in `run_verification`,
  `check_leak` double counting, `work_dir` deletion on cleanup paths,
  `prune` numeric ordering, autotune `TypeError` guard, eval side-effect
  in `generate_stream`, generation-unload race - all fixed with
  regression tests.

### Tests

694 total: the deep-review regressions (traversal catalog, secret
masking, no-write config listing, startup normalization), chat stop
endpoint + cancelled persistence (stream and non-stream), chat session
store, context provenance, task checkpoint/resume, verify falsification
mode, tokenizer versioning + drift, orphaned checkpoints, start-script
dependency checks.
