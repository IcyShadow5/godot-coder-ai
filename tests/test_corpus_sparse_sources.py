from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

from godot_coder import corpus


def test_addons_are_only_admitted_for_explicit_addon_sources() -> None:
    path = Path("addons/plugin/main.gd")
    assert corpus._is_excluded(path, {}) is True
    assert corpus._is_excluded(path, {"allow_addons": True}) is False


def test_source_only_archive_skips_assets_and_excluded_addons(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("repo-root/LICENSE", "MIT License")
        handle.writestr("repo-root/project.godot", "[application]\n")
        handle.writestr("repo-root/scripts/main.gd", "extends Node\n")
        handle.writestr("repo-root/addons/copied/plugin.gd", "extends Node\n")
        handle.writestr("repo-root/assets/icon.png", b"PNG")
    destination = tmp_path / "checkout"
    corpus._safe_extract_github_archive(archive, destination, {
        "source_only": True,
        "allow_addons": False,
        "exclude_paths": ["addons"],
    })
    assert (destination / "LICENSE").exists()
    assert (destination / "project.godot").exists()
    assert (destination / "scripts" / "main.gd").exists()
    assert not (destination / "addons" / "copied" / "plugin.gd").exists()
    assert not (destination / "assets" / "icon.png").exists()


def test_source_only_checkout_uses_partial_and_sparse_clone(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        values = [str(item) for item in command]
        commands.append(values)
        if values[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(values, 0, "abc123\n", "")
        return subprocess.CompletedProcess(values, 0, "ok\n", "")

    monkeypatch.setattr(corpus, "_run", fake_run)
    target = tmp_path / "repo"
    result = corpus._checkout_source(target, {
        "url": "https://github.com/example/repo.git",
        "branch": "main",
        "source_only": True,
        "allow_addons": False,
        "exclude_paths": ["vendor"],
        "license": "MIT",
    }, update=False)

    assert result.returncode == 0
    fetch = next(command for command in commands if "fetch" in command)
    assert "--filter=blob:none" in fetch
    sparse = next(command for command in commands if command[:4] == ["git", "sparse-checkout", "set", "--no-cone"])
    assert "**/*.gd" in sparse
    assert "!**/addons/**" in sparse
