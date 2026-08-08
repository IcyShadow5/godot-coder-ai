# Security

Godot Coder AI is a local-first tool: no cloud, no telemetry, no accounts.
Your data and your training runs stay on your machine.

## Secret scanning

Every imported project is scanned for secrets - API keys, tokens, `.env`
files, private keys, and similar - before it can enter the training corpus.
Anything flagged is hard-excluded by default. This is a deliberate part of
the pipeline, not an afterthought: a training corpus is only as clean as the
secrets it does not contain.

## Reporting a vulnerability

If you find a security issue - a secret the scanner misses, a path traversal,
a way an untrusted project could execute code during import - please report
it privately before opening a public issue:

- Open a private security advisory on GitHub, or
- Email the maintainers (address listed on the profile).

Please include a minimal repro and which version you tested. Reports are
acknowledged within a few days.

## Scope

The Studio binds to `127.0.0.1` by default and is not designed to be exposed
to the public internet. `remote_access configure` exposes it inside a
Tailscale tailnet only. Importing untrusted projects is the main attack
surface - see the import pipeline docs for how validation limits it.
