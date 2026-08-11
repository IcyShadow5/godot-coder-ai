# Godot Coder AI

A local training studio for building a compact Godot/GDScript language model from scratch — train your own GPT-style model that actually understands GDScript. No cloud, no API keys, everything runs on your machine.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/IcyShadow5/godot-coder-ai/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://github.com/IcyShadow5/godot-coder-ai/blob/main/pyproject.toml)
[![Godot](https://img.shields.io/badge/Godot-4.x-purple.svg)](https://godotengine.org)
[![CI](https://github.com/IcyShadow5/godot-coder-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/IcyShadow5/godot-coder-ai/actions/workflows/ci.yml)

## What This Is

Three things bundled into one project:

1. **Corpus curation** — imports open-source Godot projects (or your own), scans for secrets, validates syntax through Godot's own parser, deduplicates, and assembles a clean training dataset. Every script is verified by Godot itself — via a full project import or a standalone per-file parse — so nothing is kept unverified.
2. **Training** — a from-scratch decoder-only transformer trained on your local GPU (PyTorch/CUDA).
3. **Studio (web UI)** — import projects, monitor training, inspect the corpus. Runs on `127.0.0.1:8765`.

I built this because I wanted a model that knows *my* GDScript style. That's why the whole pipeline is private-first: local projects get their own license entry (`LicenseRef-User-Owned-Private`), are never redistributed, and only you can enable them for training.

## First Results (so far)

Honest numbers from the first real training run:

- **Model:** 91M params, from scratch (12 layers, d=768, 12 heads, 8192-token BPE, 1024 context) — toy scale on purpose, built to prove the pipeline.
- **Data:** corpus_v06 — ~32M tokens of verified GDScript from ~830 imported Godot projects.
- **Run:** 2,460 of ~10,300 planned steps on an 8 GB RTX 5060, early-stopped at patience 4. Best validation loss **1.78** (val perplexity **5.96**).
- **The honest part:** a 91M model after roughly one pass over the data does *not* write working GDScript yet — parser pass rate is **6.25%** as a baseline. It *did* learn the training format, which proves the pipeline is real.
- **Golden Task Suite:** 30 hand-written GDScript challenges across 8 topics with reference solutions — the metric I track between runs.

## Quick Start

```powershell
python -m venv .venv
# PyTorch is not a pip dependency on purpose (CUDA builds are
# environment-specific). Pick your build at pytorch.org/get-started
.\\.venv\\Scripts\\pip install torch --index-url https://download.pytorch.org/whl/cu124
.\\.venv\\Scripts\\pip install -e .
.\\.venv\\Scripts\\python.exe -m godot_coder.doctor
.\\.venv\\Scripts\\python.exe -m pytest -q
.\\.venv\\Scripts\\python.exe -m godot_coder.studio
```

## Explore

- [**Studio**](STUDIO.md) — the Studio areas in detail
- [**Architecture**](ARCHITECTURE.md) — pipeline and validation paths
- [**Roadmap**](ROADMAP.md) — what's done and what's next
- [**Configuration Reference**](CONFIG_REFERENCE.md) — every YAML key explained
- [**Install Guide**](INSTALL_v0.10.16.md) — install/upgrade walkthrough
- [**Changelog**](CHANGELOG_v0.10.16.md) — the release notes

## License

The code is Apache-2.0-licensed. Corpus sources keep their original licenses — always check before redistributing trained models. Local imports are marked `LicenseRef-User-Owned-Private` and are never redistributed. **Trained model weights are released separately, under their own terms** — the code and the models are not the same license.
