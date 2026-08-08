# Validation watchdog v0.7.4

> **Superseded / Archive.** The v0.7.4 watchdog was replaced by the
> managed-process runner with job objects and the error-rate abort
> (v0.10.x). The current validation flow is in `ARCHITECTURE.md`.

# Validation watchdog v0.7.4

## Flow

1. A cleaned, persistent corpus copy is created or reused when the fingerprint is identical.
2. A separate, short-lived validation workspace is created from it.
3. Godot runs with `--headless --xr-mode off --disable-crash-handler --path <workspace> --import`.
4. Output appears immediately in the technical log view.
5. The simple view receives a status heartbeat every five seconds.
6. When the time limit expires, Godot and its started subprocesses are terminated.
7. A one-time second run may use the existing import caches of the isolated copy.
8. The result and the complete technical log are saved.
9. The validation workspace is removed; a remaining lock is logged but does not block the corpus copy.

## Behavior on errors

- Timeout: the project is not enabled for training, but the import job completes in an orderly way and shows the last successful step.
- Parser error: the project stays quarantined; the parser output stays in the technical log and report.
- Studio crash: on the next import the saved PID is only terminated after a command-line check.
- Old v0.7.3 process: use `RECOVER_STUCK_VALIDATION.bat`.

## Privacy and data preservation

The original projects continue to be only read. The Godot check runs neither in the original folder nor in the persistent corpus copy. Private projects are not uploaded and not marked as redistributable.
