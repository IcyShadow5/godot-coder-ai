# Contributing

Thanks for stopping by! This project started as a personal tool and grew
into something shareable, so here is how to help without tripping over the
setup.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q
```

That is everything for the test suite - no Godot install required. The tests
mock the Godot binary, so they run fast and anywhere.

## What actually needs Godot

Only the real pipeline steps need a Godot 4.x binary: importing sources
(`corpus validate`), the per-file parser, and `validate_dataset.py`. Those
are never exercised by the unit tests.

## Test suite

- `python -m pytest -q` runs the full suite (250+ tests, all mocked).
- `node tests/js_progress_test.cjs` checks the front-end progress logic.

If you add a feature, add a test that does not need Godot. If it genuinely
must call Godot, patch the runner instead of spawning it.

## Code style

- Type hints on every public function; `str | None` style (Python 3.10+).
- A short docstring on anything non-obvious, written like a human wrote it.
- Keep functions small and the pipeline steps separable - the whole point of
  the project is that each stage (scan, validate, audit, tokenize, train)
  can run and be debugged on its own.

## Commit messages

Short summary line, then a blank line, then bullet points of what changed
and why. The changelog is written from these, so be honest about intent.

## Where things live

See `docs/ARCHITECTURE.md` for the pipeline map and `docs/ROADMAP.md` for what is
planned. `docs/` holds per-version patch notes and the config reference.
