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
