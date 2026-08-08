# Audit v0.5.0

> **Superseded / Archive.** Historical audit of the v0.5 phase. The successor is
> `docs/AUDIT_v0.6.md`; everything since v0.6 is in `docs/CHANGELOG_v0.10.2.md`.

## Fixed bugs and risks

- Git sources use reproducible branches/tags/commits; incomplete downloads no longer count as finished.
- Changes to the source ref are detected and marked as a renewed download.
- Corpus staging and prepared splits are replaced atomically from a build folder.
- Dataset files are written atomically, hashed and checked against the manifest length.
- Token streams automatically switch from `uint16` to `uint32` for large vocabularies.
- Sampling can now also use the last valid context start.
- Training resolves relative paths against the actually passed configuration, not against a randomly installed checkout.
- Training throughput only measures training time; validation and checkpoint I/O are reported separately.
- Resume and partial intervals produce correct token and step counts.
- `latest.pt` and `best.pt` use hardlinks where possible; old step checkpoints are kept in a limited way.
- VRAM probe runs handle early OOMs without follow-up errors and run isolated per profile.
- The model cached in the chat is unloaded from GPU/RAM before training, benchmark and VRAM probe.
- Stop terminates the complete process tree on Windows, including Git and probe child processes.
- Generation and evaluation take tokenizer and compute precision from the checkpoint.
- Temporary Godot check files and configuration backups are collision-safe.
- The Studio prioritizes the real corpus dataset over old demo manifests.
- Own Git sources have a visible ref field instead of a silent `master` default.
- Windows console output is ASCII-safe; JSON files stay UTF-8.
- FastAPI shutdown uses a lifespan handler instead of the deprecated event API.

## Deliberate limits

- v0.5 does not yet build an agent system and no autonomous project editing.
- Public repositories are only admitted with a permitted, declared license.
- The Godot validator checks syntax/API resolution, but not automatically complete gameplay semantics.
- A larger dataset only improves the model together with meaningful diversity, splits and training formats.
