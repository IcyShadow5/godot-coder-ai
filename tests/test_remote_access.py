from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from godot_coder.remote_access import (
    MAX_UNLOCK_ATTEMPTS,
    RemoteAccessError,
    RemoteAccessManager,
    configure_remote_access,
    hash_pin,
    load_remote_config,
    remote_audit_path,
    request_identity,
    verify_pin,
)
from godot_coder.ui.server import create_app


REMOTE_HEADERS = {
    "Tailscale-User-Login": "Owner@Example.com",
    "Tailscale-User-Name": "Owner Device",
}


def _project(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")


def test_pin_hash_roundtrip_and_plaintext_is_not_persisted(tmp_path: Path) -> None:
    salt, digest = hash_pin("123456")
    assert verify_pin("123456", salt, digest)
    assert not verify_pin("654321", salt, digest)

    configure_remote_access(tmp_path, allowed_users=["Owner@Example.com"], pin="123456")
    raw = (tmp_path / "data" / "studio" / "remote_access.json").read_text(encoding="utf-8")
    assert "123456" not in raw
    config = load_remote_config(tmp_path)
    assert config["enabled"] is True
    assert config["allowed_users"] == ["owner@example.com"]


def test_identity_headers_are_decoded_and_normalized() -> None:
    identity = request_identity({
        "tailscale-user-login": "OWNER@Example.com ",
        "tailscale-user-name": "=?utf-8?q?M=C3=BCller?=",
    })
    assert identity["login"] == "owner@example.com"
    assert identity["display_name"] == "Müller"


def test_remote_read_only_unlock_csrf_and_lock_flow(tmp_path: Path) -> None:
    _project(tmp_path)
    configure_remote_access(tmp_path, allowed_users=["owner@example.com"], pin="123456")
    app = create_app(tmp_path)
    with TestClient(app, base_url="https://studio.example.test") as client:
        status = client.get("/api/remote/status", headers=REMOTE_HEADERS)
        assert status.status_code == 200
        assert status.json()["can_read"] is True
        assert status.json()["can_write"] is False

        assert client.get("/api/overview", headers=REMOTE_HEADERS).status_code == 200
        locked = client.post("/api/jobs/corpus/local-import", headers=REMOTE_HEADERS, json={"confirm_owned": True})
        assert locked.status_code == 423

        unlocked = client.post("/api/remote/unlock", headers=REMOTE_HEADERS, json={"pin": "123456"})
        assert unlocked.status_code == 200
        csrf = unlocked.json()["csrf_token"]
        assert csrf
        # A UI reload can recover the CSRF token from the authenticated, no-store status response.
        resumed = client.get("/api/remote/status", headers=REMOTE_HEADERS)
        assert resumed.json()["csrf_token"] == csrf

        missing_csrf = client.post("/api/jobs/corpus/local-import", headers=REMOTE_HEADERS, json={"confirm_owned": False})
        assert missing_csrf.status_code == 403

        authorized_headers = {**REMOTE_HEADERS, "X-Godot-Coder-CSRF": csrf}
        authorized = client.post("/api/jobs/corpus/local-import", headers=authorized_headers, json={"confirm_owned": False})
        assert authorized.status_code == 400  # request reached the endpoint, then failed ownership confirmation

        locked_again = client.post("/api/remote/lock", headers=authorized_headers, json={})
        assert locked_again.status_code == 200
        assert client.get("/api/remote/status", headers=REMOTE_HEADERS).json()["can_write"] is False


def test_unlisted_tailscale_identity_is_denied(tmp_path: Path) -> None:
    _project(tmp_path)
    configure_remote_access(tmp_path, allowed_users=["owner@example.com"], pin="123456")
    with TestClient(create_app(tmp_path), base_url="https://studio.example.test") as client:
        response = client.get("/api/overview", headers={"Tailscale-User-Login": "intruder@example.com"})
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "intruder@example.com" in detail
    assert "owner@example.com" not in detail.lower()  # allowlist must not be leaked


def test_unlock_rate_limit_and_secret_masked_audit(tmp_path: Path) -> None:
    configure_remote_access(tmp_path, allowed_users=["owner@example.com"], pin="123456")
    manager = RemoteAccessManager(tmp_path)
    for _ in range(MAX_UNLOCK_ATTEMPTS):
        with pytest.raises(RemoteAccessError, match="wrong"):
            manager.unlock(REMOTE_HEADERS, "000000")
    with pytest.raises(RemoteAccessError, match="Too many failed attempts"):
        manager.unlock(REMOTE_HEADERS, "123456")

    manager.audit("test", identity="owner@example.com", message="api_key=super-secret-value")
    audit = remote_audit_path(tmp_path).read_text(encoding="utf-8")
    assert "super-secret-value" not in audit
    assert "[REDACTED]" in audit


def test_expired_remote_session_loses_write_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_remote_access(tmp_path, allowed_users=["owner@example.com"], pin="123456", session_ttl_seconds=300)
    manager = RemoteAccessManager(tmp_path)
    session = manager.unlock(REMOTE_HEADERS, "123456")
    monkeypatch.setattr(time, "time", lambda: session.expires_at + 1)
    denial = manager.authorize(
        method="POST",
        path="/api/jobs/corpus/local-import",
        headers={**{k.lower(): v for k, v in REMOTE_HEADERS.items()}, "x-godot-coder-csrf": session.csrf_token},
        cookies={"godot_coder_remote_session": session.token},
    )
    assert denial is not None
    assert denial[0] == 423


def _zip_payload() -> bytes:
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("demo/project.godot", "config_version=5\n")
        archive.writestr("demo/main.gd", "extends Node\n")
    return buffer.getvalue()


def test_remote_upload_requires_session_and_stages_zip(tmp_path: Path) -> None:
    _project(tmp_path)
    configure_remote_access(tmp_path, allowed_users=["owner@example.com"], pin="123456")
    app = create_app(tmp_path)
    with TestClient(app, base_url="https://studio.example.test") as client:
        locked = client.post(
            "/api/remote/sources/upload?filename=phone.zip&confirm_owned=true",
            headers={**REMOTE_HEADERS, "Content-Type": "application/octet-stream"},
            content=_zip_payload(),
        )
        assert locked.status_code == 423
        unlocked = client.post("/api/remote/unlock", headers=REMOTE_HEADERS, json={"pin": "123456"})
        csrf = unlocked.json()["csrf_token"]
        uploaded = client.post(
            "/api/remote/sources/upload?filename=phone.zip&confirm_owned=true",
            headers={**REMOTE_HEADERS, "X-Godot-Coder-CSRF": csrf, "Content-Type": "application/octet-stream"},
            content=_zip_payload(),
        )
    assert uploaded.status_code == 200
    assert uploaded.json()["name"] == "phone.zip"
    assert (tmp_path / "data" / "local_sources" / "inbox" / "phone.zip").is_file()


def test_remote_link_endpoint_starts_pc_side_download_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path)
    configure_remote_access(tmp_path, allowed_users=["owner@example.com"], pin="123456")
    app = create_app(tmp_path)
    captured: dict[str, object] = {}

    def fake_start(kind: str, args: list[str], **kwargs):
        captured.update({"kind": kind, "args": args})
        return {"id": "remote123", "kind": kind, "status": "starting"}

    app.state.jobs.start = fake_start
    monkeypatch.setattr("godot_coder.ui.routers.remote.validate_remote_url", lambda value: value)
    with TestClient(app, base_url="https://studio.example.test") as client:
        unlocked = client.post("/api/remote/unlock", headers=REMOTE_HEADERS, json={"pin": "123456"})
        headers = {**REMOTE_HEADERS, "X-Godot-Coder-CSRF": unlocked.json()["csrf_token"]}
        response = client.post(
            "/api/jobs/remote/source-download",
            headers=headers,
            json={"url": "https://github.com/example/project", "confirm_owned": True},
        )
    assert response.status_code == 200
    assert captured["kind"] == "remote-source-download"
    assert captured["args"][-2:] == ["--url", "https://github.com/example/project"]


def test_tailscale_status_and_serve_use_current_cli_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    import godot_coder.remote_access as remote

    commands: list[list[str]] = []
    fake_executable = Path("/usr/bin/tailscale")
    monkeypatch.setattr(remote, "find_tailscale_cli", lambda: fake_executable)

    def fake_run(command, **kwargs):
        commands.append([str(value) for value in command])
        if command[1:3] == ["status", "--json"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "BackendState": "Running",
                    "Self": {"DNSName": "workstation.tailnet.ts.net.", "HostName": "workstation", "Online": True},
                    "CurrentTailnet": {"Name": "example.ts.net"},
                }),
                stderr="",
            )
        if command[1:4] == ["serve", "status", "--json"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"Web": {"workstation.tailnet.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}}}),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="Available within your tailnet", stderr="")

    monkeypatch.setattr(remote.subprocess, "run", fake_run)
    status = remote.tailscale_status()
    configured = remote.configure_tailscale_serve(8765)
    assert status["online"] is True
    assert status["serve_url"] == "https://workstation.tailnet.ts.net"
    expected_executable = str(fake_executable)
    assert commands[0] == [expected_executable, "status", "--json"]
    assert commands[1] == [expected_executable, "serve", "--bg", "http://127.0.0.1:8765"]
    assert commands[2] == [expected_executable, "serve", "status", "--json"]
    assert configured["configured"] is True


def test_tailscale_commands_preserve_windows_executable_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import PureWindowsPath
    from types import SimpleNamespace
    import godot_coder.remote_access as remote

    commands: list[list[str]] = []
    fake_executable = PureWindowsPath(r"C:\Program Files\Tailscale\tailscale.exe")
    monkeypatch.setattr(remote, "find_tailscale_cli", lambda: fake_executable)

    def fake_run(command, **kwargs):
        commands.append([str(value) for value in command])
        if command[1:3] == ["status", "--json"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "BackendState": "Running",
                    "Self": {"DNSName": "workstation.tailnet.ts.net.", "HostName": "workstation", "Online": True},
                    "CurrentTailnet": {"Name": "example.ts.net"},
                }),
                stderr="",
            )
        if command[1:4] == ["serve", "status", "--json"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"Web": {"workstation.tailnet.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}}}),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="Available within your tailnet", stderr="")

    monkeypatch.setattr(remote.subprocess, "run", fake_run)
    remote.tailscale_status()
    remote.configure_tailscale_serve(8765)

    expected_executable = str(fake_executable)
    assert commands[0] == [expected_executable, "status", "--json"]
    assert commands[1] == [expected_executable, "serve", "--bg", "http://127.0.0.1:8765"]
    assert commands[2] == [expected_executable, "serve", "status", "--json"]


def test_denied_remote_response_has_no_store_and_security_headers(tmp_path: Path) -> None:
    _project(tmp_path)
    configure_remote_access(tmp_path, allowed_users=["owner@example.com"], pin="123456")
    with TestClient(create_app(tmp_path), base_url="https://studio.example.test") as client:
        response = client.post(
            "/api/jobs/corpus/local-import",
            headers=REMOTE_HEADERS,
            json={"confirm_owned": True},
        )
    assert response.status_code == 423
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_tagged_tailscale_device_without_identity_header_is_not_treated_as_local(tmp_path: Path) -> None:
    _project(tmp_path)
    configure_remote_access(tmp_path, allowed_users=["owner@example.com"], pin="123456")
    tagged_headers = {"Host": "workstation.example-tailnet.ts.net"}
    with TestClient(create_app(tmp_path), base_url="https://workstation.example-tailnet.ts.net") as client:
        status = client.get("/api/remote/status", headers=tagged_headers)
        denied = client.get("/api/overview", headers=tagged_headers)
    assert status.status_code == 200
    assert status.json()["is_remote"] is True
    assert status.json()["identity"]["login"] is None
    assert status.json()["can_read"] is False
    assert denied.status_code == 403
    assert "No Tailscale identity received" in denied.json()["detail"]


def test_tailnet_fallback_allows_read_without_identity(tmp_path: Path) -> None:
    _project(tmp_path)
    configure_remote_access(tmp_path, allowed_users=["owner@example.com"], pin="123456", require_identity_for_read=False)
    tagged_headers = {"Host": "workstation.example-tailnet.ts.net"}
    with TestClient(create_app(tmp_path), base_url="https://workstation.example-tailnet.ts.net") as client:
        status = client.get("/api/remote/status", headers=tagged_headers)
        overview = client.get("/api/overview", headers=tagged_headers)
        locked = client.post("/api/jobs/corpus/local-import", headers=tagged_headers, json={"confirm_owned": True})
        unlocked = client.post("/api/remote/unlock", headers=tagged_headers, json={"pin": "123456"})
        csrf = unlocked.json()["csrf_token"]
        authorized_headers = {**tagged_headers, "X-Godot-Coder-CSRF": csrf}
        authorized = client.post("/api/jobs/corpus/local-import", headers=authorized_headers, json={"confirm_owned": False})
    assert status.json()["can_read"] is True
    assert status.json()["can_write"] is False
    assert status.json()["tailnet_allowed"] is True
    assert overview.status_code == 200
    assert locked.status_code == 423
    assert unlocked.status_code == 200
    assert authorized.status_code == 400  # reached endpoint, ownership not confirmed
