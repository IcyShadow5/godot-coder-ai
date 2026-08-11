# Changelog

## v0.10.13 (2026-08-09)

The deep review reached the training core. All the earlier passes were about
processes, validation, security - this one sat down with model.py and
train.py and read the forward pass and the loop line by line. It found four
real bugs, all in paths I trusted because the tests never exercised them.

### Fixed

- **Causal masking broke on chunked prefill.** The attention mask was
  enabled when there was no cached past and disabled when there was. That
  is right for single-token generation, but completely wrong when you feed
  a multi-token chunk after a warm start: the mask vanished and every
  position could attend to the future. The fix is simply
  `causal = sequence > 1` - SDPA already handles the single-token case
  without the extra condition. This affects batched generation and any
  chunked prefill path.
- **Sliding-window evaluation always ran with batch_size=1.** The eval
  mode buffered one window at a time and called the model on each, ignoring
  `config.batch_size` entirely. Evaluation was needlessly slow and, worse,
  the per-window loss variance read larger than it really was, which made
  the early-stop patience logic twitchier than intended. Windows now
  accumulate into real batches.
- **Tied embeddings got weight decay.** With `tie_embeddings` on, the
  shared `token_embedding.weight` doubles as the LM head, but the optimizer
  grouped it by `dim >= 2` and put it in the decay group anyway. Decaying
  an embedding that is also the head is a subtle, well-known way to kneecap
  a small model. It is now excluded, matching how tied-embedding models
  are meant to be trained.
- **Prefetch pinned memory on the main thread.** `BatchPrefetcher.next()`
  called `pin_memory()` synchronously, so every batch hand-off stalled the
  loop while the GPU transfer buffer was prepared. Pinning now happens
  inside the prefetch worker where the blocking belongs.

### Added

- **Module docstrings everywhere.** checkpoint, curriculum, generate,
  evaluate, model, config, tokenizer, prepare_data, instruction_data and
  godot_cli all opened with imports and nothing else. Each now starts with
  one line about what the module actually does.
- **Function/class docstrings filled in** where they were missing:
  autotune (4), data.TokenStream, and a few stragglers.
- **JS section headers** in api.js and remote.js so the files read like
  chapters instead of a wall of functions.
- **Inline comments on the model layers** - RMSNorm (why no mean centering),
  RoPE (what the rotation does), SwiGLU (half gate, half values),
  TransformerBlock (pre-norm + residual skips). Explain the why, not just
  the what.
- **Voice reword on the comments.** My first pass wrote textbook-style
  comments ("RMSNorm: cheaper than LayerNorm, no mean centering", "SwiGLU:
  better than ReLU/GeLU MLPs") - they read like a reference card, not like
  me. Rewrote them the way I actually talk: "RMSNorm: normalise over the
  last dimension only, skip the mean for speed", "SwiGLU: half the inner
  projection acts as a learnable gate, the other half as values",
  "Pre-norm transformer block: attention then MLP, each with a residual
  skip". Same for three module docstrings (checkpoint, tokenizer,
  evaluate) that had drifted into feature-list mode.

### Changed

- Nothing user-facing changed. This is a training-correctness + readability
  release.

### Version

- `__init__.py`, `pyproject.toml` and the service worker cache bumped to
  `0.10.13`. 251 tests stay green - these are fixes to existing behavior,
  no new tests required.
