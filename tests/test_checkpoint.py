import collections
import pickle
from pathlib import Path

import numpy as np
import pytest
import torch

from godot_coder.checkpoint import capture_rng_state, load_checkpoint, restore_rng_state, save_checkpoint
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

def test_new_checkpoint_loads_with_weights_only(tmp_path: Path) -> None:
    """New checkpoints store RNG state primitively, so the safe unpickler loads them without an allowlist."""
    model_config = ModelConfig(vocab_size=269, max_seq_len=16, n_layers=1, d_model=32, n_heads=4, d_ff=64)
    model = TinyGPT(model_config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    scaler = torch.amp.GradScaler("cpu", enabled=False)
    path = save_checkpoint(
        tmp_path,
        step=1,
        model=model,
        optimizer=optimizer,
        scaler=scaler,
        model_config=model_config.to_dict(),
        train_config={"max_steps": 1},
        tokenizer_fingerprint="abc",
        best_val_loss=1.0,
    )
    payload = torch.load(path, map_location="cpu", weights_only=True)
    assert payload["format"] == "godot-coder-checkpoint"
    assert isinstance(payload["rng_state"]["numpy"], dict)
    assert isinstance(payload["rng_state"]["numpy"]["key"], list)


def test_rng_restore_is_deterministic_after_round_trip(tmp_path: Path) -> None:
    """A checkpoint captures the seeded state; restore reproduces the exact next draws."""
    model_config = ModelConfig(vocab_size=269, max_seq_len=16, n_layers=1, d_model=32, n_heads=4, d_ff=64)
    model = TinyGPT(model_config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    scaler = torch.amp.GradScaler("cpu", enabled=False)
    np.random.seed(2024)
    path = save_checkpoint(
        tmp_path,
        step=1,
        model=model,
        optimizer=optimizer,
        scaler=scaler,
        model_config=model_config.to_dict(),
        train_config={"max_steps": 1},
        tokenizer_fingerprint="abc",
        best_val_loss=1.0,
    )
    np.random.seed(999)  # disturb the generator after the capture
    disturbed = np.random.randint(0, 2**31, 5)
    restore_rng_state(load_checkpoint(path)["rng_state"])
    restored = np.random.randint(0, 2**31, 5)
    np.random.seed(2024)
    expected = np.random.randint(0, 2**31, 5)
    assert (restored == expected).all()
    assert not (disturbed == expected).all()


def test_legacy_checkpoint_with_raw_numpy_rng_state_loads(tmp_path: Path) -> None:
    """Checkpoints saved before the primitive RNG state still load via the scoped numpy allowlist."""
    payload = {
        "format": "godot-coder-checkpoint",
        "format_version": 1,
        "step": 1,
        "model_state": {},
        "optimizer_state": {},
        "scaler_state": {},
        "model_config": {},
        "train_config": {},
        "tokenizer_fingerprint": "legacy",
        "best_val_loss": 1.0,
        "best_step": 1,
        "rng_state": capture_rng_state(),  # raw numpy tuple with ndarray
        "data_rng_state": None,
    }
    path = tmp_path / "legacy.pt"
    torch.save(payload, path)
    loaded = load_checkpoint(path)
    restore_rng_state(loaded["rng_state"])  # must not raise
    assert loaded["tokenizer_fingerprint"] == "legacy"


def test_checkpoint_rejects_untrusted_globals(tmp_path: Path) -> None:
    """weights_only=True stays on: non-allowlisted objects never get unpickled."""
    payload = {
        "format": "godot-coder-checkpoint",
        "format_version": 1,
        "step": 1,
        "model_state": {"sneaky": collections.deque([1, 2, 3])},
        "optimizer_state": {},
        "scaler_state": {},
        "model_config": {},
        "train_config": {},
        "tokenizer_fingerprint": "abc",
        "best_val_loss": 1.0,
        "best_step": 1,
        "rng_state": None,
        "data_rng_state": None,
    }
    path = tmp_path / "malicious.pt"
    torch.save(payload, path)
    with pytest.raises(pickle.UnpicklingError):
        load_checkpoint(path)


def test_restore_rng_state_handles_loaded_checkpoint_on_any_device(tmp_path: Path) -> None:
    """A checkpoint loaded with map_location=cuda puts the CPU RNG state on
    CUDA; restore_rng_state must still coerce it back to a CPU ByteTensor
    instead of crashing like torch.set_rng_state would on a CUDA tensor."""
    state = capture_rng_state()
    if torch.cuda.is_available():
        state["torch"] = state["torch"].cuda()
    restore_rng_state(state)
    # The CPU generator must have accepted the (possibly CUDA-placed) state.
    torch.randn(1)


def test_restore_rng_state_accepts_legacy_list_torch_state() -> None:
    """Older checkpoints stored the torch RNG state as a plain list of ints."""
    state = capture_rng_state()
    state["torch"] = state["torch"].tolist()
    restore_rng_state(state)
    torch.randn(1)
