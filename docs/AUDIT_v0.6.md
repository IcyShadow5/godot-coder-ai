# Audit v0.6 - Professional Training Core

> Historical document: This is the audit I wrote before the v0.6 rebuild.
> It describes the state of v0.5.2 and what v0.6 made of it.
> What has happened since then is in `docs/CHANGELOG_v0.10.3.md` - the audit itself
> is not updated continuously.

## Starting architecture v0.5.2

Godot Coder AI consisted of a decoder-only transformer with RoPE, RMSNorm,
SwiGLU, PyTorch SDPA, BF16/FP16 autocast, AdamW, gradient accumulation,
optional activation checkpointing, checkpoints, a FastAPI Studio interface
and a licensed Godot corpus pipeline.

## Identified risks

1. **Data format:** `train.bin` and `val.bin` were continuous token streams
   without a machine-readable document index. Random windows could cross
   document boundaries unnoticed.
2. **Evaluation:** Validation windows were drawn randomly again during every
   evaluation. Model comparisons were therefore not exactly reproducible.
3. **Training budget:** Profiles were primarily controlled by fixed step counts.
   With small corpora that led to extremely many
   dataset passes and overfitting.
4. **Early stopping:** There was no automatic termination after missing
   validation improvement.
5. **Corpus quality:** Exact content duplicates were removed, but
   normalized and near duplicates, split leaks and incomplete fragments
   were not reported as a separate audit.
6. **Cache:** Downloads were pinned, but Godot parser checks and fixed
   evaluation samples were not re-usable fingerprint-based.
7. **Hardware measurement:** Three fixed profiles were measured. Context variants,
   checkpointing on/off, batches 1/2/4/6/8 and optional `torch.compile`
   were not compared as a complete matrix.
8. **Generation:** Every new token recomputed the entire context;
   a KV cache was missing.
9. **Studio:** There was no central preflight traffic light with blocking reasons
   before a night run.
10. **User profiles:** A syntactically valid YAML file with the wrong
    root type could abort the entire profile listing.

## v0.6 migration

- Old v1 token streams and checkpoints remain readable.
- New datasets use format v2 with shards and document index.
- Existing downloads, `.venv`, CUDA/PyTorch, checkpoints and reports are
  not deleted.
- The new corpus audit writes to `data/corpus/audited`; the previous
  `prepared` remains as a fallback.
- New main profiles write to their own `checkpoints/v06_*` folders.

## Corpus pipeline v0.6

`Download -> Scan -> Godot validation with cache -> Professional Audit -> BPE ->
document-aware shards`

The audit captures source, commit, ref, SPDX license, attribution, content and
normalized hash, SimHash, project group, split, parser status and
quality status. Files with unknown license, parser errors, damaged
encoding, incomplete delimiters or split leakage are quarantined.

## Data format v2

Each split contains one or more `split-00000.bin` shards. `manifest.json`
lists per document path, shard, offset, global position, token count and hash.
The default `packed_with_file_sep` only allows cross-document windows
because every document is explicitly framed with BOS/EOS and `<file_sep>`.
The document index remains available for later strict document samples.

## Training v0.6

Supported are:

- `max_steps`
- `max_tokens`
- `target_dataset_passes`
- `validation_interval_tokens`
- fixed or sliding-window evaluation
- early stopping with patience and minimum improvement
- warning/block limits for excessive dataset passes
- `save_best_only`
- optional `torch.compile` with eager fallback
- CPU prefetch with pinned CUDA transfers

## Hardware

The autotuner tests 91M/1024, 91M/2048, 163M/1024 and 163M/2048, each with
checkpointing on/off, micro-batches 1/2/4/6/8 and in full mode eager
against `torch.compile`. Every attempt runs as an isolated process. Only
results under 90% reserved VRAM are recommended.

## Inference

An optional KV cache was added. The previous full recompute
remains as a fallback, especially when prompt plus output exceed the
configured context.

## Remaining limits

- The build machine has no RTX 5060 and no Godot-4.7 binary.
  CUDA autotuning and the real audit of the locally already downloaded
  sources must therefore run on the target machine.
- Near-duplicate detection is conservative and serves as a warning signal, not as
  automatic proof of identical content.
- Instruction tuning and agents remain explicitly outside of
  v0.6 - see `docs/INSTRUCTION_ROADMAP_v0.7.md` and `ROADMAP.md`.
