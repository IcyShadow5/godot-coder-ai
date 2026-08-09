# Roadmap

## Where things stand — v0.10.15

The pipeline is complete and measured: import → validate → tokenize →
train → generate → benchmark, all running locally. The list below is the
honest state and the order I plan to work in.

### Done (v0.10.1 → v0.10.15)

**Corpus & data**
- licensed Godot corpus (local + cataloged), registry fetch covering
  ~1,260 sources
- secret scan guaranteed in every import mode
- no record is ever kept unverified: hard error list, line-only
  classification, strict-project checker, per-file verification of
  context warnings
- zip-bomb + path-traversal guard on every archive path; fast import
  without temp-working-copy leaks

**Training**
- BPE tokenizer and a from-scratch decoder-only transformer
- hardware profiles + autotuner, VRAM probe
- crash-safe dataset swap, OOM emergency checkpoint, RNG-state resume,
  checkpoints load through torch's safe unpickler

**Studio & tooling**
- local web Studio with remote access (Tailscale, read/write protected)
- job-object process runner, stall watchdog, upgrade packages
- CI on Windows/Linux/macOS with a security gate, 263 tests
- published docs site (GitHub Pages)

### Measured so far

- first real run: 91M params, corpus_v06 (~32M tokens, ~830 projects),
  2,460 of ~10,300 steps, best val loss 1.78 (perplexity 5.96)
- benchmark: 6.25% parser pass rate on 16 prompts — a baseline, not a result
- Golden Task Suite: 30 hand-written GDScript tasks across 8 topics, the
  scoreboard I track between runs

## Next

1. **More data.** The registry covers ~1,260 sources, ~830 ready. Fetch,
   validate and re-tokenize before the next real run — more verified
   GDScript is the cheapest lever left.
2. **Finish a real run.** The 91M baseline stopped early at ~2,400 steps
   (patience-based early stop). Run it to completion for a real loss curve.
3. **Beat the baseline.** Golden Task Suite + heldout benchmark are the
   scoreboard — anything that doesn't move them doesn't count.

## After the baseline moves

4. **Instruction tuning** — turn "continues code" into "answers prompts".
   The instruction dataset builder already exists; wire it into a real
   training stage.
5. **Fill-in-the-middle and repair formats** — more training shapes for
   editor-style completion and error fixing.
6. **Local Godot documentation search** — let the model and Studio consult
   the actual Godot docs offline.

## Later (only once the above is measurable)

7. **Patch/test/repair agents** with preview and approval — no blind
   execution against user projects.
8. **Scale** — bigger context, longer training, a larger model only once
   the small one stops improving.

Per-version detail lives in the [changelog](CHANGELOG_v0.10.15.md).
