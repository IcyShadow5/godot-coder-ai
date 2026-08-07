from __future__ import annotations

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

from godot_coder.ui.server import create_app


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "godot_coder" / "ui" / "static"


class IdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        value = dict(attrs).get("id")
        if value:
            self.ids.add(value)


def test_remote_mobile_ui_assets_and_pwa_manifest() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    parser = IdParser()
    parser.feed(html)
    required = {
        "view-remote", "remote-state-card", "remote-unlock-form", "remote-source-url",
        "remote-source-file", "remote-inbox-list", "install-pwa", "remote-access-banner",
    }
    assert required <= parser.ids
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    assert (STATIC / "app-icon.svg").is_file()
    assert (STATIC / "app-icon-192.png").is_file()
    assert (STATIC / "app-icon-512.png").is_file()
    assert {icon["sizes"] for icon in manifest["icons"]} >= {"192x192", "512x512"}


def test_service_worker_never_caches_api_or_private_job_data() -> None:
    service_worker = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert 'url.pathname.startsWith("/api/")' in service_worker
    assert "caches.open" in service_worker
    assert "manifest.webmanifest" in service_worker
    subprocess.run(["node", "--check", str(STATIC / "sw.js")], check=True, capture_output=True, text=True)


def test_remote_javascript_and_responsive_mobile_css_are_present() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    for function in [
        "refreshRemote", "runRemoteSelfCheck", "unlockRemote", "lockRemote", "startRemoteSourceDownload",
        "uploadRemoteSource", "applyRemoteWritePolicy", "registerPwa",
    ]:
        assert f"function {function}" in javascript or f"async function {function}" in javascript
    assert "X-Godot-Coder-CSRF" in javascript
    assert "@media (max-width: 760px)" in css
    assert ".main-nav" in css
    assert "position:fixed" in css
    assert ".remote-readonly" in css
    subprocess.run(["node", "--check", str(STATIC / "app.js")], check=True, capture_output=True, text=True)


def test_manifest_and_service_worker_routes_have_security_headers(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    with TestClient(create_app(tmp_path)) as client:
        manifest = client.get("/manifest.webmanifest")
        worker = client.get("/sw.js")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert worker.status_code == 200
    assert worker.headers["content-type"].startswith("application/javascript")
    assert worker.headers["x-frame-options"] == "DENY"


def test_remote_self_check_controls_are_present() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="remote-self-check"' in html
    assert 'id="remote-self-check-result"' in html
    assert "http://127.0.0.1:8765" in html
