# Security

This document records a 10-point security audit of the Godot Coder AI codebase (v0.10.12, August 2026). It covers process isolation, network boundaries, secret handling, path safety, and injection surfaces — everything between the Studio web UI and the Godot child processes it spawns.

If you find a security issue, please report it via the private vulnerability reporting on GitHub instead of opening a public issue.

---

## Architecture overview

The attack surface is bounded by three layers:

```
Browser (localhost only)
  │
  ▼  http://127.0.0.1:8765
FastAPI server (server.py)
  │  _SecureRemoteMiddleware gates every request
  │  safe_child() validates every filesystem path argument
  │
  ▼  subprocess (command list, never shell)
Godot headless (+ Mono runtime)
  │  Job Object / process group kill-on-close
  ▼
GDScript parser / project importer
```

No remote network access exists by default. The optional **Tailscale Serve** integration adds a fourth layer (identity header verification, PIN unlock, CSRF tokens, session cookies) which is documented in `remote_access.py` and gated by explicit user configuration.

---

## 1. Managed-process lifecycle

**Before the audit:** the corpus import pipeline used `run_managed_process` everywhere (Windows Job Objects, heartbeat monitoring, idle-timeout detection, tree cleanup), but `validate_godot.py` (the standalone GDScript parser CLI) still called raw `subprocess.run`. A timed-out Godot parse on that path would leave an orphaned Mono child process behind.

**After the audit:** every Godot invocation in the codebase now goes through `run_managed_process`:

| Module | Godot call | Runner |
|---|---|---|
| `corpus.py` | `--import`, per-file `--script --check-only` | `run_managed_process` |
| `local_sources.py` | `--import`, per-file checker | `run_managed_process` |
| `validate_dataset.py` | per-file `--script --check-only` | `run_managed_process` |
| `validate_godot.py` | `--script --check-only` | `run_managed_process` ✅ *(fixed)* |
| `ui/services.py` (`validate_code`) | `--script --check-only` | `run_managed_process` |

**Windows:** a real `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` struct (144 bytes x64, `LimitFlags` at offset 16) with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` guarantees that every Mono grandchild dies when the parent Python process exits — even on a hard crash. `taskkill /T` is the verified fallback when job assignment fails.

**macOS / Linux:** `start_new_session=True` creates a process group; `_posix_descendants()` walks `/proc` to enumerate the full tree before sending `SIGTERM`/`SIGKILL`. On macOS and BSD (no `/proc`), `ps` output is parsed to detect zombie processes so the wait-loop doesn't hang.

**Race condition documented:** between `process.poll()` and the termination call the PID could theoretically be recycled. On Windows the Job Object covers every descendant regardless of PID; on POSIX the process group plus descendant enumeration makes the window practically impossible to hit. See the comment block in `process_control.py` for the full analysis.

---

## 2. Structured result types

Validation results are now typed dataclasses rather than bare dicts:

- `ManagedProcessResult` — return value of every `run_managed_process` call (frozen, all fields explicit)
- `PerFileResult` — outcome of a single Godot parser check on one `.gd` file
- `ValidationReport` — aggregate report from `validate_dataset`, serializable via `to_dict()`

No caller anywhere in the codebase accesses internal dict keys on these types without going through the typed interface.

---

## 3. Remote Studio (Tailscale Serve)

The remote-access layer is gated behind explicit user configuration and multiple defenses:

- **PIN:** 6–12 digit numeric only, hashed with `PBKDF2-HMAC-SHA256` (310,000 iterations, 24-byte random salt), verified with `hmac.compare_digest` (timing-safe)
- **Rate limiting:** 5 failed unlock attempts per identity within 5 minutes → lockout
- **Sessions:** 32-byte `secrets.token_urlsafe` session + CSRF tokens, stored in httponly/secure/samesite=strict cookies, TTL between 5 minutes and 24 hours
- **Identity:** Tailscale `Tailscale-User-Login` header verified against the configured allowlist. Tagged-device traffic (no user header) is treated as remote and denied
- **CSRF:** every write request requires a matching `X-Godot-Coder-CSRF` header. Wrong token → 403, must re-unlock
- **Audit log:** every unlock attempt and remote write is recorded to `reports/remote_access/remote_audit.jsonl`, with all secrets masked
- **Read-only by default:** remote GET/HEAD/OPTIONS return data but require identity. Writes are locked behind the PIN. The `require_identity_for_read` flag can relax read access to the entire tailnet, but this is explicitly opt-in
- **Config permissions:** remote config file is written atomically (tmp + `os.replace`) and `chmod`ed to `0o600`

**Tests:** `test_remote_access.py` (17 tests) + `test_remote_access_gaps.py` (9 tests) cover PIN validation, CSRF mismatch, identity rejection, corrupted salt/digest, session expiry, rate limiting, and unauthorized write denial.

---

## 4. Path / filesystem escape

All user-supplied path arguments go through `safe_child()` in `ui/paths.py`:

```python
def safe_child(root: Path, relative: str | Path, *, must_exist: bool = False) -> Path:
    # Rejects absolute paths, resolves, and verifies the result
    # stays inside root via Path.relative_to().
```

Every endpoint in `server.py` that accepts a path argument chains this with a second `.relative_to()` check that confines the result to a specific subdirectory (e.g., `configs/`, `checkpoints/`, `data/raw/`). The data lab editor (`write_dataset_file`) is locked to `data/raw/` exclusively — it cannot write outside that tree.

**Zip-slip guard:** remote source downloads (`remote_sources.py`) and corpus ingestion (`corpus.py`) reject archive entries that contain `..` traversal, absolute paths, or resolve outside the extraction root.

**Tests:** `test_path_security_gaps.py` (8 tests) + `test_ui_paths.py` (2 tests) cover absolute rejection, platform-aware backslash traversal, must_exist enforcement, zip-slip rejection, and archive root-layout validation.

---

## 5. Command execution sandbox

**No `shell=True` anywhere in the codebase.** Every subprocess invocation uses a command list, never string interpolation. The commands themselves are hardcoded templates in `server.py` — user input never reaches a shell:

```python
# Example: training start
args = ["-m", "godot_coder.train", "--config", str(config_path)]
app.state.jobs.start("training", args, max_steps=max_steps)
```

User-controlled values (`config_path`, `resume`, `input_dir`, `tokenizer`, etc.) are validated through `safe_child()` before they're appended to the command list. No `os.system()`, no `exec()`, no `eval()` on user input.

---

## 6. Secret isolation

**Detection (`mask_secrets` in `progress_events.py`):** 5 regex patterns catch secrets before they reach logs, reports, or audit trails:

| Pattern | What it catches |
|---|---|
| `sk-(proj-)?[A-Za-z0-9_-]{12,}` | Stripe-style API keys |
| `AKIA[0-9A-Z]{16}` | AWS access key IDs |
| `-----BEGIN ... PRIVATE KEY-----` | PEM private keys (RSA, EC, OpenSSH) |
| `api_key\|secret_key\|access_token\|auth_token\|password[:=]\s*["']?[^\s,;"']{8,}` | Key-value assignments in configs/scripts |
| `authorization\s*:\s*bearer\s+[A-Za-z0-9._~+\-/=]{8,}` | Bearer tokens in HTTP headers |

`mask_secrets` is called recursively on every event payload before it's serialized. It also scrubs the remote audit log and Tailscale status snapshots.

**Storage:** the remote config (`remote_access.json`) is written atomically with `os.replace` and `chmod 0o600`. PIN digests never appear in plaintext.

**Environment:** no secrets are hardcoded in source files. `GODOT_CODER_*` env vars are read at runtime and never written to disk.

---

## 7. Network access limits

Remote source downloads (`remote_sources.py`) are the only outbound network path:

| Constraint | Enforcement |
|---|---|
| **Hosts** | Whitelist: GitHub, GitLab, Bitbucket only |
| **Protocol** | HTTPS only (port 443) |
| **IP range** | `ipaddress.ip_address().is_global` check — private, loopback, link-local, and reserved addresses are blocked |
| **Credentials** | URLs containing `user:password@` are rejected |
| **Query/fragment** | Stripped — only the path is preserved |
| **Size** | 256 MiB cap, enforced mid-stream |
| **Redirects** | Max 5, manually followed (no auto-redirect handler) |
| **User-Agent** | `Godot-Coder-AI/{version}` — versioned, not a generic browser string |

No other outbound network calls exist. The Studio server binds to `127.0.0.1` only.

**Tests:** `test_remote_sources.py` covers URL validation, host whitelist, private-IP rejection, and credential rejection.

---

## 8. Prompt injection (training data)

GDScript files from the corpus are tokenized directly for training (`prepare_data.py` → `data.py` → `train.py`). They are **never interpolated into an LLM system prompt**. The chat generation path is:

```
user prompt → tokenizer.encode → model.generate → tokenizer.decode → response
```

There is no intermediate LLM, no system-prompt template, and no way for corpus file content to enter a prompt context. The model sees only token IDs during training and only the user's chat input during inference.

Corpus files are still scanned for secrets (`local_sources.py` secret scan phase) and validated by Godot before admission, but this is about data quality and credential leaks, not prompt safety.

---

## 9. Permission escalation / subagents

There are no subagents in this codebase. Every action is a direct Python function call or a subprocess spawned with a hardcoded command template. No dynamic tool dispatch, no plugin system, no `importlib`-based module loading from user input.

The `run_managed_process` child process inherits the parent's environment and runs as the same OS user. It cannot escalate privileges — it has the same access as the Python process that spawned it, which is already the user running the Studio.

---

## 10. Public release readiness

| Check | Status |
|---|---|
| No secrets in source | ✅ verified (`mask_secrets` pass + manual review) |
| No hardcoded credentials | ✅ (env vars only, PIN hashed at rest) |
| No `shell=True` | ✅ |
| All paths gated by `safe_child()` | ✅ |
| All Godot calls through `run_managed_process` | ✅ |
| Remote access disabled by default | ✅ |
| Network access whitelisted | ✅ |
| Audit logging for sensitive actions | ✅ |
| Security headers on all responses | ✅ (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy) |
| Test suite: 234 tests, 3 OS CI | ✅ |

### Security headers (applied by `_SecureRemoteMiddleware`)

```
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
X-Frame-Options: DENY
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
Content-Security-Policy: default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'
Cache-Control: no-store (on /api/*)
```

---

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** (Security → Advisories → Report a vulnerability). Do not open a public issue for security-sensitive findings.

I review reports within 72 hours and will acknowledge receipt. If the issue is confirmed I'll ship a fix and publish an advisory after the patch is available.
