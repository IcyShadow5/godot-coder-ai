# Releasing

Releasing is a one-liner at the end, but the prep still has a few moving
parts, so here is the order I follow. The one thing that kept breaking was
the `STUDIO.md` header - that is exactly why `tools/bump_version.py`
exists now: it bumps all four version markers in one go and refuses to run
if they are out of sync.

## 1. Prep (version bump + docs)

```powershell
python tools/bump_version.py 0.10.22
```

This updates `pyproject.toml`, `src/godot_coder/__init__.py`, the service
worker cache name in `sw.js`, and the `docs/STUDIO.md` header - and
validates all four afterwards. `python tools/bump_version.py --check`
only verifies consistency without changing anything.

Then write the per-version prose by hand:

- `docs/CHANGELOG_v<ver>.md` (patch notes) + a short entry in
  `CHANGELOG.md`
- `docs/INSTALL_v<ver>.md` (copy the previous one, update the file list)
- `README.md` Recent Changes entry + latest-doc links
- `mkdocs.yml` nav (point Install Guide / Changelog at the new files)

## 2. Verify

```powershell
python -m pytest -q
```

## 3. Ship

```powershell
git add -A
git commit -m "v<ver>: <what this one does>"
git push origin main      # CI + Docs workflows fire
git tag -a v<ver> -m "Release v<ver>"
git push origin v<ver>    # Release workflow builds the zip + release
```

## 4. After

- If any commit lands after the tag (docs fixes happen), refresh the
  release asset so the zip matches the current main:

  ```powershell
  git archive --format=zip -o godot-coder-ai-v<ver>-full.zip HEAD
  gh release upload v<ver> godot-coder-ai-v<ver>-full.zip `
    --repo IcyShadow5/godot-coder-ai --clobber
  ```

- Sync the changed files to the live Studio (`<your local live install>`), restart it and verify `/api/overview` reports
  the new version.
