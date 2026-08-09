from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Iterable

from . import __version__
from .local_sources import _archive_preflight, inbox_path
from .progress_events import ProgressEmitter, mask_secrets

MAX_REMOTE_SOURCE_BYTES = 256 * 1024 * 1024
MAX_REDIRECTS = 5
TRUSTED_REMOTE_HOSTS = {
    "github.com",
    "codeload.github.com",
    "gitlab.com",
    "bitbucket.org",
}
_USER_AGENT = f"Godot-Coder-AI/{__version__} (+local private corpus import)"


class RemoteSourceError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _public_addresses(host: str, port: int, resolver: Callable[..., Iterable[Any]] = socket.getaddrinfo) -> list[str]:
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RemoteSourceError(f"Could not resolve host: {host}") from exc
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise RemoteSourceError(f"Invalid target address: {address}") from exc
        if not parsed.is_global:
            raise RemoteSourceError("Local, private or reserved target addresses are blocked for link imports.")
        addresses.append(str(parsed))
    if not addresses:
        raise RemoteSourceError(f"No public address found for {host}.")
    return sorted(set(addresses))


def validate_remote_url(
    value: str,
    *,
    resolver: Callable[..., Iterable[Any]] = socket.getaddrinfo,
) -> str:
    raw = value.strip()
    if len(raw) > 2048:
        raise RemoteSourceError("The URL is too long.")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.lower() != "https":
        raise RemoteSourceError("Remote imports only allow HTTPS links.")
    if parsed.username or parsed.password:
        raise RemoteSourceError("Credentials must not be part of the URL.")
    if parsed.query or parsed.fragment:
        raise RemoteSourceError("Query parameters and fragments are not allowed for safe link imports.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in TRUSTED_REMOTE_HOSTS:
        raise RemoteSourceError("Currently allowed: GitHub, GitLab and Bitbucket. Other hosts stay blocked.")
    port = parsed.port or 443
    if port != 443:
        raise RemoteSourceError("Only the HTTPS standard port 443 is allowed.")
    _public_addresses(host, port, resolver)
    normalized_path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urllib.parse.urlunsplit(("https", host, normalized_path, "", ""))


def remote_url_candidates(value: str, *, resolver: Callable[..., Iterable[Any]] = socket.getaddrinfo) -> list[str]:
    normalized = validate_remote_url(value, resolver=resolver)
    parsed = urllib.parse.urlsplit(normalized)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.hostname == "github.com" and len(parts) == 2:
        owner, repository = parts
        repository = repository.removesuffix(".git")
        return [
            f"https://github.com/{owner}/{repository}/archive/refs/heads/main.zip",
            f"https://github.com/{owner}/{repository}/archive/refs/heads/master.zip",
        ]
    if parsed.hostname == "gitlab.com" and len(parts) >= 2 and "-" not in parts:
        namespace = "/".join(parts[:-1])
        repository = parts[-1].removesuffix(".git")
        return [
            f"https://gitlab.com/{namespace}/{repository}/-/archive/main/{repository}-main.zip",
            f"https://gitlab.com/{namespace}/{repository}/-/archive/master/{repository}-master.zip",
        ]
    if parsed.hostname == "bitbucket.org" and len(parts) == 2:
        owner, repository = parts
        repository = repository.removesuffix(".git")
        return [
            f"https://bitbucket.org/{owner}/{repository}/get/main.zip",
            f"https://bitbucket.org/{owner}/{repository}/get/master.zip",
        ]
    return [normalized]


def safe_url_for_log(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def sanitize_archive_name(value: str, fallback: str = "remote-godot-project.zip") -> str:
    decoded = urllib.parse.unquote(value).strip()
    parsed = urllib.parse.urlsplit(decoded)
    candidate = parsed.path if parsed.scheme and parsed.netloc else decoded
    # Handle both POSIX and Windows client filenames independent of server OS.
    name = PureWindowsPath(candidate).name if "\\" in candidate else Path(candidate).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "-", name).strip(" .-")
    if not name:
        name = fallback
    if not name.lower().endswith(".zip"):
        name += ".zip"
    stem = Path(name).stem[:96].strip(" .-") or "remote-godot-project"
    return f"{stem}.zip"


def _unique_destination(folder: Path, name: str) -> Path:
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(2, 10_000):
        alternative = folder / f"{stem}-{index}{suffix}"
        if not alternative.exists():
            return alternative
    raise RemoteSourceError("No free file name found in the import folder.")


def validate_staged_zip(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size < 4:
        raise RemoteSourceError("The transferred file is empty or incomplete.")
    with path.open("rb") as handle:
        signature = handle.read(4)
    if signature[:2] != b"PK" or not zipfile.is_zipfile(path):
        raise RemoteSourceError("The link or upload does not contain a valid ZIP archive.")
    try:
        return _archive_preflight(path)
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        raise RemoteSourceError(str(exc)) from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def stage_uploaded_zip(project_root: Path, temporary_path: Path, filename: str) -> dict[str, Any]:
    preflight = validate_staged_zip(temporary_path)
    inbox = inbox_path(project_root)
    destination = _unique_destination(inbox, sanitize_archive_name(filename))
    os.replace(temporary_path, destination)
    digest = _sha256_file(destination)
    return {
        "name": destination.name,
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
        "sha256": digest,
        "archive_entries": preflight["entries"],
        "archive_uncompressed_bytes": preflight["uncompressed_bytes"],
    }


def download_remote_source(
    project_root: Path,
    url: str,
    *,
    opener: Any | None = None,
    resolver: Callable[..., Iterable[Any]] = socket.getaddrinfo,
    max_bytes: int = MAX_REMOTE_SOURCE_BYTES,
    emitter: ProgressEmitter | None = None,
) -> dict[str, Any]:
    progress = emitter or ProgressEmitter()
    candidates = remote_url_candidates(url, resolver=resolver)
    client = opener or urllib.request.build_opener(_NoRedirect())
    last_error: Exception | None = None
    staging_root = project_root / "data" / "local_sources" / ".remote_staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    progress.emit(
        "remote_source_started",
        phase="remote_link_validation",
        phase_label="Check remote link safely",
        phase_status="completed",
        source_url=safe_url_for_log(url),
        message="The remote link was checked against host, protocol and network rules.",
    )
    for candidate_index, initial_url in enumerate(candidates, start=1):
        current_url = initial_url
        redirects = 0
        while True:
            try:
                current_url = validate_remote_url(current_url, resolver=resolver)
                request = urllib.request.Request(current_url, headers={"User-Agent": _USER_AGENT, "Accept": "application/zip, application/octet-stream;q=0.9"})
                try:
                    response = client.open(request, timeout=30)
                except urllib.error.HTTPError as exc:
                    if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                        if redirects >= MAX_REDIRECTS:
                            raise RemoteSourceError("Too many redirects while downloading.") from exc
                        current_url = urllib.parse.urljoin(current_url, exc.headers["Location"])
                        redirects += 1
                        continue
                    if exc.code == 404 and candidate_index < len(candidates):
                        last_error = exc
                        break
                    raise RemoteSourceError(f"Remote server responded with HTTP {exc.code}.") from exc
                status = getattr(response, "status", 200)
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    response.close()
                    if not location or redirects >= MAX_REDIRECTS:
                        raise RemoteSourceError("Invalid or too many redirects during download.")
                    current_url = urllib.parse.urljoin(current_url, location)
                    redirects += 1
                    continue
                content_length = response.headers.get("Content-Length")
                total = int(content_length) if content_length and content_length.isdigit() else None
                if total is not None and total > max_bytes:
                    response.close()
                    raise RemoteSourceError(f"The download is larger than the limit of {max_bytes // 1024**2} MiB.")
                disposition = response.headers.get("Content-Disposition", "")
                filename_match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, flags=re.IGNORECASE)
                remote_name = filename_match.group(1) if filename_match else Path(urllib.parse.urlsplit(current_url).path).name
                target_name = sanitize_archive_name(remote_name)
                fd, temporary_name = tempfile.mkstemp(prefix="remote-", suffix=".zip.part", dir=staging_root)
                os.close(fd)
                temporary = Path(temporary_name)
                digest = hashlib.sha256()
                received = 0
                last_event_at = 0
                try:
                    with response, temporary.open("wb") as output:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > max_bytes:
                                raise RemoteSourceError(f"The download exceeds the limit of {max_bytes // 1024**2} MiB.")
                            output.write(chunk)
                            digest.update(chunk)
                            if received - last_event_at >= 4 * 1024 * 1024 or (total and received == total):
                                progress.emit(
                                    "remote_source_progress",
                                    phase="remote_download",
                                    phase_label="Download source to this PC",
                                    phase_status="running",
                                    bytes_received=received,
                                    bytes_total=total,
                                    overall_progress=(received / total) if total else None,
                                    source_url=safe_url_for_log(current_url),
                                    message=f"{received / 1024**2:.1f} MiB received on the PC.",
                                )
                                last_event_at = received
                    preflight = validate_staged_zip(temporary)
                    destination = _unique_destination(inbox_path(project_root), target_name)
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
                report = {
                    "format": "godot-coder-remote-source-download",
                    "format_version": 1,
                    "created_at": time.time(),
                    "source_url": safe_url_for_log(current_url),
                    "name": destination.name,
                    "path": str(destination),
                    "size_bytes": received,
                    "sha256": digest.hexdigest(),
                    "archive_entries": preflight["entries"],
                    "archive_uncompressed_bytes": preflight["uncompressed_bytes"],
                    "private_local_source": True,
                    "redistribution_allowed": False,
                }
                report_dir = project_root / "reports" / "remote_sources"
                report_dir.mkdir(parents=True, exist_ok=True)
                report_path = report_dir / f"download_{int(time.time())}_{digest.hexdigest()[:12]}.json"
                report_path.write_text(json.dumps(mask_secrets(report), ensure_ascii=False, indent=2), encoding="utf-8")
                progress.emit(
                    "remote_source_completed",
                    phase="remote_download",
                    phase_label="Download source to this PC",
                    phase_status="completed",
                    job_status="completed",
                    bytes_received=received,
                    bytes_total=total,
                    overall_progress=1.0,
                    source_name=destination.name,
                    message="The ZIP was checked and placed into the private import folder.",
                )
                print("REMOTE_SOURCE_SUMMARY_JSON=" + json.dumps(report, ensure_ascii=True))
                return report
            except (RemoteSourceError, OSError, urllib.error.URLError, ValueError) as exc:
                last_error = exc
                if candidate_index < len(candidates):
                    break
                raise RemoteSourceError(str(exc)) from exc
    raise RemoteSourceError(str(last_error or "The remote source could not be loaded."))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a private Godot ZIP source on the Studio PC.")
    parser.add_argument("--root", default=".")
    parser.add_argument("download")
    parser.add_argument("--url", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        download_remote_source(Path(args.root).expanduser().resolve(), args.url)
        return 0
    except (RemoteSourceError, OSError) as exc:
        print(f"Remote source error: {mask_secrets(str(exc))}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
