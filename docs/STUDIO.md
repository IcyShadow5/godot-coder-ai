# Godot Coder Studio v0.10.15

The Studio continues to run locally on `127.0.0.1` and requires no cloud API. Optionally, Tailscale Serve provides a private HTTPS entry point within your own tailnet.

## Areas

- **Chat & Code:** Load a checkpoint, generate a completion, check it with Godot and save it as a learning example.
- **Training:** Pick a configuration, start from scratch or from a compatible checkpoint, live logs and stop.
- **Knowledge Building:** five guided steps from the Git repository to the token stream.
- **Data Lab:** read/edit GDScript; existing files are backed up first.
- **Models:** select `best`, `latest` and retained step checkpoints.
- **System:** check Python, PyTorch, CUDA/MPS, GPU, Godot and the project path. On Apple Silicon the GPU shows up via the MPS backend; without any GPU the panel falls back to CPU mode.

## Chat & Code

Select a checkpoint, enter a prompt and generate a GDScript completion. The output can be checked directly with Godot; confirmed examples can be saved as learning examples. When saving, the target file is backed up first.

## Data Lab

A small editor for the local corpus. Read and edit GDScript files, delete entries, create new ones. Filter by data type (all, active training tokens, your raw files, task data, train/val/test split) and search by name. Every edit backs up the original first.

## Knowledge Building: the guided steps

1. **Select sources** - the official Godot sources are preselected; they can be disabled, plus extension packages (small / large build · 5M / max build · 20M). Own Git sources can only be added with a permitted license.
2. **Fetch & clean sources** - `fetch` downloads the selected repositories, `build` creates the staging manifest (licenses, splits, deduplication).
3. **Check** - `validate` runs the code through the Godot parser; then the corpus audit with token counting follows.
4. **Token stream** - `train-bpe` builds the byte-BPE tokenizer and the versioned token stream.
5. **Prepare dataset** - the final training dataset with shards and hashes; then training can start.

For your own projects, use the toggles under "Import options for large volumes".

## Remote Studio (Tailscale Serve)

Four steps to access the Studio from your phone or tablet inside your tailnet:

1. Run `python -m godot_coder.remote_access configure` (sets up Tailscale Serve; the Studio stays bound to `127.0.0.1:8765`).
2. Start the Studio with `python -m godot_coder.studio`.
3. Open the displayed Tailscale HTTPS address on your phone/tablet.
4. Remove it again with `python -m godot_coder.remote_access disable`.

The remote area shows Tailscale status, identity, read mode, PIN session, private link downloads, ZIP uploads and the local import folder. All jobs run on the PC. API data is not cached offline by the PWA. I don't intend to expose this over Tailscale Funnel — it's your training machine, not a public service.

## Guardrails

Unknown licenses, bad Git URLs, paths outside the project, mismatched tokenizers and invalid training configs are all rejected before anything starts. Long-running jobs run separately and you can stop them any time.

## Import options for large volumes (v0.10.2)

Under **Knowledge Building -> Own Godot projects · private** there are now three
toggles that were previously only reachable as environment variables:

- **Skip Godot project import** (`GODOT_CODER_SKIP_PROJECT_IMPORT=1`) -
  checks each `.gd` file individually instead of a full `--import`. Significantly
  faster, but without project context; the full check runs later via
  `validate_dataset.py`.
- **Skip static AST analysis** (`GODOT_CODER_FAST_STATIC=1`) -
  skips only the slow warning analysis. The secret scan and the
  file size check still run (guaranteed since v0.10.2).
- **Tighten error abort** (`GODOT_CODER_ERROR_ABORT_THRESHOLD=60`) -
  aborts Godot after 60 instead of 500 error lines. Useful when an add-on
  drives the import into an infinite loop.

The toggles are passed to the import job as environment variables
(`extra_env`), so without a shell. The settings only apply to the respective
import run, not permanently.

## Professional Training Core

The training view contains a preflight traffic light. Red blocks the night run, yellow allows a deliberate review and green means mandatory checks passed. Advanced options stay collapsed.

## Parser fallback

When a stuck Godot Mono editor import (timeout, idle, error flood) occurs,
the Studio automatically switches to the per-file `--check-only` check. The
current path, file counter and excluded parser errors are shown in the
live progress. Since v0.10.1 the Windows job-object runner terminates the
complete process tree before the fallback starts - no orphans anymore.
Since v0.10.3 the corpus validation (`corpus validate`) also runs through this
runner: a hanging Mono import is terminated together with grandchild processes
instead of blocking forever on an inherited pipe. In addition, a
stall watchdog terminates every job that produces no output for longer than
`GODOT_CODER_JOB_STALL_TIMEOUT_SECONDS` (default 20 minutes, `0` disables it)
and marks it as failed in the UI.
