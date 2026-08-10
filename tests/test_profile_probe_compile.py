"""Tests for profile_probe compile handling - the autotune compile probe.

The real worker keeps running in eager mode when torch.compile fails and
reports compile_enabled=False + compile_error instead of status=error.
The autotuner relies on exactly those fields (see autotune.compile_failed),
so this guards the contract.
"""

from pathlib import Path

import torch

from godot_coder.profile_probe import _worker_result


def _write_probe_config(tmp_path: Path, *, compile_enabled: bool) -> Path:
    (tmp_path / "configs").mkdir()
    config = tmp_path / "configs" / "probe.yaml"
    config.write_text(
        f"""
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
  compile:
    enabled: {'true' if compile_enabled else 'false'}
    mode: default
""".strip(),
        encoding="utf-8",
    )
    return config


def test_probe_reports_compile_failure_without_failing(tmp_path: Path, monkeypatch) -> None:
    """A failing torch.compile must not fail the probe - it reports the reason."""
    def failing_compile(model, *, mode=None, dynamic=False):
        raise RuntimeError("TritonMissing: no triton kernel available")

    monkeypatch.setattr(torch, "compile", failing_compile)
    config = _write_probe_config(tmp_path, compile_enabled=True)
    result = _worker_result(config, batch_size=1, device_name="cpu", warmup_steps=0, measure_steps=1)
    assert result["status"] == "pass"
    assert result["compile_requested"] is True
    assert result["compile_enabled"] is False
    assert result["compile_error"] is not None
    assert "TritonMissing" in result["compile_error"]


def test_probe_reports_compile_success_when_available(tmp_path: Path, monkeypatch) -> None:
    """A working torch.compile sets compile_enabled=True and no error."""
    def identity_compile(model, *, mode=None, dynamic=False):
        return model

    monkeypatch.setattr(torch, "compile", identity_compile)
    config = _write_probe_config(tmp_path, compile_enabled=True)
    result = _worker_result(config, batch_size=1, device_name="cpu", warmup_steps=0, measure_steps=1)
    assert result["status"] == "pass"
    assert result["compile_requested"] is True
    assert result["compile_enabled"] is True
    assert result["compile_error"] is None


def test_probe_with_compile_disabled_reports_not_requested(tmp_path: Path) -> None:
    """A config without compile simply reports compile_requested=False."""
    config = _write_probe_config(tmp_path, compile_enabled=False)
    result = _worker_result(config, batch_size=1, device_name="cpu", warmup_steps=0, measure_steps=1)
    assert result["status"] == "pass"
    assert result["compile_requested"] is False
    assert result["compile_enabled"] is False
    assert result["compile_error"] is None
