from pathlib import Path

import pytest

from godot_coder.ui.paths import safe_child
from godot_coder.ui.services import write_dataset_file


def test_safe_child_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_child(tmp_path, "../outside.txt")


def test_dataset_writer_is_scoped_and_creates_backup(tmp_path: Path) -> None:
    (tmp_path / "data" / "raw").mkdir(parents=True)
    target = tmp_path / "data" / "raw" / "lesson.gd"
    target.write_text("extends Node\n", encoding="utf-8")

    result = write_dataset_file(tmp_path, "data/raw/lesson.gd", "extends Node2D\n")

    assert target.read_text(encoding="utf-8") == "extends Node2D\n"
    assert result["backup"] is not None
    assert (tmp_path / result["backup"]).exists()

    with pytest.raises(ValueError):
        write_dataset_file(tmp_path, "configs/unsafe.gd", "pass")
