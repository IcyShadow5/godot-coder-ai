import torch

from godot_coder.config import ModelConfig
from godot_coder.model import TinyGPT


def tiny_config() -> ModelConfig:
    return ModelConfig(
        vocab_size=269,
        max_seq_len=32,
        n_layers=2,
        d_model=64,
        n_heads=4,
        d_ff=128,
        dropout=0.0,
    )


def test_forward_shape_and_loss() -> None:
    model = TinyGPT(tiny_config())
    x = torch.randint(0, 269, (2, 16))
    y = torch.randint(0, 269, (2, 16))
    output = model(x, y)
    assert output.logits.shape == (2, 16, 269)
    assert output.loss is not None
    assert torch.isfinite(output.loss)


def test_generation_extends_sequence() -> None:
    model = TinyGPT(tiny_config())
    x = torch.randint(0, 269, (1, 8))
    generated = model.generate(x, max_new_tokens=4, temperature=0.0)
    assert generated.shape == (1, 12)
