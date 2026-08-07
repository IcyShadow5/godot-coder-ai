import json
import os
import subprocess
import sys
from pathlib import Path

from godot_coder.data import prepare_dataset
from godot_coder.tokenizer import ByteTokenizer


def test_training_writes_verified_token_summary(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw"
    processed = tmp_path / "data" / "processed"
    artifacts = tmp_path / "artifacts"
    configs = tmp_path / "configs"
    raw.mkdir(parents=True)
    artifacts.mkdir()
    configs.mkdir()
    for index in range(4):
        (raw / f"sample_{index}.gd").write_text(
            (f"extends Node\nvar value: int = {index}\nfunc get_value() -> int:\n    return value\n\n" * 20),
            encoding="utf-8",
        )
    tokenizer = ByteTokenizer()
    tokenizer.save(artifacts / "tokenizer.json")
    prepare_dataset(raw, processed, tokenizer, val_ratio=0.25)
    config = configs / "telemetry.yaml"
    config.write_text(
        """
profile:
  id: telemetry-test
  title: Telemetry Test
  method: Test
model:
  max_seq_len: 16
  n_layers: 1
  d_model: 32
  n_heads: 4
  d_ff: 64
  dropout: 0.0
  rope_base: 10000.0
  tie_embeddings: true
  gradient_checkpointing: false
train:
  tokenizer_path: artifacts/tokenizer.json
  data_dir: data/processed
  output_dir: checkpoints/telemetry
  device: cpu
  dtype: float32
  seed: 7
  batch_size: 2
  gradient_accumulation_steps: 2
  max_steps: 2
  learning_rate: 0.001
  min_learning_rate: 0.0001
  warmup_steps: 1
  weight_decay: 0.0
  beta1: 0.9
  beta2: 0.95
  gradient_clip: 1.0
  log_interval: 1
  eval_interval: 1
  eval_batches: 1
  save_interval: 1
""".strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    project_src = Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = str(project_src) + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-m", "godot_coder.train", "--config", str(config)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads((tmp_path / "checkpoints" / "telemetry" / "training_summary_latest.json").read_text(encoding="utf-8"))
    assert summary["status"] == "completed"
    assert summary["token_accounting"]["tokens_per_optimizer_step"] == 64
    assert summary["run_tokens_seen"] == 128
    assert summary["cumulative_tokens_seen"] == 128
    assert summary["average_tokens_per_second"] > 0
    assert summary["best_step"] in {1, 2}
    assert Path(tmp_path / summary["metrics_jsonl"]).exists()
