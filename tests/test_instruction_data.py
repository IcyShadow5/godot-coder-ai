import json
from pathlib import Path

from godot_coder.instruction_data import build_instruction_dataset


SCRIPT = '''extends Node\n\nfunc spend_energy(amount: float) -> void:\n\tif amount <= 0.0:\n\t\treturn\n\tenergy = maxf(0.0, energy - amount)\n'''


def test_instruction_foundation_is_deterministic_and_split_aware(tmp_path: Path) -> None:
    audited = tmp_path / "data" / "corpus" / "audited"
    for split in ("train", "val", "test"):
        folder = audited / split / "demo"
        folder.mkdir(parents=True)
        (folder / f"{split}.gd").write_text(SCRIPT, encoding="utf-8")
    first = build_instruction_dataset(tmp_path)
    second = build_instruction_dataset(tmp_path)
    assert first["fingerprint"] == second["fingerprint"]
    assert first["counts"] == {"train": 2, "val": 2, "test": 2}
    assert first["total_tasks"] == 6
    assert first["training_ready"] is False
    records = [json.loads(line) for line in (tmp_path / "data" / "instructions" / "v07" / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {item["task_type"] for item in records} == {"function_completion", "syntax_repair"}
    assert all(item["output"].startswith("func spend_energy") for item in records)


MULTI_FUNC_SCRIPT = """extends Node\n
func one() -> int:\n
\treturn 1\n
func two() -> int:\n
\treturn 2\n
func three() -> int:\n
\treturn 3\n
func four() -> int:\n
\treturn 4\n
"""


def test_multi_function_file_contributes_beyond_first_function(tmp_path: Path) -> None:
    """The per-file cap must span all functions, not just the first one.

    Regression for the bug where the loop broke after the first function that
    produced a task, silently discarding the rest of the file.
    """
    audited = tmp_path / "data" / "corpus" / "audited" / "train" / "demo"
    audited.mkdir(parents=True)
    (audited / "multi.gd").write_text(MULTI_FUNC_SCRIPT, encoding="utf-8")

    # Cap of 4: the file has 4 functions, each yielding up to 2 candidates.
    # With the bug, only function `one` was ever seen (2 tasks). The fixed
    # loop walks functions until the file cap is reached.
    report = build_instruction_dataset(tmp_path, max_tasks_per_file=4)
    assert report["counts"]["train"] == 4
    records = [json.loads(line) for line in (tmp_path / "data" / "instructions" / "v07" / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    inputs = "\n".join(item["input"] for item in records)
    for expected in ("func one", "func two", "func three", "func four"):
        assert expected in inputs, f"expected {expected} to appear in the generated tasks"
