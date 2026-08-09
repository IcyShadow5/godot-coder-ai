# Changelog

Patch notes, so I still know later what I was thinking. Detailed notes for
each version live in `docs/CHANGELOG_v0.10.x.md`; this file is the quick tour.

## v0.10.12 (2026-08-09)

Maintenance release from the deep code review - the Windows job-object
cleanup never actually engaged (struct too small, so it failed silently
and every managed run fell back to taskkill):

- **Job objects finally work.** Correct 144-byte struct with `LimitFlags`
  at offset 16, correct assign rights, and a taskkill fallback when the job
  path can't finish a termination. This explains the frozen orphaned
  processes (stale `prepare_data` holding the output dir).
- **Chat validation cleans up its tree** - `validate_code` now uses the
  managed process runner, so a hung Mono-Godot child can't survive the
  timeout as an orphan.
- **No more prompt echo.** The chat returns only the completion, not your
  prompt pasted back as "AI output". Empty completions show a hint instead
  of a blank code block.
- **Warmup validation is resume-aware** - resuming past warmup is no longer
  blocked by the fresh-run check. `data.py` closes its memmap alias
  explicitly.
- 19 new tests (206 total).

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

No record is ever kept unverified: context-warning scripts are additionally
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
