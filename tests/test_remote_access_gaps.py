from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from godot_coder.remote_access import (
    MAX_UNLOCK_ATTEMPTS,
    RemoteAccessError,
    RemoteAccessManager,
    SESSION_COOKIE,
    configure_remote_access,
    disable_remote_access,
    hash_pin,
    load_remote_config,
    remote_config_path,
    request_is_remote,
    tailscale_serve_status,
    tailscale_status,
    verify_pin,
)

REMOTE_HEADERS = {"Tailscale-User-Login": "Owner@Example.com"}


def _manager(tmp_path: Path, *, require_identity_for_read: bool = True) -> RemoteAccessManager:
    configure_remote_access(
        tmp_path,
        allowed_users=["owner@example.com"],
        pin="123456",
        require_identity_for_read=require_identity_for_read,
    )
    return RemoteAccessManager(tmp_path)


# --- PIN hashing validation --------------------------------------------


def test_hash_pin_rejects_invalid_length_and_non_digit_pins() -> None:
    for invalid in ("12345", "1234567890123", "12345a", "12 4567", "-123456"):
        with pytest.raises(ValueError, match="6 to 12 digits"):
            hash_pin(invalid)


def test_verify_pin_returns_false_for_corrupted_salt_or_digest() -> None:
    salt, digest = hash_pin("123456")
    bad_salt = base64.b64encode(b"corrupt").decode("ascii")
    bad_digest = base64.b64encode(b"corrupt").decode("ascii")
    assert verify_pin("123456", bad_salt, digest) is False
    assert verify_pin("123456", salt, bad_digest) is False
    assert verify_pin("123456", "not-base64!!", "not-base64!!") is False


# --- config loading hardening -------------------------------------------


def test_load_remote_config_raises_on_corrupt_json(tmp_path: Path) -> None:
    path = remote_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(RemoteAccessError, match="could not be read"):
        load_remote_config(tmp_path)


def test_load_remote_config_applies_v2_defaults_and_clamps_values(tmp_path: Path) -> None:
    # Write a v2-era file directly (no require_identity_for_read key) with
    # out-of-range values so the load-time defaults and clamps are exercised.
    path = remote_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "format": "godot-coder-remote-access",
            "format_version": 2,
            "enabled": True,
            "allowed_users": ["owner@example.com"],
            "session_ttl_seconds": 99999,
            "port": 99999,
            "pin_salt": "x",
            "pin_hash": "y",
            "configured_at": "2026-01-01T00:00:00Z",
        }),
        encoding="utf-8",
    )

    config = load_remote_config(tmp_path)
    assert config["require_identity_for_read"] is True  # v2 default
    assert config["session_ttl_seconds"] == 86_400  # clamped down
    assert config["port"] == 65_535  # clamped down

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["session_ttl_seconds"] = 10
    raw["port"] = -5  # truthy but out of range, so the clamp applies (0 would hit the or-default fallback)
    path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_remote_config(tmp_path)
    assert config["session_ttl_seconds"] == 300  # clamped up
    assert config["port"] == 1  # clamped up


def test_configure_remote_access_rejects_empty_allowlist_and_invalid_port(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="At least one allowed"):
        configure_remote_access(tmp_path, allowed_users=[], pin="123456")
    with pytest.raises(ValueError, match="port"):
        configure_remote_access(tmp_path, allowed_users=["owner@example.com"], pin="123456", port=0)
    with pytest.raises(ValueError, match="port"):
        configure_remote_access(tmp_path, allowed_users=["owner@example.com"], pin="123456", port=70_000)


def test_disable_remote_access_persists_disabled_flag(tmp_path: Path) -> None:
    _manager(tmp_path)
    config = disable_remote_access(tmp_path)
    assert config["enabled"] is False
    assert load_remote_config(tmp_path)["enabled"] is False
    raw = json.loads(remote_config_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["enabled"] is False
    assert "disabled_at" in raw


# --- authorize denial paths ---------------------------------------------


def test_authorize_denies_remote_writes_when_remote_disabled(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    disable_remote_access(tmp_path)
    denial = manager.authorize(
        method="GET",
        path="/api/overview",
        headers={k.lower(): v for k, v in REMOTE_HEADERS.items()},
        cookies={},
    )
    assert denial is not None
    assert denial[0] == 403
    assert "disabled" in denial[1].lower()


def test_authorize_rejects_wrong_csrf_token_even_with_valid_session(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = manager.unlock(REMOTE_HEADERS, "123456")
    headers = {k.lower(): v for k, v in REMOTE_HEADERS.items()}
    denial = manager.authorize(
        method="POST",
        path="/api/jobs/corpus/local-import",
        headers={**headers, "x-godot-coder-csrf": "wrong-token"},
        cookies={SESSION_COOKIE: session.token},
    )
    assert denial is not None
    assert denial[0] == 403
    assert "CSRF" in denial[1]


def test_authorize_allows_public_read_paths_without_identity(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    headers = {"host": "workstation.example-tailnet.ts.net"}  # remote, no identity
    for path in ("/", "/sw.js", "/manifest.webmanifest", "/static/app.js", "/api/remote/status"):
        denial = manager.authorize(method="GET", path=path, headers=headers, cookies={})
        assert denial is None, f"{path} should be readable remotely without identity"


def test_unlock_rate_limit_is_per_identity(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    for _ in range(MAX_UNLOCK_ATTEMPTS):
        with pytest.raises(RemoteAccessError, match="wrong"):
            manager.unlock(REMOTE_HEADERS, "000000")
    # A different identity is not blocked by the first identity's attempts.
    other = {"Tailscale-User-Login": "other@example.com"}
    with pytest.raises(RemoteAccessError, match="not allowed"):
        manager.unlock(other, "123456")


# --- tailscale CLI error paths ------------------------------------------


def test_tailscale_status_reports_errors_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    import godot_coder.remote_access as remote

    monkeypatch.setattr(remote, "find_tailscale_cli", lambda: Path("/usr/bin/tailscale"))

    def fail_run(command, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom: no tailnet")

    monkeypatch.setattr(remote.subprocess, "run", fail_run)
    status = tailscale_status()
    assert status["installed"] is True
    assert status["online"] is False
    assert "boom" in status["error"]

    def bad_json(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="not json at all", stderr="")

    monkeypatch.setattr(remote.subprocess, "run", bad_json)
    status = tailscale_status()
    assert status["online"] is False
    assert "no valid status JSON" in status["error"]


def test_tailscale_serve_status_falls_back_to_text_output(monkeypatch: pytest.MonkeyPatch) -> None:
    import godot_coder.remote_access as remote

    monkeypatch.setattr(remote, "find_tailscale_cli", lambda: Path("/usr/bin/tailscale"))

    def text_run(command, **kwargs):
        # Older clients print a human-readable table instead of JSON.
        return SimpleNamespace(
            returncode=0,
            stdout="Available within your tailnet\nhttps://workstation.tailnet.ts.net -> http://127.0.0.1:8765",
            stderr="",
        )

    monkeypatch.setattr(remote.subprocess, "run", text_run)
    status = tailscale_serve_status(8765)
    assert status["status_format"] == "text"
    assert status["configured"] is True


def test_tailscale_status_handles_timeout_without_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    import godot_coder.remote_access as remote

    monkeypatch.setattr(remote, "find_tailscale_cli", lambda: Path("/usr/bin/tailscale"))

    def timeout_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, timeout=5)

    monkeypatch.setattr(remote.subprocess, "run", timeout_run)
    status = tailscale_status()
    assert status["online"] is False
    assert "timed out" in status["error"].lower() or "TimeoutExpired" in status["error"]


# --- request classification ---------------------------------------------


def test_request_is_remote_normalizes_ipv6_brackets_and_case() -> None:
    assert request_is_remote({"host": "[::1]:8765"}) is False
    assert request_is_remote({"host": "127.0.0.1:8765"}) is False
    assert request_is_remote({"host": "MYBOX.EXAMPLE-TAILNET.TS.NET"}) is True
    assert request_is_remote({"host": "mybox.example-tailnet.ts.net.:443"}) is True
    assert request_is_remote({"host": "evil.com"}) is False
