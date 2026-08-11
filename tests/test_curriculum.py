import json
import sys
from pathlib import Path

import pytest

from godot_coder.curriculum import _split_for, build_curriculum, main, parse_args


def test_curriculum_has_fixed_topics_and_splits(tmp_path: Path) -> None:
    root = tmp_path / "curriculum"
    manifest = build_curriculum(root)
    assert manifest["total_lessons"] == 192
    assert manifest["split_counts"] == {"train": 144, "val": 24, "test": 24}
    assert len(manifest["topic_counts"]) == 8
    assert (root / "project.godot").exists()
    assert len(list((root / "train").rglob("*.gd"))) == 144
    sample = next((root / "train").rglob("*.gd")).read_text(encoding="utf-8")
    assert sample.startswith("# curriculum: godot-coder-v0.3")
    assert "extends " in sample


def test_build_curriculum_raises_when_directory_not_empty(tmp_path: Path) -> None:
    root = tmp_path / "curriculum"
    root.mkdir()
    (root / "stale.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        build_curriculum(root)


def test_build_curriculum_overwrite_replaces(tmp_path: Path) -> None:
    root = tmp_path / "curriculum"
    manifest = build_curriculum(root)
    assert manifest["total_lessons"] == 192
    (root / "stale.txt").write_text("x", encoding="utf-8")
    rebuilt = build_curriculum(root, overwrite=True)
    assert rebuilt["total_lessons"] == 192
    assert not (root / "stale.txt").exists()


def test_split_boundaries() -> None:
    assert _split_for(1) == "train"
    assert _split_for(18) == "train"
    assert _split_for(19) == "val"
    assert _split_for(21) == "val"
    assert _split_for(22) == "test"
    assert _split_for(24) == "test"


def test_manifest_lesson_details(tmp_path: Path) -> None:
    manifest = build_curriculum(tmp_path / "curriculum")
    lessons = manifest["lessons"]
    assert len(lessons) == 192
    first = lessons[0]
    assert first["path"] == "train/01_basics/lesson_001.gd"
    assert first["split"] == "train"
    assert first["topic"] == "01_basics"
    assert first["lesson"] == 1
    assert first["bytes"] > 0
    assert manifest["topics"][0] == {"slug": "01_basics", "label": "Basics"}
    assert len(manifest["topics"]) == 8


def test_project_godot_uses_compatibility_renderer(tmp_path: Path) -> None:
    root = tmp_path / "curriculum"
    build_curriculum(root)
    content = (root / "project.godot").read_text(encoding="utf-8")
    assert "config/name=\"Godot Coder Curriculum v0.3\"" in content
    assert "gl_compatibility" in content


def test_parse_args_defaults(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["curriculum"])
    args = parse_args()
    assert Path(args.output).name == "curriculum_v03"
    assert args.overwrite is False


def test_parse_args_override(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["curriculum", "--output", "out", "--overwrite"])
    args = parse_args()
    assert args.output == "out"
    assert args.overwrite is True


def test_main_prints_manifest_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_build(output, *, overwrite=False):
        captured["output"] = output
        captured["overwrite"] = overwrite
        return {"name": "n", "total_lessons": 1, "split_counts": {}, "topic_counts": {}}

    monkeypatch.setattr("godot_coder.curriculum.build_curriculum", fake_build)
    monkeypatch.setattr(sys, "argv", ["curriculum", "--output", str(tmp_path / "out"), "--overwrite"])
    main()
    out = json.loads(capsys.readouterr().out)
    assert out["total_lessons"] == 1
    assert captured["overwrite"] is True
