# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.23 (2026-08-11)

The chat finally streams tokens instead of dribbling out one finished
block, the training workspace shows the loss curve live while a run is
still going, and the deep-review backlog is paid down. Three review
rounds in one release: the five real findings from the first pass, the
remaining five from the second, and then the streaming feature itself
went through review twice before it was allowed to live.

### New

- **Real token streaming in the chat.** `model.py` gained a
  `generate_stream` generator that keeps the KV cache alive across the
  whole generation and yields every freshly sampled token; `generate` is
  now just its concatenation (token-identical, locked in by tests). The
  chat router runs the model on a worker thread so the forward pass never
  blocks the event loop, and streams SSE events: one `data:` frame per
  token delta, then a `done` event carrying the cleaned completion, then
  `[DONE]`. The UI shows a blinking caret while tokens arrive and swaps
  the raw stream for the cleaned text at the end. Verified live against
  `v06_v2/best.pt`, including the disconnect path: drop the connection
  mid-generation and the worker stops after the current token, the next
  request works immediately.
- **Live loss in the training workspace.** `train.py` emits a structured
  `train_loss` event after every optimizer step; the JobManager keeps a
  bounded history (400 samples) plus the current value in the job
  snapshot, so the existing `/api/jobs/current` poll shows it without a
  new endpoint. The professional-run card now shows the current loss and
  a small SVG sparkline of the last 60 samples.
- **`sampling.py` — one place for the sampling defaults.** The model-level
  defaults (temperature, top_k, top_p, repetition penalty) moved into a
  central module; `GenerateRequest` and the chat service read from it, so
  CLI, benchmark and Studio can't drift apart again.
- **`start_studio.bat` / `start_studio.ps1` work from a naked checkout.**
  Without `.venv` they fall back to the system Python with
  `PYTHONPATH=src` instead of failing, so a fresh clone starts the Studio
  immediately.

### Fixed

- **Scale plan used a static token estimate.** `tokens_per_step` in
  `GET /api/corpus/scale-plan` now comes from the autotune recommendation
  (measured on this machine) instead of the fixed batch-context formula.
- **`validate_code` reported fake durations.** It recorded a hardcoded
  30.0 seconds; the real wall time is measured and stored now.
- **Unbounded process output.** The `process_control` output queue is
  trimmed, so a chatty child can no longer grow memory without bound.
- **Job snapshots rewritten on every output line.** The snapshot file is
  now throttled to every 0.5 s while draining (the log/JSONL sidecars are
  per-line anyway); restart recovery reads only the snapshot, so nothing
  is lost.
- **`data/eval_cache/` accumulated one file per config tweak.**
  `fixed_evaluation_windows` now deletes stale variants with the same
  dataset fingerprint after writing a new cache (the `.tmp.` files are
  protected). One cache per fingerprint, not per experiment.
- **Preflight messages mixed German and English separators.** The
  `.replace(",", ".")` remnant turned "5,000,000" into "5.000.000" in the
  middle of English sentences; back to plain English formatting.
- **The service worker would have broken the installed chat.** `stream.js`
  was missing from the precache list — the new `app.js` needs
  `GodotCoderStream`, so the cache got bumped and the file added.
- **Snapshot payloads stay small on long runs.** The loss history is
  bounded at 400 samples and `events` trims at 5000; the UI reads the
  job snapshot, never the raw event list.

### Testable frontend

- The SSE parsing core of the chat (`consumeStreamChunk`) is a pure,
  DOM-free module in `stream.js` that `app.js` uses and a Node test
  exercises: chunk boundaries, multi-frame buffers, `[DONE]`, broken
  frames, and an unfinished final line. The asset test hooks also run
  `node --check` on the shipped JS and verify the script order in
  `index.html`.

### Tests

564 total: streaming token identity on both KV-cache paths, eos stop,
service event order, SSE endpoint, disconnect handling, loss-ring
population and bound, emitter round-trip, stream parser, eval-cache
cleanup, sampling defaults, preflight separators.
