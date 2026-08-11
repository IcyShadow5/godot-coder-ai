# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.22 (2026-08-11)

The chat finally stops looping. The garbage completions were not just an
undertrained model - the sampler had no repetition penalty at all, so once
the model repeated a token it happily filled the whole token budget with the
same line. This release fixes the sampling side of chat quality without any
retraining, and the golden benchmark agrees: parser pass rate went from
2/30 (6.7 %) to 6/30 (20 %) with the new chat defaults.

### New

- **Repetition penalty.** `model.py` applies an HF-style penalty to freshly
  generated tokens only - never the prompt. The Studio chat defaults to
  1.15, the UI got a slider (1.0-2.0, step 0.05), and the benchmark and CLI
  expose `--repetition-penalty`.
- **Nucleus sampling (`top_p`).** Optional `top_p` in `_sample`, off by
  default (1.0), wired through the same paths as the penalty.
- **Completion cleanup.** `_clean_completion` normalizes blank-line runs and
  `_collapse_repeated_blocks` cuts completions at the first repeated block.
  Multi-line loops collapse; single lines only when clearly looping
  (comments at 2 repeats, code lines at 3), so a legitimate identical pair
  survives.
- **Benchmark + CLI flags.** `benchmark.py` and `generate.py` accept
  `--top-p` / `--repetition-penalty`. The chat router passes both through on
  the streaming and non-streaming paths - the streaming route was the code
  review find, without it the slider would have been dead in the UI.

### Fixed

- **Checkpoint resume on GPU always crashed.** `restore_rng_state` handed
  the CUDA-placed RNG tensors straight to `torch.set_rng_state`, which only
  accepts CPU ByteTensors. Two hidden bugs in a row (CPU and CUDA state)
  surfaced while continuing a training run; both are fixed by coercing back
  to CPU, with two regression tests. Resume on GPU had simply never been
  exercised before, so this one was waiting for a while.

### Measured

- Golden Task Suite on `v06_balanced/best.pt`, temperature 0:
  **6/30 (20 %)** with repetition penalty 1.15 vs **2/30 (6.7 %)** before.
  Cleanup alone changed nothing (6.7 %); the penalty is what moved the
  number.
- A continue-training experiment (3000 steps, ~8 dataset passes) improved
  validation loss but *hurt* the golden benchmark (10 %) - textbook
  overfitting on the same corpus. The real levers are more/better data, not
  more passes over the same tokens.

### Tests

483 total (+16): penalty math, top_p masking, loop-break in both decode
paths, cleanup thresholds, schema defaults, router pass-through.
