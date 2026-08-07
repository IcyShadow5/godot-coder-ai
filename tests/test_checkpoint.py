from pathlib import Path

import torch

from godot_coder.checkpoint import load_checkpoint, save_checkpoint
from godot_coder.config import ModelConfig, TrainConfig
from godot_coder.model import TinyGPT


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    model_config = ModelConfig(vocab_size=269, max_seq_len=16, n_layers=1, d_model=32, n_heads=4, d_ff=64)
    train_config = TrainConfig(max_steps=1)
    model = TinyGPT(model_config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    scaler = torch.amp.GradScaler("cuda", enabled=False)
    path = save_checkpoint(
        tmp_path,
        step=1,
        model=model,
        optimizer=optimizer,
        scaler=scaler,
        model_config=model_config.to_dict(),
        train_config=train_config.to_dict(),
        tokenizer_fingerprint="abc",
        best_val_loss=1.0,
        data_rng_state={"train": {"state": 1}, "eval": {"state": 2}},
        is_best=True,
    )
    payload = load_checkpoint(path)
    assert payload["step"] == 1
    assert payload["tokenizer_fingerprint"] == "abc"
    assert payload["data_rng_state"]["train"]["state"] == 1


def test_best_checkpoint_alias(tmp_path: Path) -> None:
    model_config = ModelConfig(vocab_size=269, max_seq_len=16, n_layers=1, d_model=32, n_heads=4, d_ff=64)
    train_config = TrainConfig(max_steps=1)
    model = TinyGPT(model_config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    scaler = torch.amp.GradScaler("cuda", enabled=False)
    save_checkpoint(
        tmp_path,
        step=1,
        model=model,
        optimizer=optimizer,
        scaler=scaler,
        model_config=model_config.to_dict(),
        train_config=train_config.to_dict(),
        tokenizer_fingerprint="abc",
        best_val_loss=1.0,
        is_best=True,
    )
    assert (tmp_path / "best.pt").exists()
    assert load_checkpoint(tmp_path / "best.pt")["step"] == 1


def test_checkpoint_retention_and_aliases(tmp_path: Path) -> None:
    model_config = ModelConfig(vocab_size=269, max_seq_len=16, n_layers=1, d_model=32, n_heads=4, d_ff=64)
    train_config = TrainConfig(max_steps=3, warmup_steps=0)
    model = TinyGPT(model_config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    scaler = torch.amp.GradScaler("cuda", enabled=False)
    for step in range(1, 4):
        save_checkpoint(
            tmp_path,
            step=step,
            model=model,
            optimizer=optimizer,
            scaler=scaler,
            model_config=model_config.to_dict(),
            train_config=train_config.to_dict(),
            tokenizer_fingerprint="abc",
            best_val_loss=float(4 - step),
            is_best=step == 2,
            keep_last=2,
        )
    assert not (tmp_path / "step_00000001.pt").exists()
    assert (tmp_path / "step_00000002.pt").exists()
    assert (tmp_path / "step_00000003.pt").exists()
    assert load_checkpoint(tmp_path / "latest.pt")["step"] == 3
    assert load_checkpoint(tmp_path / "best.pt")["step"] == 2


def test_save_checkpoint_keep_last_zero_preserves_existing(tmp_path: Path) -> None:
    """keep_last=0 creates a checkpoint without pruning existing files (emergency save)."""
    output_dir = tmp_path / "checkpoints"
    output_dir.mkdir()
    # Create a tiny model
    model = torch.nn.Embedding(16, 8)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    scaler = torch.amp.GradScaler("cpu")

    # Save a normal checkpoint first
    first = save_checkpoint(
        output_dir, step=100, model=model, optimizer=optimizer, scaler=scaler,
        model_config={"vocab_size": 16, "max_seq_len": 32},
        train_config={"batch_size": 2},
        tokenizer_fingerprint="test-tok",
        best_val_loss=1.0, best_step=50,
        data_rng_state={"train": {}, "eval": {}},
        is_best=True, keep_last=2,
    )
    assert first.exists()

    # Emergency save with keep_last=0 — should not delete existing checkpoints
    emergency = save_checkpoint(
        output_dir, step=150, model=model, optimizer=optimizer, scaler=scaler,
        model_config={"vocab_size": 16, "max_seq_len": 32},
        train_config={"batch_size": 2},
        tokenizer_fingerprint="test-tok",
        best_val_loss=0.9, best_step=150,
        data_rng_state={"train": {}, "eval": {}},
        is_best=False, keep_last=0,
    )
    assert emergency.exists()
    assert first.exists(), "keep_last=0 should not delete existing checkpoints"
