from __future__ import annotations

import io
import json
import socket
import urllib.error
import zipfile
from pathlib import Path
from typing import Any

import pytest

from godot_coder.remote_sources import (
    RemoteSourceError,
    download_remote_source,
    remote_url_candidates,
    sanitize_archive_name,
    stage_uploaded_zip,
    validate_remote_url,
)


def public_resolver(host: str, port: int, **kwargs: Any):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def private_resolver(host: str, port: int, **kwargs: Any):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]


def _zip_bytes(name: str = "demo/project.godot", content: str = "config_version=5\n") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, content)
        archive.writestr("demo/main.gd", "extends Node\n")
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, body: bytes, *, headers: dict[str, str] | None = None, status: int = 200) -> None:
        self._stream = io.BytesIO(body)
        self.headers = headers or {}
        self.status = status

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def close(self) -> None:
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class FakeOpener:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[str] = []

    def open(self, request, timeout: int = 30):
        self.requests.append(request.full_url)
        return self.response


@pytest.mark.parametrize("url", [
    "http://github.com/a/b/archive.zip",
    "https://user:pass@github.com/a/b.zip",
    "https://github.com/a/b.zip?token=secret",
    "https://example.com/a.zip",
    "https://github.com:444/a/b.zip",
])
def test_remote_url_rejects_unsafe_forms(url: str) -> None:
    with pytest.raises(RemoteSourceError):
        validate_remote_url(url, resolver=public_resolver)


def test_remote_url_rejects_private_dns_resolution() -> None:
    with pytest.raises(RemoteSourceError, match="private|reservierte|Lokale"):
        validate_remote_url("https://github.com/a/b.zip", resolver=private_resolver)


def test_github_repository_is_normalized_to_branch_archive_candidates() -> None:
    candidates = remote_url_candidates("https://github.com/Owner/Repo", resolver=public_resolver)
    assert candidates == [
        "https://github.com/Owner/Repo/archive/refs/heads/main.zip",
        "https://github.com/Owner/Repo/archive/refs/heads/master.zip",
    ]


def test_upload_staging_validates_zip_and_uses_safe_windows_filename(tmp_path: Path) -> None:
    temporary = tmp_path / "incoming.part"
    temporary.write_bytes(_zip_bytes())
    result = stage_uploaded_zip(tmp_path, temporary, r"C:\Users\Icy\My Project?.zip")
    destination = Path(result["path"])
    assert destination.is_file()
    assert destination.parent == tmp_path / "data" / "local_sources" / "inbox"
    assert destination.name == "My Project.zip"
    assert result["archive_entries"] == 2
    assert len(result["sha256"]) == 64


def test_upload_staging_rejects_zip_path_traversal(tmp_path: Path) -> None:
    temporary = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(temporary, "w") as archive:
        archive.writestr("../escape.gd", "extends Node")
    with pytest.raises(RemoteSourceError, match="Unsicherer ZIP-Pfad"):
        stage_uploaded_zip(tmp_path, temporary, "unsafe.zip")


def test_server_side_download_stages_private_zip_and_writes_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = _zip_bytes()
    opener = FakeOpener(FakeResponse(body, headers={
        "Content-Length": str(len(body)),
        "Content-Disposition": 'attachment; filename="demo.zip"',
    }))
    result = download_remote_source(
        tmp_path,
        "https://github.com/Owner/Repo/archive/refs/heads/main.zip",
        opener=opener,
        resolver=public_resolver,
    )
    assert Path(result["path"]).is_file()
    assert result["private_local_source"] is True
    assert result["redistribution_allowed"] is False
    assert result["source_url"].startswith("https://github.com/")
    reports = list((tmp_path / "reports" / "remote_sources").glob("download_*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text(encoding="utf-8"))["sha256"] == result["sha256"]
    assert "GCAI_EVENT" in capsys.readouterr().out


def test_download_rejects_declared_oversize_before_writing(tmp_path: Path) -> None:
    opener = FakeOpener(FakeResponse(b"PK\x03\x04", headers={"Content-Length": "999999"}))
    with pytest.raises(RemoteSourceError, match="größer"):
        download_remote_source(
            tmp_path,
            "https://github.com/a/b/archive.zip",
            opener=opener,
            resolver=public_resolver,
            max_bytes=1024,
        )


def test_sanitize_archive_name_is_stable_for_long_or_empty_names() -> None:
    assert sanitize_archive_name("https://host/") == "remote-godot-project.zip"
    value = sanitize_archive_name("x" * 300 + ".zip")
    assert value.endswith(".zip")
    assert len(Path(value).stem) <= 96


def test_gitlab_and_bitbucket_repository_home_links_get_archive_candidates() -> None:
    assert remote_url_candidates("https://gitlab.com/group/subgroup/project", resolver=public_resolver) == [
        "https://gitlab.com/group/subgroup/project/-/archive/main/project-main.zip",
        "https://gitlab.com/group/subgroup/project/-/archive/master/project-master.zip",
    ]
    assert remote_url_candidates("https://bitbucket.org/owner/project", resolver=public_resolver) == [
        "https://bitbucket.org/owner/project/get/main.zip",
        "https://bitbucket.org/owner/project/get/master.zip",
    ]
