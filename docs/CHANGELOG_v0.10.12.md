# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.12 (2026-08-09)

The deep code review finally paid off: the Windows job-object cleanup that I
trusted for crash-safe process kills never actually worked - and it explains
every frozen `prepare_data`/Godot child that held the output dir. That and a
few chat-quality fixes.

### Fixed

- **Windows job objects were completely non-functional.** The struct built in
  `_windows_job_create()` was 96 bytes, but Windows expects the 144-byte
  `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` (64-bit), so
  `SetInformationJobObject` failed with `ERROR_BAD_LENGTH` and the whole
  creation returned `None` - meaning `run_managed_process` silently fell back
  to `taskkill` for every run, and the "kills the whole tree even when the
  parent crashes" guarantee was a lie. Two defects, both fixed:
  1. The struct is now a real ctypes `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`
     (144 bytes, `LimitFlags` at offset 16, verified empirically), with
     `KILL_ON_JOB_CLOSE` in the right place.
  2. `_windows_job_assign()` used wrong access rights, so even a created job
     silently failed to claim the child process. It now requests
     `PROCESS_SET_QUOTA | PROCESS_TERMINATE | SYNCHRONIZE` and falls back to
     `taskkill` (which then also walks the tree) when the job path cannot
     finish the termination - so a failed assignment can never leave an
     orphan behind again. Job-creation failures now emit a one-time warning
     instead of failing silently.
- **Chat validation left orphans on timeout.** `validate_code` used
  `subprocess.run` with a 30s timeout; a hung Mono-Godot child survived the
  timeout as an orphan (hence the old `RECOVER_STUCK_VALIDATION.bat`
  workaround). It now uses `run_managed_process` like the corpus path, so the
  whole tree is cleaned up, and the response reports `timed_out` honestly.
- **The chat echoed your prompt back as "AI output".** `generate` decoded the
  whole sequence (prompt + completion), so every answer started with your own
  words and the model looked dumber than it is. It now returns only the
  completion, the same way `generate.py --suffix-only` does.
- **Warmup validation blocked valid resumes.** `TrainConfig.validate` required
  `warmup_steps < max_steps` without considering that a resumed run already
  past warmup is fine. Validation is now resume-aware (`start_step` is passed
  through `load_config`), and `train.py` peeks the checkpoint's step on CPU
  before validating so a resume past warmup is never rejected.
- **The memmap compatibility alias in `data.py` wasn't explicitly closed**
  (it waited for GC), which could hold the mmap file open on Windows a moment
  too long. It's closed deterministically now.
- **Dead `subprocess.TimeoutExpired` catch removed from the chat-validate
  endpoint** - `run_managed_process` returns `timed_out` instead of raising,
  so that clause (and the now-unused import) was misleading dead code.

### Changed

- **Empty completions look intentional now.** When the model hits its
  end-of-sequence token immediately, the chat shows a short hint ("usually
  means the checkpoint is undertrained for this prompt") instead of a blank
  code block that looked like a broken UI.

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to
  `0.10.12`. 228 tests total (41 new: job-object struct/rights mocks,
  managed-process validation, prompt-strip, resume-aware warmup, memmap
  close, plus 22 security-gap tests covering PIN/config hardening, CSRF
  mismatch, denial paths, path traversal and zip-slip extraction).
