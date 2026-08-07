from pathlib import Path

from godot_coder.profile_probe import _worker_result
from godot_coder.ui.services import list_configs


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_three_corpus_profiles_have_expected_scale() -> None:
    profiles = {
        item.get("profile_id"): item
        for item in list_configs(PROJECT_ROOT)
        if item.get("profile_id") and not item.get("profile_generated")
    }
    assert set(profiles) == {"starter", "balanced", "experimental"}
    assert 29_000_000 <= profiles["starter"]["parameters"] <= 30_000_000
    assert 90_000_000 <= profiles["balanced"]["parameters"] <= 93_000_000
    assert 160_000_000 <= profiles["experimental"]["parameters"] <= 166_000_000
    assert profiles["starter"]["tokens_per_optimizer_step"] == 8192
    assert profiles["balanced"]["tokens_per_optimizer_step"] == 8192
    assert profiles["experimental"]["tokens_per_optimizer_step"] == 7680


def test_probe_worker_executes_real_cpu_training_step(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    config = tmp_path / "configs" / "probe.yaml"
    config.write_text(
        """
profile:
  id: test
  title: Test
  probe_vocab_size: 269
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
  tokenizer_path: artifacts/missing.json
  data_dir: data/processed
  output_dir: checkpoints/test
  device: cpu
  dtype: float32
  seed: 1
  batch_size: 1
  gradient_accumulation_steps: 1
  max_steps: 1
  learning_rate: 0.001
  min_learning_rate: 0.0001
  warmup_steps: 0
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
    result = _worker_result(config, batch_size=1, device_name="cpu", warmup_steps=0, measure_steps=1)
    assert result["status"] == "pass"
    assert result["tokens_per_second"] > 0
    assert result["parameters"] > 0


def test_invalid_custom_config_is_reported_without_breaking_listing(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "custom.yaml").write_text("keep me\n", encoding="utf-8")
    items = list_configs(tmp_path)
    assert len(items) == 1
    assert items[0]["name"] == "custom"
    assert "error" in items[0]


def test_generated_autotune_config_is_not_counted_as_standard_profile(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "autotuned_night.yaml").write_text(
        """
profile:
  id: a91-1024
  title: A91-1024
model:
  max_seq_len: 1024
  n_layers: 12
  d_model: 768
  n_heads: 12
  d_ff: 2048
  tie_embeddings: true
train:
  tokenizer_path: artifacts/missing.json
  data_dir: data/processed/corpus_v06
  output_dir: checkpoints/autotuned
  batch_size: 6
  gradient_accumulation_steps: 2
""".strip(),
        encoding="utf-8",
    )
    [item] = list_configs(tmp_path)
    assert item["profile_id"] == "autotuned-night"
    assert item["profile_method"] == "Hardware-Autotuner"
    assert item["profile_generated"] is True
    migrated = (configs / "autotuned_night.yaml").read_text(encoding="utf-8")
    assert "generated: true" in migrated
    assert "batch_size: 6" in migrated
