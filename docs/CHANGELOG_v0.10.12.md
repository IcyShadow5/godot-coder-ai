# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.12 (2026-08-09)

The deep code review turned into two full days of fixes. The Windows
job-object cleanup that I trusted for crash-safe process kills never
actually worked. The validate_godot.py CLI was the last module still on
raw subprocess.run. And a methodical security audit found zero holes but
plenty of stuff that should have tests — so I wrote them.

### Fixed

- **Windows job objects were completely non-functional.** The struct built in
  `_windows_job_create()` was 96 bytes, but Windows expects the 144-byte
  `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` (64-bit), so
  `SetInformationJobObject` failed with `ERROR_BAD_LENGTH` and every
  managed run silently fell back to `taskkill`. Two defects: 1) the struct
  is now a real ctypes type with `LimitFlags` at offset 16, verified
  empirically. 2) `_windows_job_assign()` used wrong access rights, so even
  a created job failed to claim the child. It now requests
  `PROCESS_SET_QUOTA | PROCESS_TERMINATE | SYNCHRONIZE` and falls back to
  `taskkill` when the job path can't finish — a failed assignment can
  never leave an orphan behind.
- **validate_godot.py was the last module on raw subprocess.run.** Every
  other Godot call went through `run_managed_process` with full tree
  cleanup, but the standalone parser CLI didn't. A timed-out parse left
  orphaned Mono children. Fixed — it now uses the same managed runner with
  idle-timeout detection and the Job Object guarantee.
- **Chat validation left orphans on timeout.** Same bug, different module:
  `validate_code` in services.py used `subprocess.run` with a 30s timeout.
  Now uses `run_managed_process`.
- **classify_failure() missed parse errors when Godot exited 0.** Godot
  prints `SCRIPT ERROR: Parse Error:` to stderr but still exits 0
  sometimes. The classifier saw rc=0 and returned `NONE` without checking
  the output. Fixed — output is always scanned for failure markers first.
- **failure_kind was str, not the FailureKind enum.** `ManagedProcessResult`
  stored `failure_kind: str = ""` instead of `FailureKind.NONE`. Comparing
  `result.failure_kind == FailureKind.PARSE_ERROR` would silently fail
  because you can't compare a string to an enum. Fixed the type and the
  return statement.
- **The chat echoed your prompt back as "AI output".** `generate` decoded
  the whole sequence (prompt + completion). Now returns only the completion,
  same as `generate.py --suffix-only`.
- **Warmup validation blocked valid resumes.** `TrainConfig.validate` required
  `warmup_steps < max_steps` without considering that a resumed run already
  past warmup is fine. Now resume-aware.
- **The memmap compatibility alias wasn't explicitly closed** — waited for GC,
  which could hold the mmap file open on Windows. Closed deterministically.
- **Dead `subprocess.TimeoutExpired` catch in chat-validate** —
  `run_managed_process` returns `timed_out`, never raises. Removed.

### Added

- **FailureKind enum** — 11-type taxonomy (NONE, TIMEOUT, IDLE_TIMEOUT,
  STARTUP_ERROR, PARSE_ERROR, RUNTIME_ERROR, MONO_ERROR,
  PERMISSION_ERROR, ENVIRONMENT_ERROR, INFRASTRUCTURE_ERROR,
  UNKNOWN_ERROR) with Godot output pattern matching in
  `classify_failure()`. Every `ManagedProcessResult` gets one.
- **metrics.py** — structured observability module (`MetricEvent` enum,
  `MetricRecord` dataclass, `MetricsCollector` with thread-safe JSONL
  append). Tracks parse/runtime success, tool timeouts, retries, token
  usage. Not wired into train.py yet (TODO for v0.11), but the
  infrastructure is ready.
- **ValidationReport + PerFileResult** — typed dataclasses instead of bare
  dicts in `validate_dataset.py`. `to_dict()` for the JSON file, typed
  access everywhere else.
- **Dynamic user-agent** — `remote_sources.py` now uses
  `Godot-Coder-AI/{__version__}` instead of a hardcoded `0.7.5`.
- **SECURITY.md** — 10-point audit covering managed processes, path safety,
  command sandbox, secret isolation, network limits, prompt injection,
  permission escalation and release readiness.
- **CI security-gate job** — 85 security-critical tests run on every push
  (parallel with the cross-OS matrix), independently gatable in branch
  rules.
- **10 new security-gap tests** — `test_command_sandbox.py` (6 tests:
  safe_child vs shell metacharacters, absolute-path rejection, zero
  shell=True audit, no dynamic imports, remote write denial, server.py
  gate verification) + `test_corpus_content_safety.py` (4 tests: injection
  surface audit, tokenization path, secret scan, mask_secrets coverage).
- **6 validate_godot tests** — `test_validate_godot.py` covers missing
  Godot, missing project, missing script, managed-process usage, timeout
  handling and startup errors.
- **Deterministic cleanup test** — `test_validation_watchdog.py`'s
  descendant-cleanup test no longer polls with `time.sleep()` (CI flake
  risk). It mocks `process_is_alive` instead.

### Changed

- **All new files rewritten in my voice.** SECURITY.md, metrics.py,
  test_command_sandbox.py, test_corpus_content_safety.py, and the
  FailureKind/classify_failure docstrings all read like a security auditor
  or technical writer wrote them. Now they sound like me: direct, personal,
  practical.
- **STUDIO.md** — version bumped to v0.10.12, Data Lab description
  tightened, section headers renamed (\"Secure Remote Studio\" ->
  \"Remote Studio (Tailscale Serve)\", \"Beginner protection\" ->
  \"Guardrails\"), stiff sentences rewarmed.
- **Empty completions show a hint** instead of a blank code block.
- **classify_failure scans output once** (was duplicated after the rc=0 fix)
  and truncates to 10k chars so a huge Godot compilation log doesn't slow
  it down.

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to
  `0.10.12`. 244 tests total (52 new since v0.10.11: job-object
  struct/rights mocks, managed-process validate_godot, prompt-strip,
  resume-aware warmup, memmap close, FailureKind/classify_failure,
  metrics, 22 security-gap tests, 6 validate_godot tests, deterministic
  cleanup test).
