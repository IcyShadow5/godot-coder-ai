from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from godot_coder import corpus
from godot_coder.ui.paths import safe_child


# --- safe_child hardening ----------------------------------------------


def test_safe_child_rejects_absolute_paths(tmp_path: Path) -> None:
    absolute = str(tmp_path / "outside.txt")  # absolute on both POSIX and Windows
    with pytest.raises(ValueError, match="absolute"):
        safe_child(tmp_path, absolute)


def test_safe_child_rejects_backslash_traversal(tmp_path: Path) -> None:
    # Windows treats backslashes as separators, so this escapes and must raise.
    # POSIX treats them as plain filename characters, so the result is a
    # harmless name inside the root - the important part is that it never
    # escapes. Assert the right behavior per platform instead of pretending
    # backslash traversal exists everywhere.
    if os.name == "nt":
        with pytest.raises(ValueError):
            safe_child(tmp_path, "..\\..\\pyproject.toml")
    else:
        result = safe_child(tmp_path, "..\\..\\pyproject.toml")
        assert result.is_relative_to(tmp_path.resolve())


def test_safe_child_requires_existing_when_requested(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        safe_child(tmp_path, "missing.txt", must_exist=True)
    (tmp_path / "present.txt").write_text("ok", encoding="utf-8")
    assert safe_child(tmp_path, "present.txt", must_exist=True) == (tmp_path / "present.txt").resolve()


def test_safe_child_accepts_nested_relative_paths(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "file.gd"
    nested.parent.mkdir(parents=True)
    nested.write_text("extends Node\n", encoding="utf-8")
    assert safe_child(tmp_path, "a/b/file.gd") == nested.resolve()


# --- corpus archive zip-slip guard --------------------------------------


def _zip_with_entries(entries: list[tuple[str, str]], directory: Path) -> Path:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    path = directory / "unsafe.zip"
    path.write_bytes(buffer.getvalue())
    return path


def test_github_archive_rejects_parent_traversal_entry(tmp_path: Path) -> None:
    archive = _zip_with_entries([("repo-root/ok.gd", "extends Node\n"), ("repo-root/../escape.gd", "evil")], tmp_path)
    destination = tmp_path / "checkout"
    with pytest.raises(ValueError, match="unsafe archive path"):
        corpus._safe_extract_github_archive(archive, destination, {})


def test_github_archive_rejects_absolute_entry(tmp_path: Path) -> None:
    # An absolute entry introduces a second root, so the root-layout guard
    # rejects the archive before the per-entry check runs. Both reject it.
    archive = _zip_with_entries([("repo-root/ok.gd", "extends Node\n"), ("/etc/escape.gd", "evil")], tmp_path)
    destination = tmp_path / "checkout"
    with pytest.raises(ValueError, match="unsafe archive path|unexpected root layout"):
        corpus._safe_extract_github_archive(archive, destination, {})


def test_github_archive_rejects_multiple_roots(tmp_path: Path) -> None:
    archive = _zip_with_entries([("root-a/a.gd", "extends Node\n"), ("root-b/b.gd", "extends Node\n")], tmp_path)
    destination = tmp_path / "checkout"
    with pytest.raises(ValueError, match="unexpected root layout"):
        corpus._safe_extract_github_archive(archive, destination, {})


def test_github_archive_extracts_clean_single_root(tmp_path: Path) -> None:
    archive = _zip_with_entries(
        [("repo-root/project.godot", "[application]\n"), ("repo-root/scripts/main.gd", "extends Node\n")], tmp_path
    )
    destination = tmp_path / "checkout"
    corpus._safe_extract_github_archive(archive, destination, {})
    assert (destination / "project.godot").exists()
    assert (destination / "scripts" / "main.gd").exists()
    assert not (destination.parent / "escape.gd").exists()
