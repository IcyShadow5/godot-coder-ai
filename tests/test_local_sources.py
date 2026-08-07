from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from godot_coder.corpus import build_staging, load_registry, save_registry, verify_declared_license
from godot_coder.local_sources import (
    _ERROR_LINE_RE,
    _PROGRESS_LINE_RE,
    _safe_extract,
    import_inbox,
    inbox_path,
)


def _write_project(root: Path, name: str = "Private Demo") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.godot").write_text(
        f'config_version=5\n[application]\nconfig/name="{name}"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n',
        encoding="utf-8",
    )
    (root / "main.gd").write_text('extends Node\n\nfunc _ready() -> void:\n\tprint("private")\n', encoding="utf-8")
    (root / ".godot").mkdir()
    (root / ".godot" / "cache.bin").write_bytes(b"generated")
    (root / "addons" / "foreign").mkdir(parents=True)
    (root / "addons" / "foreign" / "plugin.gd").write_text("extends Node\n", encoding="utf-8")
    (root / "bundled.exe").write_bytes(b"MZ")


def test_safe_zip_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escape.txt", "no")
    with pytest.raises(ValueError, match="Unsicherer ZIP-Pfad"):
        _safe_extract(archive, tmp_path / "out")


def test_private_import_requires_ownership_confirmation(tmp_path: Path) -> None:
    inbox = inbox_path(tmp_path)
    _write_project(inbox / "project")
    with pytest.raises(ValueError, match="Bestätige"):
        import_inbox(tmp_path, ownership_confirmed=False)


def test_private_project_import_filters_generated_and_enables_after_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inbox = inbox_path(tmp_path)
    source = inbox / "private-project"
    _write_project(source)
    monkeypatch.setattr("godot_coder.local_sources._validate_project", lambda project: ("passed", None, "ok"))

    report = import_inbox(tmp_path, ownership_confirmed=True)
    assert report["summary"]["projects"] == 1
    assert report["summary"]["enabled"] == 1
    project = report["projects"][0]
    destination = Path(project["imported_path"])
    assert (destination / "main.gd").exists()
    assert not (destination / ".godot").exists()
    assert not (destination / "bundled.exe").exists()
    assert (destination / "addons" / "foreign" / "plugin.gd").exists()  # needed only for project validation

    local = [item for item in load_registry(tmp_path)["sources"] if item["catalog_tier"] == "local-private"]
    assert len(local) == 1
    assert local[0]["license"] == "LicenseRef-User-Owned-Private"
    assert local[0]["redistribution_allowed"] is False
    assert local[0]["split_policy"] == "train"
    assert local[0]["enabled"] is True

    manifest = build_staging(tmp_path)
    private_records = [record for record in manifest["records"] if record["source_id"] == local[0]["id"]]
    assert len(private_records) == 1
    assert private_records[0]["split"] == "train"
    assert all("addons/" not in record["original_path"] for record in private_records)
    license_manifest = json.loads((tmp_path / "data" / "corpus" / "LICENSE_MANIFEST.json").read_text(encoding="utf-8"))
    assert license_manifest["contains_private_sources"] is True


def test_cc_by_30_accepts_official_and_short_license_wording(tmp_path: Path) -> None:
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "LICENSE.txt").write_text("Creative Commons Legal Code\nAttribution 3.0 Unported", encoding="utf-8")
    assert verify_declared_license(repo, {"license": "CC-BY-3.0"})["verified"] is True
    (repo / "LICENSE.txt").write_text("Creative Commons Attribution 3.0", encoding="utf-8")
    assert verify_declared_license(repo, {"license": "CC-BY-3.0"})["verified"] is True


def test_scan_skips_enabled_source_that_is_not_ready(tmp_path: Path) -> None:
    ready = {
        "id": "ready-source", "title": "Ready", "description": "test",
        "url": "https://example.invalid/ready.git", "branch": "main", "kind": "godot_projects",
        "license": "MIT", "attribution": "Test", "enabled": True,
    }
    missing = {
        "id": "missing-source", "title": "Missing", "description": "test",
        "url": "https://example.invalid/missing.git", "branch": "main", "kind": "godot_projects",
        "license": "MIT", "attribution": "Test", "enabled": True,
    }
    save_registry(tmp_path, [ready, missing])
    root = tmp_path / "data" / "corpus" / "downloads" / "ready-source"
    root.mkdir(parents=True)
    (root / "LICENSE").write_text(
        'Permission is hereby granted, free of charge.\nTHE SOFTWARE IS PROVIDED "AS IS".', encoding="utf-8"
    )
    (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (root / "main.gd").write_text("extends Node\n\nfunc _ready() -> void:\n\tprint(1)\n", encoding="utf-8")
    (root / ".godot-coder-source.json").write_text(json.dumps({
        "url": ready["url"], "ref": "main", "commit": "abc",
    }), encoding="utf-8")

    manifest = build_staging(tmp_path)
    assert manifest["records"]
    assert manifest["unavailable_sources"][0]["id"] == "missing-source"


def test_error_line_re_matches_godot_error_output() -> None:
    """_ERROR_LINE_RE must detect Godot error/parse/resource failures and
    _PROGRESS_LINE_RE must detect import progress markers so the error-rate
    abort can reset its counter."""
    # Error lines that should increment the counter
    for line in [
        "ERROR: Failed loading resource: res://foo.png",
        "SCRIPT ERROR: Parse Error: Preload file missing",
        "  Parse Error: Something went wrong",
        "WARNING: invalid UID: uid://abc123",
        "ERROR: can't load script",
        "Attempt to open script resulted in error File not found",
        "ERROR: Resource file not found: res://",
    ]:
        assert _ERROR_LINE_RE.search(line), f"should match error: {line[:80]}"

    # Progress lines that should reset the counter
    for line in [
        "[  16% ] first_scan_filesystem | Loading global class names...",
        "[  50% ] first_scan_filesystem | Creating autoload scripts...",
        "[  83% ] first_scan_filesystem | Starting file scan...",
        "importing assets",
    ]:
        assert _PROGRESS_LINE_RE.search(line), f"should match progress: {line[:80]}"

    # Non-error, non-progress lines should not match the error RE
    for line in [
        "Godot Engine v4.7.stable.mono.official",
        "OpenGL API 3.3.0 - Build 10.18.10.5161",
    ]:
        assert not _ERROR_LINE_RE.search(line), f"should NOT match error: {line[:80]}"

    # Line that contains both error AND progress — progress takes priority
    mixed = "[  16% ] first_scan_filesystem | ERROR: Some warning"
    assert _ERROR_LINE_RE.search(mixed)
    assert _PROGRESS_LINE_RE.search(mixed), "progress marker must be detectable even in error lines"
