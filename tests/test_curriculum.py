from pathlib import Path

from godot_coder.curriculum import build_curriculum


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
