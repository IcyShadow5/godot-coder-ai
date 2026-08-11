"""Tests for the generate CLI — the chat/inference path users actually hit."""

import sys

import pytest
import torch

import godot_coder.generate as generate


class _FakeTokenizer:
    def __init__(self, fingerprint: str = "abc") -> None:
        self._fp = fingerprint
        self.eos_id = 0
        self.last_encoded = ""

    def fingerprint(self) -> str:
        return self._fp

    def encode(self, text: str, *, add_bos: bool = False) -> list[int]:
        self.last_encoded = text
        return [1, 2, 3]

    def decode(self, ids, *, skip_special_tokens: bool = False) -> str:
        # The prompt prefix is [1, 2, 3]; generated ids start at 100. This
        # lets tests tell "prompt still in output" from "suffix only".
        if ids[:3] == [1, 2, 3]:
            return "PROMPT_AND_GENERATED"
        return "GENERATED_ONLY"


class _FakeModel:
    def __init__(self, config) -> None:
        self.config = config
        self.evaluated = False

    def to(self, device):
        return self

    def load_state_dict(self, state) -> None:
        self.state = state

    def eval(self) -> None:
        self.evaluated = True

    def generate(self, input_ids, *, max_new_tokens, temperature, top_k, top_p=None, repetition_penalty=1.0, eos_id=None):
        prompt = input_ids[0].tolist()
        return torch.tensor([prompt + list(range(100, 100 + max_new_tokens))])


_MODEL_CONFIG = {
    "vocab_size": 269,
    "max_seq_len": 256,
    "n_layers": 4,
    "d_model": 192,
    "n_heads": 6,
    "d_ff": 512,
    "dropout": 0.0,
    "rope_base": 10000.0,
    "tie_embeddings": True,
    "gradient_checkpointing": False,
}


def _install_fakes(monkeypatch, tmp_path, *, fp="abc"):
    checkpoint = tmp_path / "latest.pt"
    checkpoint.write_bytes(b"fake")
    payload = {
        "train_config": {"tokenizer_path": "artifacts/tokenizer.json"},
        "tokenizer_fingerprint": fp,
        "model_config": dict(_MODEL_CONFIG),
        "model_state": {"w": 1},
    }
    monkeypatch.setattr(generate, "find_project_root", lambda start: tmp_path)
    monkeypatch.setattr(generate, "resolve_project_path", lambda root, path: tmp_path / "tokenizer.json")
    monkeypatch.setattr(generate, "load_checkpoint", lambda path, map_location: payload)
    monkeypatch.setattr(generate, "TinyGPT", _FakeModel)
    monkeypatch.setattr(generate, "resolve_device", lambda device: torch.device("cpu"))
    return checkpoint


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["generate"] + argv)
    generate.main()


def _install_tokenizer(monkeypatch):
    captured: dict = {}

    def _loader(path):
        tokenizer = _FakeTokenizer()
        captured["tokenizer"] = tokenizer
        return tokenizer

    monkeypatch.setattr(generate, "load_tokenizer", _loader)
    return captured


def test_parse_args_defaults(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["generate"])
    args = generate.parse_args()
    assert args.checkpoint == "checkpoints/tiny/latest.pt"
    assert args.prompt == "extends Node\n\n"
    assert args.max_new_tokens == 300
    assert args.temperature == 0.8
    assert args.top_k == 40
    assert args.output is None
    assert args.suffix_only is False


def test_parse_args_override(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["generate", "--max-new-tokens", "10", "--top-k", "5", "--suffix-only", "--output", "out.gd"])
    args = generate.parse_args()
    assert args.max_new_tokens == 10
    assert args.top_k == 5
    assert args.suffix_only is True
    assert args.output == "out.gd"


@pytest.mark.parametrize("flag,value,message", [
    ("--max-new-tokens", "0", "max_new_tokens"),
    ("--temperature", "-1", "temperature"),
    ("--temperature", "nan", "temperature"),
    ("--top-k", "-1", "top_k"),
])
def test_main_rejects_invalid_args(monkeypatch, flag, value, message):
    with pytest.raises(ValueError, match=message):
        _run_main(monkeypatch, [flag, value])


def test_parse_args_sampling_flags(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["generate", "--top-p", "0.9", "--repetition-penalty", "1.2"])
    args = generate.parse_args()
    assert args.top_p == 0.9
    assert args.repetition_penalty == 1.2


def test_main_generates_and_prints(monkeypatch, tmp_path, capsys):
    checkpoint = _install_fakes(monkeypatch, tmp_path)
    _install_tokenizer(monkeypatch)
    _run_main(monkeypatch, ["--checkpoint", str(checkpoint), "--max-new-tokens", "5"])
    out = capsys.readouterr().out
    # Default mode keeps the prompt prefix in the decoded output.
    assert "PROMPT_AND_GENERATED" in out


def test_main_writes_output_file(monkeypatch, tmp_path):
    checkpoint = _install_fakes(monkeypatch, tmp_path)
    _install_tokenizer(monkeypatch)
    out_file = tmp_path / "out" / "result.gd"
    _run_main(monkeypatch, ["--checkpoint", str(checkpoint), "--max-new-tokens", "5", "--output", str(out_file)])
    assert out_file.exists()
    assert "PROMPT_AND_GENERATED" in out_file.read_text(encoding="utf-8")


def test_main_suffix_only_drops_prompt(monkeypatch, tmp_path, capsys):
    checkpoint = _install_fakes(monkeypatch, tmp_path)
    _install_tokenizer(monkeypatch)
    _run_main(monkeypatch, ["--checkpoint", str(checkpoint), "--max-new-tokens", "5", "--suffix-only"])
    out = capsys.readouterr().out
    # With --suffix-only the prompt ids are cut from the output first.
    assert "GENERATED_ONLY" in out
    assert "PROMPT_AND_GENERATED" not in out


def test_main_rejects_tokenizer_mismatch(monkeypatch, tmp_path):
    checkpoint = _install_fakes(monkeypatch, tmp_path, fp="abc")
    monkeypatch.setattr(generate, "load_tokenizer", lambda path: _FakeTokenizer("DIFFERENT"))
    with pytest.raises(ValueError, match="do not match"):
        _run_main(monkeypatch, ["--checkpoint", str(checkpoint)])


def test_main_uses_prompt_file_content(monkeypatch, tmp_path):
    checkpoint = _install_fakes(monkeypatch, tmp_path)
    captured = _install_tokenizer(monkeypatch)
    prompt_file = tmp_path / "prompt.gd"
    prompt_file.write_text("extends Node2D\n", encoding="utf-8")
    _run_main(monkeypatch, ["--checkpoint", str(checkpoint), "--prompt-file", str(prompt_file)])
    # The tokenizer received the file content, not the default prompt.
    assert captured["tokenizer"].last_encoded == "extends Node2D\n"


def test_main_falls_back_to_cwd_when_no_project_root(monkeypatch, tmp_path, capsys):
    checkpoint = _install_fakes(monkeypatch, tmp_path)
    _install_tokenizer(monkeypatch)

    def no_root(start):
        raise FileNotFoundError("no project root")

    monkeypatch.setattr(generate, "find_project_root", no_root)
    _run_main(monkeypatch, ["--checkpoint", str(checkpoint), "--max-new-tokens", "3"])
    assert "PROMPT_AND_GENERATED" in capsys.readouterr().out
