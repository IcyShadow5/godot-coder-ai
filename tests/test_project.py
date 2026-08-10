"""Tests for project-root detection — looks_like_project, find_project_root, resolve/relative paths."""

from pathlib import Path

import pytest

from godot_coder.project import (
    find_project_root,
    looks_like_project,
    project_relative,
    resolve_project_path,
)


def _make_project(root: Path) -> None:
    (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (root / "configs").mkdir()
    (root / "src").mkdir()


def test_looks_like_project_true_with_all_markers(tmp_path: Path) -> None:
    _make_project(tmp_path)
    assert looks_like_project(tmp_path)


def test_looks_like_project_false_when_marker_missing(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "src").rmdir()
    assert not looks_like_project(tmp_path)


def test_looks_like_project_false_for_file(tmp_path: Path) -> None:
    _make_project(tmp_path)
    assert not looks_like_project(tmp_path / "pyproject.toml")


def test_find_project_root_explicit(tmp_path: Path) -> None:
    _make_project(tmp_path)
    assert find_project_root(explicit=tmp_path) == tmp_path.resolve()


def test_find_project_root_explicit_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        find_project_root(explicit=tmp_path / "nope")


def test_find_project_root_searches_upward(tmp_path: Path) -> None:
    _make_project(tmp_path)
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_project_root(start=nested) == tmp_path.resolve()


def test_resolve_project_path_relative_and_absolute(tmp_path: Path) -> None:
    _make_project(tmp_path)
    relative = resolve_project_path(tmp_path, "configs/tiny.yaml")
    assert relative == (tmp_path / "configs" / "tiny.yaml").resolve()
    absolute = tmp_path / "other.yaml"
    assert resolve_project_path(tmp_path, absolute) == absolute.resolve()


def test_project_relative_inside_root(tmp_path: Path) -> None:
    _make_project(tmp_path)
    target = tmp_path / "configs" / "tiny.yaml"
    assert project_relative(target, tmp_path) == "configs/tiny.yaml"


def test_project_relative_outside_root(tmp_path: Path) -> None:
    _make_project(tmp_path)
    outside = tmp_path.parent / "elsewhere.yaml"
    assert project_relative(outside, tmp_path) == str(outside.resolve())
