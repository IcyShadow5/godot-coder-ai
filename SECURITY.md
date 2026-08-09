# Security

I went through every path in the codebase and checked what could go wrong — process orphans, filesystem escapes, secret leaks, network exposure, prompt injection. Here's what I found and what I fixed.

If you spot something I missed, use GitHub's private vulnerability reporting (Security -> Advisories). Don't open a public issue for security stuff.

---

## How it's wired

```
Browser (localhost only)
  │
  ▼  http://127.0.0.1:8765
FastAPI server (server.py)
  │  _SecureRemoteMiddleware on every request
  │  safe_child() on every filesystem path
  │
  ▼  subprocess (command list, never a shell string)
Godot headless (+ Mono runtime)
  │  Job Object / process group kill-on-close
  ▼
GDScript parser / project importer
```

No remote access by default. The Tailscale Serve stuff (identity headers, PIN, CSRF, sessions) lives in `remote_access.py` and only activates when you explicitly set it up.

---

## 1. Managed-process lifecycle

The corpus import pipeline always used `run_managed_process` — Windows Job Objects, heartbeat monitoring, idle-timeout detection, tree cleanup. But `validate_godot.py` (the standalone GDScript parser CLI) still called raw `subprocess.run`. A timed-out Godot parse on that path would leave an orphaned Mono child behind. I fixed that.

Every Godot call now goes through the same managed process:

| Module | Godot call | Runner |
|---|---|---|
| `corpus.py` | `--import`, per-file `--script --check-only` | `run_managed_process` |
| `local_sources.py` | `--import`, per-file checker | `run_managed_process` |
| `validate_dataset.py` | per-file `--script --check-only` | `run_managed_process` |
| `validate_godot.py` | `--script --check-only` | `run_managed_process` *(fixed)* |
| `ui/services.py` (`validate_code`) | `--script --check-only` | `run_managed_process` |

**Windows:** I built a real `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` struct (144 bytes on x64, `LimitFlags` at offset 16) with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. This means every Mono grandchild dies when Python exits — even on a hard crash. No more ghost processes. `taskkill /T` is the fallback when job assignment fails.

**macOS / Linux:** `start_new_session=True` creates a process group. `_posix_descendants()` walks `/proc` to find the whole tree before `SIGTERM`/`SIGKILL`. On macOS and BSD (no `/proc`) I parse `ps` output so zombie processes don't hang the wait-loop.

There's a theoretical race between `process.poll()` and the termination call — the PID could be recycled. On Windows the Job Object doesn't care about PIDs. On POSIX the process group plus descendant enumeration makes it practically impossible to hit. The full analysis is in a comment block in `process_control.py`.

---

## 2. Structured result types

Validation results used to be bare dicts — accessing `result["passed"]` with a typo was a silent bug waiting to happen. They're typed dataclasses now:

- `ManagedProcessResult` — every `run_managed_process` call returns one (frozen, all fields explicit)
- `PerFileResult` — outcome of one Godot parser check on one `.gd` file
- `ValidationReport` — aggregate from `validate_dataset`, `to_dict()` for serialization

No internal dict key access anywhere anymore. The type checker catches mistakes at dev time.

---

## 3. Remote Studio (Tailscale Serve)

This is buried behind config and I put multiple gates in front of it:

- **PIN:** 6-12 digit numeric only, hashed with `PBKDF2-HMAC-SHA256` (310k iterations, 24-byte random salt), verified with `hmac.compare_digest` (timing-safe)
- **Rate limiting:** 5 failed attempts per identity within 5 minutes, then locked out
- **Sessions:** 32-byte `secrets.token_urlsafe` + CSRF tokens, httponly/secure/samesite=strict cookies, 5 min to 24 hour TTL
- **Identity:** Tailscale `Tailscale-User-Login` header checked against the allowlist. Tagged-device traffic (no user header) is treated as remote and denied
- **CSRF:** every write needs a matching `X-Godot-Coder-CSRF` header. Wrong token gets you a 403, you have to re-unlock
- **Audit log:** every unlock and remote write goes to `reports/remote_access/remote_audit.jsonl`, secrets masked
- **Read-only by default:** remote GET/HEAD/OPTIONS return data but need identity. Writes are PIN-locked. The `require_identity_for_read` flag can relax reads to the whole tailnet, but you have to opt in
- **Config:** written atomically (tmp + `os.replace`), `chmod 0o600`

26 tests cover this (`test_remote_access.py` + `test_remote_access_gaps.py`): PIN validation, CSRF mismatch, identity rejection, corrupted salt/digest, session expiry, rate limiting, unauthorized writes.

---

## 4. Path / filesystem escape

Every user-supplied path goes through `safe_child()` in `ui/paths.py`:

```python
def safe_child(root: Path, relative: str | Path, *, must_exist: bool = False) -> Path:
    # Rejects absolute paths, resolves, and verifies the result
    # stays inside root via Path.relative_to().
```

Every endpoint in `server.py` chains this with a second `.relative_to()` that confines the result to a specific subdirectory (`configs/`, `checkpoints/`, `data/raw/`). The data lab editor is locked to `data/raw/` — it physically cannot write outside that tree.

**Zip-slip:** remote source downloads and corpus ingestion reject archive entries with `..` traversal, absolute paths, or anything that resolves outside the extraction root.

10 tests cover this (`test_path_security_gaps.py` + `test_ui_paths.py`): absolute rejection, backslash traversal (platform-aware), must_exist, zip-slip, archive root validation.

---

## 5. Command execution

**No `shell=True` anywhere.** I grep the whole source tree for it in CI. Every subprocess call uses a command list. The command templates are hardcoded in `server.py` — user input never touches a shell:

```python
# Training start — config path is gated through safe_child first
args = ["-m", "godot_coder.train", "--config", str(config_path)]
app.state.jobs.start("training", args, max_steps=max_steps)
```

No `os.system()`, no `exec()`, no `eval()` on user input anywhere. The CI security gate confirms this on every push.

---

## 6. Secrets

**Detection:** `mask_secrets` in `progress_events.py` has 5 regex patterns that catch secrets before they hit logs, reports, or audit trails. It's called recursively on every event payload. It also scrubs the remote audit log and Tailscale status snapshots.

**Storage:** remote config is written atomically with `os.replace` + `chmod 0o600`. PIN digests are never plaintext.

**Environment:** no secrets hardcoded anywhere. `GODOT_CODER_*` env vars are read at runtime and never written to disk.

---

## 7. Network

Remote source downloads (`remote_sources.py`) are the only outbound path:

- **Hosts:** GitHub, GitLab, Bitbucket only (whitelist)
- **Protocol:** HTTPS (port 443)
- **IPs:** `ipaddress.ip_address().is_global` — private, loopback, link-local, reserved all blocked
- **Credentials in URLs:** rejected (`user:password@`)
- **Query/fragment:** stripped, path only
- **Size:** 256 MiB cap, checked mid-stream
- **Redirects:** max 5, manually followed
- **User-Agent:** `Godot-Coder-AI/{version}` — versioned, not generic

Nothing else makes outbound calls. The server binds to `127.0.0.1` only.

---

## 8. Prompt injection

GDScript files from the corpus go straight into the tokenizer (`prepare_data.py` -> `data.py` -> `train.py`). They are **never** interpolated into a prompt. The chat path is:

```
user prompt -> tokenizer.encode -> model.generate -> tokenizer.decode -> response
```

No system prompt template, no intermediate LLM, no way for corpus content to enter a prompt context. Corpus files are still scanned for secrets and validated by Godot, but that's about data quality and credential leaks, not prompt safety.

---

## 9. Permission escalation

No subagents. Every action is a direct function call or a subprocess with a hardcoded command template. No dynamic tool dispatch, no plugin system, no `importlib`-based loading from user input. The child process runs as the same OS user — it can't escalate.

---

## 10. Release checklist

| Check | Status |
|---|---|
| No secrets in source | verified |
| No hardcoded credentials | env vars only, PIN hashed |
| No `shell=True` | zero, CI-enforced |
| All paths gated by `safe_child()` | every endpoint |
| All Godot calls through `run_managed_process` | 5/5 modules |
| Remote access off by default | yes |
| Network whitelisted | GitHub/GitLab/Bitbucket |
| Audit logging | remote access + secrets masked |
| Security headers | CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy |
| CI security gate | 85 tests on every push |

### Security headers

```
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
X-Frame-Options: DENY
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
Content-Security-Policy: default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'
Cache-Control: no-store (on /api/*)
```

---

## Reporting

Use GitHub private vulnerability reporting (Security -> Advisories -> Report). I check within 72 hours. If confirmed I'll ship a fix and publish an advisory after the patch is out.
