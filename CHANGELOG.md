## v0.10.16 (2026-08-10)

This is a maintainability release — no new behavior, but the code
that hurt to read is readable now. See `docs/CHANGELOG_v0.10.16.md`
for the full list.

339 tests.

## v0.10.15 (2026-08-10)

Second batch from the external review, all robustness. Fast Import Mode
leaked its temp working copy (144 leftovers found and cleaned); the GitHub
archive path got the same zip-bomb guard as local uploads; instruction data
now uses every function in a file, not just the first; the audit checkpoint
is actually read on resume; checkpoints load with `weights_only=True`; and
progress events can no longer be dropped by a 0 index. Plus the unused-import
cleanup (studio.py, ui/paths.py, golden-tasks test) and .gitignore gaps.
263 tests.


## v0.10.14 (2026-08-09)

An external review of the release zip came back with a short list. Two
findings were worth fixing properly: `prepare_dataset` could destroy a
working dataset if the process died mid-swap, and the `/proc` stat
parser misread process names with spaces, silently dropping them from
the descendant tree on timeout. Both fixed with regression tests.

The rest is cleanup: ten unused imports, the semicolon chains in
train.py (36 split, zero behavior change), LF line endings on 20 files
that still had CRLF, and `mask_secrets` now also redacts GitHub tokens,
JWTs and `user:password@` connection strings. The flaky watchdog test
was fixed too - it patched the module but called the name it had bound
at import time.

3 new tests, 254 total stay green.

## v0.10.13 (2026-08-09)

The deep review finally reached the training core. The earlier passes were
about processes, validation and security - this one read model.py and
train.py line by line and found four real bugs:

- **Causal masking broke on chunked prefill.** The mask was on when there
  was no cached past and off when there was - so feeding a multi-token
  chunk after a warm start attended to the future. Now `causal = sequence > 1`,
  which SDPA handles correctly in every case.
- **Sliding-window eval always ran with batch_size=1.** It ignored
  `config.batch_size`, so evaluation was needlessly slow and the loss
  variance looked bigger than it was. Windows now batch up properly.
- **Tied embeddings got weight decay.** With `tie_embeddings` on, the shared
  `token_embedding.weight` (which is also the LM head) sat in the decay
  group. Now excluded.
- **Prefetch pinned memory on the main thread.** `BatchPrefetcher.next()`
  called `pin_memory()` synchronously, stalling the loop on every batch.
  Moved into the prefetch worker.

Plus the documentation pass: module docstrings for all ten modules that had
none, function/class docstrings where they were missing (autotune, data,
checkpoint, ...), section headers in api.js/remote.js, and inline comments
on the model layers (RMSNorm, SwiGLU, RoPE, TransformerBlock). The model
comments got a second pass so they read like I wrote them - short and
practical, explaining the why ("skip the mean for speed") instead of
reciting textbook facts ("cheaper than LayerNorm").

No new tests - these are fixes to existing behavior. 251 tests stay green.

## v0.10.12 (2026-08-09)

Two full days from the deep code review. The Windows job-object cleanup
never actually worked (struct too small, so it failed silently), and the
validate_godot CLI was the last module still on raw subprocess.run:

- **Job objects finally work.** Correct 144-byte struct, correct assign
  rights, taskkill fallback when the job path can't finish.
- **Every Godot call now through managed processes** — validate_godot.py
  and chat-validate were the last two on raw subprocess.run.
- **FailureKind enum** — 11-type taxonomy with Godot output pattern
  matching. classify_failure() catches parse errors even when Godot
  exits 0.
- **metrics.py** — structured observability infrastructure (not wired into
  train.py yet, ready for v0.11).
- **ValidationReport + PerFileResult** — typed dataclasses replace bare
  dicts in validate_dataset.py.
- **SECURITY.md** — 10-point audit. CI security-gate job (85 tests on
  every push). 10 new security-gap tests (command sandbox + prompt
  injection). 6 validate_godot tests.
- **Dynamic user-agent** — no more hardcoded 0.7.5 in remote_sources.py.
- **No more prompt echo.** The chat returns only the completion.
- 52 new tests (244 total).

## v0.10.11 (2026-08-09)

Chat samples finally get verified, and the Studio UI is no longer one
2,000-line file:

- **User-lessons validation fix.** Failed generations saved from the chat
  were staged into the corpus but never parsed by Godot - the validator
  looked for them under downloads/<source_id>/ where they never live, so
  a broken sample could slip into training data. They now resolve against
  the project root and get the real per-file check; a syntax error is a
  hard exclusion.
- **UI module split.** app.js (1,961 lines) became api.js + remote.js +
  a slimmer app.js; index.html and the service worker load them in
  order. No behavior change.
- **README first results section** - honest numbers from the first real
  training run so progress is measurable.
- 3 new tests (2 user-lessons validation, 1 ingestion). 198 total.

## v0.10.10 (2026-08-09)

Training-start fix - no more silent crash on passes-driven configs:

- Starting training with `max_steps: null` (the autotuned and balanced
  profiles) crashed the train endpoint with an HTTP 500 - `int(None)` on
  an unhandled path. Null/absent `max_steps` now means "the run is driven
  by `target_dataset_passes`" and the request succeeds.
- 2 new regression tests for the train endpoint (null and explicit
  `max_steps`). 195 total.

## v0.10.9 (2026-08-09)

Docs voice pass plus smoother upgrade tooling - no product code changed
(193 tests stay green):

- Removed leftover translation artifacts ("resp." -> "and"/"or",
  "Comprehensible" -> "Human-readable", "in principle" -> "essentially",
  "afterwards ... follows" -> "then ... follows").
- Bumped stale version stamps in STUDIO/ARCHITECTURE/ROADMAP to v0.10.9
  (current stable scope).
- Upgrade template no longer prompts "Type JA" - the apply runs
  non-interactively, and a new .bat wrapper template ships out of the box.
- No product code changed; 193 tests stay green.

## v0.10.8 (2026-08-09)

AMD GPUs and a clean exit:

- ROCm (HIP) support: `rocm_available()` detection, an explicit
  `device: rocm` config request, "ROCm ready" in the Studio system panel,
  and a HIP build line in doctor/overview.
- prepare_data no longer prints the whole manifest (per-document metadata,
  ~10MB) to stdout - on a pipe nobody drains that print blocked forever and
  the finished job sat frozen with zero CPU. It prints a one-line summary
  and exits.
- 5 new tests (ROCm device selection + prepare_data clean-exit regression).
  193 total.

## v0.10.7 (2026-08-08)

Cross-platform release - the CI matrix caught real macOS bugs, so this one
makes "runs on your machine" true instead of assumed:

- The full test suite now runs on Ubuntu, Windows and macOS in CI (CPU
  torch, fail-fast off). It immediately caught a Linux-only test bug and
  two macOS-only process bugs.
- Apple Silicon (MPS) support: device auto-selection is now cuda -> mps ->
  cpu, the Studio panel reports MPS + unified memory, and four new tests
  cover it.
- Windows job-object tests were rewritten with plain-function kernel32
  fakes instead of os.name patching (the old patch silently tested the
  wrong function and crashed pytest's failure formatter on Linux).
- process_is_alive now detects zombies on macOS/BSD (no /proc there), so
  validation crash-recovery terminates reliably; command lines are read
  untruncated via `ps -ww -o args=`.
- CI actions bumped to v7 (kills the Node 20 deprecation warning).
- 188 tests total.

## v0.10.6 (2026-08-08)

Preflight correctness fixes, found while running the first real training runs:

- Preflight accepts the current project-aware validator revision (it was
  pinned to `project-aware-v2` while the pipeline writes `v4`).
- Freshness only gates corpus-derived streams; synthetic datasets compare
  against their own raw source.
- The token-minimum gate applies to corpus profiles only, so small synthetic
  runs pass full preflight.
- Five new preflight regression tests (183 total).
- Publish-ready housekeeping: pyproject metadata, CI workflow, community
  files (CONTRIBUTING, CODE_OF_CONDUCT, SECURITY), issue/PR templates,
  README badges.

## v0.10.5 (2026-08-08)

No record is ever kept unverified: context-warning scripts are also
parsed standalone per file with Godot `--check-only`. Validator cache bumped
to v4.

## v0.10.4 (2026-08-08)

Hard syntax errors can no longer slip through (marker list widened, line-only
classification), the per-file checker survives strict projects like gdUnit4,
and stale German catalog titles are healed.

## v0.10.3 (2026-08-08)

Corpus validation can no longer hang forever on large Mono projects (managed
process runner + Studio stall watchdog). `validate_dataset.py` import bug fixed.

## v0.10.2 (2026-08-08)

FAST_STATIC no longer skips the secret scan; Studio toggles for fast import;
`validate_dataset.py` honors the shared timeout; upgrade packages built from
templates in `upgrade/`.

## v0.10.1 (2026-08-07)

Fast import mode, error-rate abort for stuck Godot imports, ETA cache fix,
MIT license, initial upgrade tooling.
