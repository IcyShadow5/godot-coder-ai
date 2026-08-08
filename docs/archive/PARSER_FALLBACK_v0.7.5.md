# Parser fallback v0.7.5

> **Superseded / Archive.** This document describes the v0.7.5 parser fallback.
> The mechanism lives on, but with new defaults and the
> Windows job-object runner. The current state is in `ARCHITECTURE.md`
> (validation paths), the environment variables in `README.md`.

# Parser fallback v0.7.5

## Diagnosis from the Pennyshire log

The static check had fully processed 85 GDScript files: 83 without findings and 2 with findings. Only afterwards Godot hung during the full Mono editor import.

The decisive messages were:

- `Plugin is not attached to debugger.`
- `Unable to start the timer because it's not inside the scene tree.`
- `GodotTools.HotReloadAssemblyWatcher.RestartTimer()`
- `EditorSettings not instantiated yet when getting setting "export/android/shutdown_adb_on_exit".`

After that no new technical output appeared. That was not a productive parser run.

## Behavior since v0.7.5

1. The full Godot import starts in an isolated working copy.
2. Known Mono/editor infrastructure errors immediately trigger a controlled abort.
3. Otherwise an inactivity watchdog applies when it stays silent.
4. Godot then checks every trainable `.gd` script with `--check-only`.
5. The Studio shows the current file path and `file x/y`.
6. Failed files are excluded individually.
7. If at least one trainable file remains and no secrets are present, the project stays enabled for training.

## Configuration

Defaults:

```text
GODOT_CODER_VALIDATION_TIMEOUT_SECONDS=420
GODOT_CODER_VALIDATION_IDLE_TIMEOUT_SECONDS=45
GODOT_CODER_PARSER_FILE_TIMEOUT_SECONDS=20
```

The values can be changed via environment variables. They should only be raised when technical logs still show real progress.
