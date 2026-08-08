# Godot Coder AI Remote API and configuration schema v1

> **Superseded / Archive.** The remote API in this form was replaced by the
> Tailscale Serve workflow (`CONFIGURE_REMOTE_STUDIO.ps1`,
> `remote_access configure | disable`). The setup is in `STUDIO.md`;
> the configuration file `data/studio/remote_access.json` is still
> used, but the endpoints may differ.

# Godot Coder AI Remote API and configuration schema v1

## Local configuration

Path: `data/studio/remote_access.json`

```json
{
  "format": "godot-coder-remote-access",
  "format_version": 1,
  "enabled": true,
  "allowed_users": ["owner@example.com"],
  "session_ttl_seconds": 3600,
  "pin_salt": "base64",
  "pin_hash": "base64-pbkdf2-sha256",
  "configured_at": "2026-08-05T00:00:00Z"
}
```

The PIN is not stored. Persisted are the salt and the PBKDF2-HMAC-SHA256 hash with 310,000 iterations. The file is created with user read/write permissions where possible.

## Identity headers

On a remote call the Studio expects the headers set by Tailscale Serve:

- `Tailscale-User-Login`
- `Tailscale-User-Name`
- optional `Tailscale-User-Profile-Pic`

These headers are only admissible as a security boundary when the backend listens exclusively on localhost and Tailscale Serve is the only remote proxy. Requests to a `*.ts.net` host without user headers - e.g. from a tagged device - are explicitly recognized as remote and rejected, not treated as local access.

## Session model

Cookie: `godot_coder_remote_session`

Properties:

- `HttpOnly`
- `Secure`
- `SameSite=Strict`
- path `/`
- valid 60 minutes by default
- only in the memory of the Studio process

Write requests additionally send:

```http
X-Godot-Coder-CSRF: <session-specific-token>
```

## Endpoints

### `GET /api/remote/status`

Returns the local resp. remote state, detected identity, read/write permission, session expiry, Tailscale state and a serve hint. Allowed users are not listed to remote clients. With an already authenticated remote session the session-specific CSRF token is delivered again so a UI reload can continue without a new PIN entry; the response is protected with `Cache-Control: no-store`.

### `POST /api/remote/unlock`

```json
{"pin": "123456"}
```

Creates a write session after identity, rate-limit and PIN checks. Response:

```json
{
  "unlocked": true,
  "csrf_token": "...",
  "expires_at": 1780000000.0
}
```

### `POST /api/remote/lock`

Deletes the current server session and the cookie. Requires a valid write session.

### `POST /api/jobs/remote/source-download`

```json
{
  "url": "https://github.com/owner/project",
  "confirm_owned": true
}
```

Starts a normal persistent Studio job. Progress events use the existing progress schema v1 and add:

- `phase = remote_link_validation`
- `phase = remote_download`
- `bytes_received`
- `bytes_total`
- `source_name`
- `source_url`

### `POST /api/remote/sources/upload`

Query parameters:

- `filename`
- `confirm_owned=true`

Body: raw ZIP bytes with `Content-Type: application/octet-stream`.

The endpoint deliberately uses no multipart form and therefore needs no additional upload dependency. The maximum payload is 256 MiB.

## Audit format

Path: `reports/remote_access/remote_audit.jsonl`

Every line contains a masked JSON object, e.g.:

```json
{
  "timestamp": "2026-08-05T00:00:00Z",
  "event": "remote_write_request",
  "level": "info",
  "identity": "owner@example.com",
  "method": "POST",
  "path": "/api/jobs/corpus/local-import",
  "status_code": 200
}
```

PINs, CSRF tokens, cookies, Authorization headers and detected secrets are not deliberately logged. The existing recursive secret masking is also applied to audit fields.

## Backward compatibility

- existing local API calls need neither Tailscale headers nor a PIN
- normal text logs stay intact
- the progress event schema stays version 1
- new download fields are optional and treated as missing on old events
- existing jobs, private import reports and training formats remain readable unchanged
