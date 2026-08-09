# Architecture v0.10.9

```text
Licensed Godot sources
  -> Git ref + commit + license manifest
  -> filtering / deduplication / repository-wise splits
  -> Godot validation
  -> byte-BPE tokenizer
  -> versioned token streams with hashes
  -> decoder-only transformer
  -> training + validation + checkpoints
  -> verified token/VRAM/timing reports
  -> generation -> Godot check
```

## Layer separation

- `corpus.py`, `data.py`, `tokenizer.py`: reproducible data pipeline.
- `model.py`, `train.py`, `checkpoint.py`: model and training.
- `generate.py`, `evaluate.py`, `benchmark.py`: inference and measurement.
- `ui/`: local interface; it uses the same core and has no second model logic.
- `jobs.py`: isolated, stoppable processes for training, Git and hardware probes.

Tokenizer or architecture changes require new weights, but no rebuild of the
Studio. Checkpoints store fingerprints so incompatible data/tokenizers are not
silently mixed.

## Validation paths (local_sources.py)

There are two Godot check modes that switch depending on environment and error
situation:

- **`project_import`** - full headless `--import` in an isolated working
  copy. It is terminated in a controlled way by timeout, idle timeout or the
  error-rate abort (>500 error lines without progress).
- **`gdscript_check`** - per-file `--check-only` parser check. Used as
  parser fallback after an aborted import and is the mode with
  `GODOT_CODER_SKIP_PROJECT_IMPORT=1`. Statically unremarkable scripts are
  skipped, only warning-suspect ones are checked individually.

`validate_dataset.py` is the CLI re-check pass for the finished dataset: same
per-file parser check, same timeout from `GODOT_CODER_PARSER_FILE_TIMEOUT_SECONDS`,
same managed-process runner with job-object cleanup.

## Fast-static (GODOT_CODER_FAST_STATIC=1)

Skips exclusively the slow AST warning analysis
(`_static_warnings()`). The secret scan, the file size check, the
byte/line counting and the deduplication run in every mode - a
security and correctness promise since v0.10.2.

The Godot project validation (`corpus validate`) runs since v0.10.3 via the
managed-process runner (`process_control.run_managed_process`, Windows
job objects), which terminates the complete process tree on timeouts - Mono Godot
grandchild processes that inherit the stdout/stderr pipes can no longer cause
deadlocks or orphans. The Studio JobManager also terminates jobs that
have been silent for longer than `GODOT_CODER_JOB_STALL_TIMEOUT_SECONDS`
(default 1200s).

Since v0.10.4 the classification is strict: only the error line itself
decides hard vs. context (a benign context error no longer demotes a real
parse error), and the hard-error list covers unambiguous syntax errors beyond
`expected ...` phrasing. Since v0.10.5 every context-warning record is
also parsed standalone with `--check-only` - no record is kept
unverified. Decisions are cached per validator version
(`godot_project_validation_v4.json`), so a classifier change forces a clean
re-check.

## v0.6 Professional Training Core

`licensed sources -> cached validation -> corpus audit/quarantine -> audited documents -> BPE -> sharded token dataset -> deterministic evaluation -> token-budgeted training -> best checkpoint -> cached inference`
