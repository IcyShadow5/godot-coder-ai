# Changelog

Patch notes, so I still know later what I was thinking.

## v0.10.17 (2026-08-10)

The Triton release. This one adds real `torch.compile` support (the one
speedup that actually shows up on consumer GPUs), hardens the autotuner
against a corner case it would have gotten wrong, and fixes a benchmark
harness bug that was silently grading the model too harshly.

### New

- **Optional `torch.compile` support.** The trainer and autotuner always
  probed for `torch.compile`, but on Windows it could never work because
  Triton ships no official Windows wheels - so the probe always fell back
  to eager mode. There's a community `triton-windows` wheel now, and the
  new `compile` extra wires it up correctly per platform:
  `pip install -e ".[compile]"`. Linux gets plain `triton>=3.7`, Windows
  gets `triton-windows>=3.7,<3.8`. Nothing changes if you don't install
  it - the probe just reports `compile_available: false` as before.

### Fixed

- **Autotune misread compile failures.** When a compile probe failed (for
  example because Triton was missing), the real worker keeps running in
  eager mode and reports `status: pass` with `compile_enabled: false` and
  a `compile_error`. The autotuner only treated `status: error` as a
  failure, so it would have kept firing off the remaining ~39 compile
  probes pointlessly and then reported `compile_available: true` - the
  opposite of the truth. It now treats any of `status: error`,
  `compile_enabled: false` or a `compile_error` on a compile-requested
  probe as a compile failure, skips the rest of the compile variants and
  reports `compile_available: false` with the reason. Regression-tested
  against a test double that mirrors the real worker's behavior.
- **Benchmark graded bare completions.** `generate()` returns only the
  completion (the chat flow strips the prompt), but the benchmark fed
  that bare snippet straight to Godot validation - a completion without
  its scaffold is not parseable, so the whole golden suite showed 0 %.
  Validation now gets prompt + completion (the scaffold re-attached),
  which is what the model actually saw. The real baseline for
  `v06_balanced/best.pt` is 2/30 (6.7 %) parser pass with 9.4 % average
  token-prefix accuracy - humble, but it's a true number now, and the
  run is deterministic (temperature 0) so the measurement is reproducible.

### Tests

- **6 new tests (345 total).** `test_autotune.py` covers the compile
  failure paths (`_mark_safety`, scoring, the recommendation selection,
  and the skip-the-rest behavior with a realistic worker double that
  fails exactly like the real one - `status: pass` + `compile_error`).
  `test_benchmark.py` regresses the scaffold re-attach so the harness
  can never silently regress to grading bare completions again.

### Chores

- **`.coderabbit.yaml` added.** CodeRabbit is enabled on this repo, and
  the config teaches it the project's conventions: path-specific
  instructions for Python code, the UI routers, tests, configs and docs,
  plus ignore patterns for generated artifacts (`data/`, `checkpoints/`,
  `*.pt`, `*.bin`, the upgrade payloads and zip assets). Review profile
  is `chill` - enough signal, not a wall of comments.

345 tests.
