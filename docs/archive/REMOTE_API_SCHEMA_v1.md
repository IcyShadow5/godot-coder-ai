# Godot Coder AI Remote API und Konfigurationsschema v1

> **Veraltet / Archiv.** Die Remote-API in dieser Form wurde durch den
> Tailscale-Serve-Workflow ersetzt (`CONFIGURE_REMOTE_STUDIO.ps1`,
> `remote_access configure | disable`). Die Einrichtung steht in `STUDIO.md`;
> die Konfigurationsdatei `data/studio/remote_access.json` wird weiterhin
> verwendet, die Endpunkte können aber abweichen.

# Godot Coder AI Remote API und Konfigurationsschema v1

## Lokale Konfiguration

Pfad: `data/studio/remote_access.json`

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

Die PIN wird nicht gespeichert. Persistiert werden Salt und PBKDF2-HMAC-SHA256-Hash mit 310.000 Iterationen. Die Datei wird nach Möglichkeit mit Benutzer-Lese-/Schreibrechten angelegt.

## Identitätsheader

Bei einem Remote-Aufruf erwartet das Studio die von Tailscale Serve gesetzten Header:

- `Tailscale-User-Login`
- `Tailscale-User-Name`
- optional `Tailscale-User-Profile-Pic`

Diese Header sind nur dann als Sicherheitsgrenze zulässig, wenn das Backend ausschließlich auf localhost lauscht und Tailscale Serve der einzige Remote-Proxy ist. Anfragen an einen `*.ts.net`-Host ohne Benutzerheader – etwa von einem getaggten Gerät – werden ausdrücklich als Remote erkannt und abgewiesen, nicht als lokaler Zugriff behandelt.

## Sitzungsmodell

Cookie: `godot_coder_remote_session`

Eigenschaften:

- `HttpOnly`
- `Secure`
- `SameSite=Strict`
- Pfad `/`
- standardmäßig 60 Minuten gültig
- nur im Arbeitsspeicher des Studio-Prozesses

Schreibende Anfragen senden zusätzlich:

```http
X-Godot-Coder-CSRF: <session-specific-token>
```

## Endpunkte

### `GET /api/remote/status`

Liefert den lokalen beziehungsweise Remote-Zustand, erkannte Identität, Lese-/Schreibberechtigung, Sitzungsablauf, Tailscale-Zustand und Serve-Hinweis. Erlaubte Benutzer werden Remote-Clients nicht aufgelistet. Bei einer bereits authentifizierten Remote-Sitzung wird der sitzungsspezifische CSRF-Token erneut geliefert, damit ein UI-Neuladen ohne erneute PIN-Eingabe fortgesetzt werden kann; die Antwort ist mit `Cache-Control: no-store` geschützt.

### `POST /api/remote/unlock`

```json
{"pin": "123456"}
```

Erstellt nach Identitäts-, Rate-Limit- und PIN-Prüfung eine Schreibsitzung. Antwort:

```json
{
  "unlocked": true,
  "csrf_token": "...",
  "expires_at": 1780000000.0
}
```

### `POST /api/remote/lock`

Löscht die aktuelle Serversitzung und das Cookie. Erfordert eine gültige Schreibsitzung.

### `POST /api/jobs/remote/source-download`

```json
{
  "url": "https://github.com/owner/project",
  "confirm_owned": true
}
```

Startet einen normalen persistenten Studio-Job. Fortschrittsereignisse verwenden das bestehende Progress-Schema v1 und ergänzen:

- `phase = remote_link_validation`
- `phase = remote_download`
- `bytes_received`
- `bytes_total`
- `source_name`
- `source_url`

### `POST /api/remote/sources/upload`

Queryparameter:

- `filename`
- `confirm_owned=true`

Body: rohe ZIP-Bytes mit `Content-Type: application/octet-stream`.

Der Endpunkt nutzt bewusst kein Multipart-Formular und benötigt daher keine zusätzliche Upload-Abhängigkeit. Die maximale Nutzlast beträgt 256 MiB.

## Auditformat

Pfad: `reports/remote_access/remote_audit.jsonl`

Jede Zeile enthält ein maskiertes JSON-Objekt, beispielsweise:

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

PINs, CSRF-Tokens, Cookies, Authorization-Header und erkannte Secrets werden nicht absichtlich protokolliert. Die vorhandene rekursive Secret-Maskierung wird auch auf Auditfelder angewendet.

## Rückwärtskompatibilität

- bestehende lokale API-Aufrufe benötigen weder Tailscale-Header noch PIN
- normale Textlogs bleiben erhalten
- das Progress-Event-Schema bleibt Version 1
- neue Downloadfelder sind optional und werden bei alten Ereignissen als fehlend behandelt
- bestehende Jobs, private Importreports und Trainingsformate bleiben unverändert lesbar
