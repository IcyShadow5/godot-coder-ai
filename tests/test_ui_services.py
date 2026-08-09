from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import torch

from godot_coder.process_control import ManagedProcessResult
from godot_coder.ui.services import GenerationService, validate_code


def _seed_project(root: Path) -> Path:
    project = root / "data" / "raw" / "seed_project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Seed"\n', encoding="utf-8")
    return project


def test_validate_code_runs_godot_through_managed_process(tmp_path: Path, monkeypatch) -> None:
    """Chat validation must use run_managed_process so a hung Mono-Godot child
    is killed as a tree, not left behind after the 30s timeout."""
    _seed_project(tmp_path)
    monkeypatch.setattr("godot_coder.ui.services.find_godot", lambda: "fake-godot")

    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs))
        return ManagedProcessResult(
            command=list(command),
            return_code=1,
            output="SCRIPT ERROR: Parse Error: Expected parameter name.",
            timed_out=False,
            duration_seconds=0.2,
            pid=4321,
            termination_attempted=False,
        )

    monkeypatch.setattr("godot_coder.ui.services.run_managed_process", fake_run)

    result = validate_code(tmp_path, "extends Node\nfunc broken(\n")
    assert result["passed"] is False
    assert result["return_code"] == 1
    assert "Parse Error" in result["output"]
    assert result["timed_out"] is False
    assert calls and calls[0][1]["timeout_seconds"] == 30
    # The temp script is always cleaned up, even on failure.
    assert not list((tmp_path / "data" / "generated").glob("studio_validation_*.gd"))


def test_validate_code_reports_timeout_flag(tmp_path: Path, monkeypatch) -> None:
    _seed_project(tmp_path)
    monkeypatch.setattr("godot_coder.ui.services.find_godot", lambda: "fake-godot")

    def fake_run(command, **kwargs):
        return ManagedProcessResult(
            command=list(command),
            return_code=None,
            output="still busy",
            timed_out=True,
            duration_seconds=30.0,
            pid=4321,
            termination_attempted=True,
        )

    monkeypatch.setattr("godot_coder.ui.services.run_managed_process", fake_run)

    result = validate_code(tmp_path, "extends Node\n")
    assert result["timed_out"] is True
    assert result["return_code"] is None


class _FakeTokenizer:
    vocab_size = 269
    eos_id = 0

    def __init__(self) -> None:
        self.decoded_ids: list[int] | None = None

    def fingerprint(self) -> str:
        return "fp-123"

    def encode(self, text: str, add_bos: bool = True, add_eos: bool = False) -> list[int]:
        return [10, 11, 12]  # 3 prompt tokens

    def decode(self, ids: list[int], skip_special_tokens: bool = True) -> str:
        self.decoded_ids = list(ids)
        return "extends Node\nfunc generated():\n\tpass"


class _FakeModel:
    def __init__(self, config) -> None:
        self.config = config

    def to(self, device):
        return self

    def eval(self) -> None:
        pass

    def load_state_dict(self, state) -> None:
        pass

    def generate(self, input_ids, **kwargs) -> torch.Tensor:
        # Model returns the full sequence: 3 prompt tokens + 2 new tokens.
        return torch.tensor([[10, 11, 12, 99, 100]])


def test_generate_returns_only_completion_not_prompt(tmp_path: Path, monkeypatch) -> None:
    """The chat shows the prompt already; generated text must not echo it back."""
    (tmp_path / "checkpoints" / "v06").mkdir(parents=True)
    (tmp_path / "checkpoints" / "v06" / "best.pt").write_bytes(b"dummy")
    (tmp_path / "artifacts").mkdir()

    payload = {
        "train_config": {"tokenizer_path": "artifacts/tokenizer.json"},
        "tokenizer_fingerprint": "fp-123",
        "model_config": {
            "vocab_size": 269,
            "max_seq_len": 32,
            "n_layers": 1,
            "d_model": 32,
            "n_heads": 4,
            "d_ff": 64,
            "dropout": 0.0,
            "rope_base": 10000.0,
            "tie_embeddings": True,
            "gradient_checkpointing": False,
        },
        "model_state": {},
    }
    monkeypatch.setattr("godot_coder.ui.services.load_checkpoint", lambda path, map_location: payload)
    tokenizer = _FakeTokenizer()
    monkeypatch.setattr("godot_coder.ui.services.load_tokenizer", lambda path: tokenizer)
    monkeypatch.setattr("godot_coder.ui.services.TinyGPT", _FakeModel)
    monkeypatch.setattr("godot_coder.ui.services.resolve_device", lambda name: torch.device("cpu"))

    service = GenerationService(tmp_path)
    text = service.generate(
        "checkpoints/v06/best.pt",
        "extends Node",
        max_new_tokens=16,
        temperature=0.8,
        top_k=40,
    )
    assert text.startswith("extends Node")  # decode result from the fake tokenizer
    assert tokenizer.decoded_ids == [99, 100], "the prompt prefix must be stripped from the completion"


def test_generate_accepts_temperature_and_top_k_bounds(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "checkpoints" / "v06").mkdir(parents=True)
    (tmp_path / "checkpoints" / "v06" / "best.pt").write_bytes(b"dummy")
    monkeypatch.setattr("godot_coder.ui.services.load_checkpoint", lambda path, map_location: {
        "train_config": {"tokenizer_path": "artifacts/tokenizer.json"},
        "tokenizer_fingerprint": "fp-123",
        "model_config": {"vocab_size": 269, "max_seq_len": 32, "n_layers": 1, "d_model": 32,
                         "n_heads": 4, "d_ff": 64, "dropout": 0.0, "rope_base": 10000.0,
                         "tie_embeddings": True, "gradient_checkpointing": False},
        "model_state": {},
    })
    monkeypatch.setattr("godot_coder.ui.services.load_tokenizer", lambda path: _FakeTokenizer())
    monkeypatch.setattr("godot_coder.ui.services.TinyGPT", _FakeModel)
    monkeypatch.setattr("godot_coder.ui.services.resolve_device", lambda name: torch.device("cpu"))

    service = GenerationService(tmp_path)
    # Boundary values must not raise.
    service.generate("checkpoints/v06/best.pt", "extends Node", max_new_tokens=1, temperature=0.0, top_k=0)
    service.generate("checkpoints/v06/best.pt", "extends Node", max_new_tokens=4096, temperature=5.0, top_k=1000)
