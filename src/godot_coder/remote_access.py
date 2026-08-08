from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path
from typing import Any, Mapping

from .progress_events import mask_secrets

REMOTE_CONFIG_FORMAT = "godot-coder-remote-access"
REMOTE_CONFIG_VERSION = 3
DEFAULT_REMOTE_PORT = 8765
SESSION_COOKIE = "godot_coder_remote_session"
DEFAULT_SESSION_TTL_SECONDS = 60 * 60
PIN_ITERATIONS = 310_000
MAX_UNLOCK_ATTEMPTS = 5
UNLOCK_WINDOW_SECONDS = 5 * 60
TAILNET_DEVICE_ID = "tailnet-device"


class RemoteAccessError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def remote_config_path(project_root: Path) -> Path:
    return project_root / "data" / "studio" / "remote_access.json"


def remote_audit_path(project_root: Path) -> Path:
    return project_root / "reports" / "remote_access" / "remote_audit.jsonl"


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _decode_identity_header(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parts = decode_header(value)
        decoded = "".join(
            item.decode(encoding or "utf-8", errors="replace") if isinstance(item, bytes) else item
            for item, encoding in parts
        )
    except (LookupError, UnicodeError, ValueError):
        decoded = value
    cleaned = decoded.strip()
    return cleaned[:320] or None


def request_identity(headers: Mapping[str, str]) -> dict[str, str | None]:
    # Starlette's Headers mapping is case-insensitive, while direct unit-test and
    # CLI mappings may not be. Normalize once so the security decision is stable.
    normalized_headers = {str(key).lower(): str(value) for key, value in headers.items()}
    login = _decode_identity_header(normalized_headers.get("tailscale-user-login"))
    return {
        "login": login.lower() if login else None,
        "display_name": _decode_identity_header(normalized_headers.get("tailscale-user-name")),
        "profile_picture": _decode_identity_header(normalized_headers.get("tailscale-user-profile-pic")),
    }


def request_is_remote(headers: Mapping[str, str]) -> bool:
    normalized_headers = {str(key).lower(): str(value) for key, value in headers.items()}
    if normalized_headers.get("tailscale-user-login"):
        return True
    host = normalized_headers.get("host", "").split(":", 1)[0].strip("[]").lower().rstrip(".")
    # Tailscale Serve DNS names are restricted to the tailnet's *.ts.net domain.
    # This also treats tagged-device traffic (which has no user identity header)
    # as remote and therefore denies it instead of accidentally granting local access.
    return host.endswith(".ts.net")


def hash_pin(pin: str, *, salt: bytes | None = None) -> tuple[str, str]:
    if not pin.isdigit() or not 6 <= len(pin) <= 12:
        raise ValueError("The remote PIN must consist of 6 to 12 digits.")
    salt_value = salt or secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt_value, PIN_ITERATIONS)
    return base64.b64encode(salt_value).decode("ascii"), base64.b64encode(digest).decode("ascii")


def verify_pin(pin: str, salt_b64: str, digest_b64: str) -> bool:
    try:
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(digest_b64, validate=True)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PIN_ITERATIONS)
    return hmac.compare_digest(actual, expected)


def load_remote_config(project_root: Path) -> dict[str, Any]:
    path = remote_config_path(project_root)
    if not path.exists():
        return {
            "format": REMOTE_CONFIG_FORMAT,
            "format_version": REMOTE_CONFIG_VERSION,
            "enabled": False,
            "allowed_users": [],
            "session_ttl_seconds": DEFAULT_SESSION_TTL_SECONDS,
            "port": DEFAULT_REMOTE_PORT,
            "pin_salt": None,
            "pin_hash": None,
            "configured_at": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RemoteAccessError(f"Remote configuration could not be read: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("format") != REMOTE_CONFIG_FORMAT:
        raise RemoteAccessError("The remote configuration has an unknown format.")
    allowed = sorted({str(value).strip().lower() for value in payload.get("allowed_users", []) if str(value).strip()})
    # v2 configs default require_identity_for_read=True; v3+ reads the actual flag.
    require_id = bool(payload.get("require_identity_for_read", True))
    return {
        "format": REMOTE_CONFIG_FORMAT,
        "format_version": int(payload.get("format_version") or REMOTE_CONFIG_VERSION),
        "enabled": bool(payload.get("enabled")),
        "allowed_users": allowed,
        "require_identity_for_read": require_id,
        "session_ttl_seconds": max(300, min(86_400, int(payload.get("session_ttl_seconds") or DEFAULT_SESSION_TTL_SECONDS))),
        "port": max(1, min(65_535, int(payload.get("port") or DEFAULT_REMOTE_PORT))),
        "pin_salt": payload.get("pin_salt"),
        "pin_hash": payload.get("pin_hash"),
        "configured_at": payload.get("configured_at"),
    }


def configure_remote_access(
    project_root: Path,
    *,
    allowed_users: list[str],
    pin: str,
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    port: int = DEFAULT_REMOTE_PORT,
    require_identity_for_read: bool = True,
) -> dict[str, Any]:
    users = sorted({value.strip().lower() for value in allowed_users if value.strip()})
    if not users:
        raise ValueError("At least one allowed Tailscale login is required.")
    if not 1 <= int(port) <= 65_535:
        raise ValueError("The remote port must be between 1 and 65535.")
    salt, digest = hash_pin(pin)
    payload = {
        "format": REMOTE_CONFIG_FORMAT,
        "format_version": REMOTE_CONFIG_VERSION,
        "enabled": True,
        "allowed_users": users,
        "require_identity_for_read": require_identity_for_read,
        "session_ttl_seconds": max(300, min(86_400, int(session_ttl_seconds))),
        "port": int(port),
        "pin_salt": salt,
        "pin_hash": digest,
        "configured_at": _now_iso(),
    }
    _atomic_json_write(remote_config_path(project_root), payload)
    return load_remote_config(project_root)


def disable_remote_access(project_root: Path) -> dict[str, Any]:
    payload = load_remote_config(project_root)
    payload["enabled"] = False
    payload["disabled_at"] = _now_iso()
    _atomic_json_write(remote_config_path(project_root), payload)
    return load_remote_config(project_root)


def find_tailscale_cli() -> Path | None:
    command = shutil.which("tailscale")
    if command:
        return Path(command)
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Tailscale" / "tailscale.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    return None


def tailscale_status(*, timeout: float = 5.0) -> dict[str, Any]:
    executable = find_tailscale_cli()
    result: dict[str, Any] = {
        "installed": executable is not None,
        "backend_state": None,
        "online": False,
        "dns_name": None,
        "hostname": None,
        "tailnet": None,
        "serve_url": None,
        "error": None,
    }
    if executable is None:
        return result
    try:
        completed = subprocess.run(
            [str(executable), "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["error"] = str(exc)
        return result
    if completed.returncode != 0:
        result["error"] = (completed.stderr or completed.stdout or "Tailscale status failed.").strip()[:500]
        return result
    try:
        payload = json.loads(completed.stdout)
    except (ValueError, TypeError):
        result["error"] = "Tailscale returned no valid status JSON."
        return result
    self_node = payload.get("Self") if isinstance(payload.get("Self"), dict) else {}
    dns_name = str(self_node.get("DNSName") or "").rstrip(".") or None
    backend_state = str(payload.get("BackendState") or "") or None
    result.update({
        "backend_state": backend_state,
        "online": backend_state == "Running" and bool(self_node.get("Online", True)),
        "dns_name": dns_name,
        "hostname": self_node.get("HostName"),
        "tailnet": (payload.get("CurrentTailnet") or {}).get("Name") if isinstance(payload.get("CurrentTailnet"), dict) else None,
        "serve_url": f"https://{dns_name}" if dns_name else None,
    })
    return mask_secrets(result)


def tailscale_serve_target(port: int) -> str:
    if not 1 <= int(port) <= 65_535:
        raise ValueError("The remote port must be between 1 and 65535.")
    return f"http://127.0.0.1:{int(port)}"


def _json_contains(value: Any, needle: str) -> bool:
    if isinstance(value, Mapping):
        return any(_json_contains(item, needle) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_json_contains(item, needle) for item in value)
    return needle.lower() in str(value).lower()


def tailscale_serve_status(port: int = DEFAULT_REMOTE_PORT, *, timeout: float = 8.0) -> dict[str, Any]:
    executable = find_tailscale_cli()
    target = tailscale_serve_target(port)
    result: dict[str, Any] = {
        "installed": executable is not None,
        "configured": False,
        "target": target,
        "error": None,
    }
    if executable is None:
        return result
    try:
        completed = subprocess.run(
            [str(executable), "serve", "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["error"] = str(exc)
        return result
    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0:
        result["error"] = mask_secrets(output or "Tailscale Serve status failed.")[:500]
        return result
    try:
        payload = json.loads(completed.stdout)
    except (ValueError, TypeError):
        # Older clients may return a human-readable status. Keep the check useful
        # without trusting arbitrary output as a configured endpoint.
        result["configured"] = target in output or f"127.0.0.1:{int(port)}" in output
        result["status_format"] = "text"
        return result
    result["configured"] = _json_contains(payload, target) or _json_contains(payload, f"127.0.0.1:{int(port)}")
    result["status_format"] = "json"
    return result


def configure_tailscale_serve(port: int, *, timeout: float = 30.0) -> dict[str, Any]:
    executable = find_tailscale_cli()
    if executable is None:
        raise RemoteAccessError("Tailscale CLI was not found. Install or start Tailscale first.")
    target = tailscale_serve_target(port)
    command = [str(executable), "serve", "--bg", target]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    output = (completed.stdout + "\n" + completed.stderr).strip()
    if completed.returncode != 0:
        raise RemoteAccessError(f"Tailscale Serve could not be enabled: {mask_secrets(output)[:800]}")
    verified = tailscale_serve_status(port)
    return {
        "configured": True,
        "verified": bool(verified.get("configured")),
        "command": command,
        "target": target,
        "output": mask_secrets(output),
        "verification": verified,
    }


def remote_self_check(project_root: Path, *, port: int | None = None, socket_timeout: float = 1.5) -> dict[str, Any]:
    config = load_remote_config(project_root)
    effective_port = int(port or config.get("port") or DEFAULT_REMOTE_PORT)
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str, *, required: bool = True) -> None:
        checks.append({"name": name, "passed": bool(passed), "required": required, "detail": detail})

    configured = bool(config.get("enabled") and config.get("allowed_users") and config.get("pin_hash") and config.get("pin_salt"))
    add("remote_config", configured, "Remote configuration complete." if configured else "Remote configuration or PIN/allowlist is missing.")
    add("loopback_target", True, tailscale_serve_target(effective_port))
    try:
        with socket.create_connection(("127.0.0.1", effective_port), timeout=socket_timeout):
            local_reachable = True
    except OSError:
        local_reachable = False
    add("local_studio", local_reachable, f"127.0.0.1:{effective_port} is reachable." if local_reachable else f"The Studio is currently not listening on 127.0.0.1:{effective_port}.")
    status = tailscale_status()
    add("tailscale_online", bool(status.get("online")), status.get("error") or status.get("backend_state") or "Tailscale is not online.")
    serve = tailscale_serve_status(effective_port)
    add("serve_proxy", bool(serve.get("configured")), serve.get("error") or serve.get("target") or "Serve target not recognized.")
    required = [item for item in checks if item["required"]]
    return {
        "ok": all(item["passed"] for item in required),
        "port": effective_port,
        "serve_url": status.get("serve_url"),
        "checks": checks,
        "tailscale": status,
        "serve": serve,
    }


@dataclass
class RemoteSession:
    token: str
    csrf_token: str
    identity: str
    created_at: float
    expires_at: float


class RemoteAccessManager:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.sessions: dict[str, RemoteSession] = {}
        self.attempts: dict[str, list[float]] = {}

    def config(self) -> dict[str, Any]:
        return load_remote_config(self.project_root)

    def audit(self, event: str, *, identity: str | None = None, level: str = "info", **fields: Any) -> None:
        path = remote_audit_path(self.project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = mask_secrets({
            "timestamp": _now_iso(),
            "event": event,
            "level": level,
            "identity": identity,
            **fields,
        })
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def _prune(self, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        self.sessions = {token: session for token, session in self.sessions.items() if session.expires_at > current}
        cutoff = current - UNLOCK_WINDOW_SECONDS
        self.attempts = {identity: [stamp for stamp in values if stamp >= cutoff] for identity, values in self.attempts.items()}

    def identity(self, headers: Mapping[str, str]) -> dict[str, str | None]:
        return request_identity(headers)

    def status(self, headers: Mapping[str, str], cookies: Mapping[str, str] | None = None) -> dict[str, Any]:
        self._prune()
        config = self.config()
        identity = self.identity(headers)
        login = identity["login"]
        is_remote = request_is_remote(headers)
        identity_allowed = bool(login and login in config["allowed_users"])
        tailnet_allowed = is_remote and config["enabled"] and not config.get("require_identity_for_read", True)
        token = (cookies or {}).get(SESSION_COOKIE)
        session = self.sessions.get(token or "")
        authenticated = bool(session and login and session.identity == login and session.expires_at > time.time())
        tailnet_authenticated = bool(session and session.identity == TAILNET_DEVICE_ID and session.expires_at > time.time())
        port = int(config.get("port") or DEFAULT_REMOTE_PORT)
        ts_status = tailscale_status() if not is_remote else {
            "installed": True,
            "online": True,
            "backend_state": "Proxied",
            "dns_name": None,
            "hostname": None,
            "tailnet": None,
            "serve_url": None,
            "error": None,
        }
        can_read = (not is_remote) or (config["enabled"] and (identity_allowed or tailnet_allowed))
        can_write = (not is_remote) or (config["enabled"] and (identity_allowed and authenticated) or (tailnet_allowed and tailnet_authenticated))
        return {
            "config_version": config["format_version"],
            "enabled": config["enabled"],
            "configured": bool(config["allowed_users"] and config["pin_hash"] and config["pin_salt"]),
            "require_identity_for_read": config.get("require_identity_for_read", True),
            "is_remote": is_remote,
            "identity": identity,
            "identity_allowed": identity_allowed,
            "tailnet_allowed": tailnet_allowed,
            "authenticated": authenticated or tailnet_authenticated,
            "csrf_token": session.csrf_token if (authenticated or tailnet_authenticated) and session else None,
            "can_read": can_read,
            "can_write": can_write,
            "read_only": is_remote and not (authenticated or tailnet_authenticated),
            "session_expires_at": session.expires_at if (authenticated or tailnet_authenticated) and session else None,
            "allowed_users": config["allowed_users"] if not is_remote else [],
            "session_ttl_seconds": config["session_ttl_seconds"],
            "port": port,
            "tailscale": ts_status,
            "serve": tailscale_serve_status(port) if not is_remote else {"configured": True, "target": tailscale_serve_target(port), "error": None},
            "serve_command": f"tailscale serve --bg {tailscale_serve_target(port)}",
            "security": {
                "localhost_origin": True,
                "tailscale_identity_required": config.get("require_identity_for_read", True),
                "write_pin_required": True,
                "csrf_required": True,
                "public_funnel_supported": False,
            },
        }

    def unlock(self, headers: Mapping[str, str], pin: str) -> RemoteSession:
        self._prune()
        config = self.config()
        identity = self.identity(headers)
        login = identity["login"]
        tailnet_fallback = not config.get("require_identity_for_read", True) and not login
        if not login and not tailnet_fallback:
            raise RemoteAccessError("Remote unlock is only available through Tailscale Serve.")
        effective_login = login or TAILNET_DEVICE_ID
        if not tailnet_fallback and (not config["enabled"] or login not in config["allowed_users"]):
            self.audit("unlock_denied", identity=login, level="warning", reason="identity_not_allowed")
            if not login:
                raise RemoteAccessError("No Tailscale identity received. Unlock is only available through Tailscale Serve.")
            raise RemoteAccessError(f"Tailscale identity '{login}' is not allowed for the Studio.")
        attempts = self.attempts.setdefault(effective_login, [])
        if len(attempts) >= MAX_UNLOCK_ATTEMPTS:
            self.audit("unlock_rate_limited", identity=effective_login, level="warning")
            raise RemoteAccessError("Too many failed attempts. Try again in a few minutes.")
        if not verify_pin(pin, str(config["pin_salt"] or ""), str(config["pin_hash"] or "")):
            attempts.append(time.time())
            self.audit("unlock_failed", identity=effective_login, level="warning")
            raise RemoteAccessError("Remote PIN is wrong.")
        self.attempts.pop(effective_login, None)
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        now = time.time()
        session = RemoteSession(
            token=token,
            csrf_token=csrf_token,
            identity=effective_login,
            created_at=now,
            expires_at=now + config["session_ttl_seconds"],
        )
        self.sessions[token] = session
        self.audit("remote_unlocked", identity=login, expires_at=session.expires_at)
        return session

    def lock(self, token: str | None, identity: str | None = None) -> None:
        if token:
            self.sessions.pop(token, None)
        self.audit("remote_locked", identity=identity)

    def authorize(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        cookies: Mapping[str, str],
    ) -> tuple[int, str] | None:
        identity = self.identity(headers)
        login = identity["login"]
        if not request_is_remote(headers):
            return None
        if path in {"/", "/manifest.webmanifest", "/sw.js"} or path.startswith("/static/") or path == "/api/remote/status":
            return None
        config = self.config()
        if not config["enabled"]:
            self.audit("request_denied", identity=login, level="warning", path=path, method=method, reason="remote_disabled")
            return 403, "Remote access is disabled."
        tailnet_fallback = not config.get("require_identity_for_read", True) and not login
        if not tailnet_fallback and login not in config["allowed_users"]:
            reason = "identity_header_missing" if not login else "identity_not_allowed"
            self.audit("request_denied", identity=login, level="warning", path=path, method=method, reason=reason)
            if not login:
                return 403, "No Tailscale identity received. Is Tailscale Serve running with HTTPS? Check `tailscale serve status`."
            return 403, f"Tailscale identity '{login}' is not allowed for the Studio."
        if method.upper() in {"GET", "HEAD", "OPTIONS"} or path == "/api/remote/unlock":
            return None
        self._prune()
        token = cookies.get(SESSION_COOKIE)
        session = self.sessions.get(token or "")
        csrf = headers.get("x-godot-coder-csrf")
        session_identity = session.identity if session else None
        valid_session = bool(
            session and session.expires_at > time.time()
            and (session_identity == login or (tailnet_fallback and session_identity == TAILNET_DEVICE_ID))
        )
        if not valid_session:
            self.audit("write_denied", identity=login or TAILNET_DEVICE_ID, level="warning", path=path, method=method, reason="locked")
            return 423, "Remote write access is locked. Unlock it with your PIN."
        if not csrf or not hmac.compare_digest(csrf, session.csrf_token):
            self.audit("write_denied", identity=login or TAILNET_DEVICE_ID, level="warning", path=path, method=method, reason="csrf")
            return 403, "CSRF check failed. Unlock remote access again."
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure secure Tailscale remote access for Godot Coder AI.")
    parser.add_argument("--root", default=".")
    subparsers = parser.add_subparsers(dest="command", required=True)
    configure = subparsers.add_parser("configure")
    configure.add_argument("--user", action="append", default=[])
    configure.add_argument("--port", type=int, default=DEFAULT_REMOTE_PORT)
    configure.add_argument("--session-minutes", type=int, default=60)
    configure.add_argument("--no-serve", action="store_true")
    configure.add_argument("--no-identity-required", action="store_true", help="Allow read access without a Tailscale identity header (within the tailnet only).")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--json", action="store_true")
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--json", action="store_true")
    disable = subparsers.add_parser("disable")
    disable.add_argument("--reset-serve", action="store_true")
    return parser.parse_args()


def _reset_tailscale_serve() -> None:
    executable = find_tailscale_cli()
    if executable is None:
        raise RemoteAccessError("Tailscale CLI was not found.")
    completed = subprocess.run([str(executable), "serve", "reset"], check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RemoteAccessError((completed.stderr or completed.stdout or "Tailscale Serve reset failed.").strip())


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    try:
        if args.command == "configure":
            users = [value.strip() for value in args.user if value.strip()]
            if not users:
                entered = input("Allowed Tailscale login (e.g. name@example.com): ").strip()
                users = [entered] if entered else []
            pin = getpass.getpass("New remote PIN (6-12 digits): ")
            confirm = getpass.getpass("Repeat remote PIN: ")
            if pin != confirm:
                raise RemoteAccessError("The PIN entries do not match.")
            config = configure_remote_access(
                root,
                allowed_users=users,
                pin=pin,
                session_ttl_seconds=args.session_minutes * 60,
                port=args.port,
                require_identity_for_read=not getattr(args, "no_identity_required", False),
            )
            if not args.no_serve:
                try:
                    configure_tailscale_serve(args.port)
                except Exception:
                    disable_remote_access(root)
                    raise
            status = tailscale_status()
            print("\nSecure Remote Studio is configured.")
            print(f"Allowed identity(ies): {', '.join(config['allowed_users'])}")
            print(f"Studio stays bound locally: http://127.0.0.1:{args.port}")
            print(f"Tailscale address: {status.get('serve_url') or 'see output of tailscale serve status'}")
            print("Remote access starts in read mode; write actions require the PIN.")
            if not config.get("require_identity_for_read", True):
                print("⚠ Identity check disabled: every device in the tailnet has read access.")
            return 0
        if args.command == "status":
            payload = {"config": load_remote_config(root), "tailscale": tailscale_status()}
            if args.json:
                print(json.dumps(mask_secrets(payload), ensure_ascii=False, indent=2))
            else:
                print(f"Remote enabled: {payload['config']['enabled']}")
                print(f"Tailscale online: {payload['tailscale']['online']}")
                print(f"Address: {payload['tailscale'].get('serve_url') or '–'}")
            return 0
        if args.command == "check":
            payload = remote_self_check(root)
            if args.json:
                print(json.dumps(mask_secrets(payload), ensure_ascii=False, indent=2))
            else:
                for item in payload["checks"]:
                    print(f"[{'OK' if item['passed'] else 'ERROR'}] {item['name']}: {item['detail']}")
                print(f"Remote self-test: {'passed' if payload['ok'] else 'failed'}")
            return 0 if payload["ok"] else 1
        if args.command == "disable":
            disable_remote_access(root)
            if args.reset_serve:
                _reset_tailscale_serve()
            print("Remote access has been disabled in Godot Coder AI.")
            return 0
    except (RemoteAccessError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Error: {exc}")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
