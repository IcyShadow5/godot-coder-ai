import pytest
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


def test_apply_repetition_penalty_math() -> None:
    logits = torch.tensor([[3.0, -1.0, 0.5]])
    past = torch.tensor([[0, 1]])
    out = TinyGPT._apply_repetition_penalty(logits, past, 1.5)
    assert out[0, 0] == pytest.approx(2.0)  # positive logit divided
    assert out[0, 1] == pytest.approx(-1.5)  # negative logit multiplied
    assert out[0, 2] == pytest.approx(0.5)  # unseen token untouched


def test_apply_repetition_penalty_noop_when_off() -> None:
    logits = torch.tensor([[3.0, -1.0]])
    past = torch.tensor([[0]])
    assert torch.equal(TinyGPT._apply_repetition_penalty(logits, past, 1.0), logits)


def test_apply_top_p_masks_low_probability_tail() -> None:
    logits = torch.tensor([[10.0, 9.0, 8.0, 0.0]])
    out = TinyGPT._apply_top_p(logits, 0.9)
    assert out[0, 3] == float("-inf")  # dominated token cut
    assert out[0, 0] == 10.0  # top token untouched


def _loop_model() -> TinyGPT:
    model = TinyGPT(tiny_config())

    class _Out:
        def __init__(self, seq_len: int) -> None:
            logits = torch.full((1, seq_len, tiny_config().vocab_size), 1.0)
            logits[0, -1, 5] = 2.0  # token 5 is the greedy favourite every step
            self.logits = logits

    model.forward = lambda x, y=None: _Out(x.shape[1])  # type: ignore[method-assign]
    model.forward_cached = (  # type: ignore[method-assign]
        lambda input_ids, cache=None: (_Out(input_ids.shape[1]).logits, None)
    )
    return model


def test_generate_breaks_repetition_under_penalty() -> None:
    model = _loop_model()
    x = torch.tensor([[1, 2, 3]])
    without = model.generate(x, max_new_tokens=8, temperature=0.0)
    assert (without[0, 3:] == 5).all()  # greedy loops on the favourite token
    withp = model.generate(x, max_new_tokens=8, temperature=0.0, repetition_penalty=3.0)
    assert withp[0, 3] == 5  # first pick still the favourite
    assert withp[0, 4] != 5  # penalty breaks the loop on the next step


def test_generate_penalty_works_without_kv_cache() -> None:
    model = _loop_model()
    x = torch.tensor([[1, 2, 3]])
    # 3 + 40 > max_seq_len 32 -> the non-cached decoding branch runs
    withp = model.generate(x, max_new_tokens=40, temperature=0.0, repetition_penalty=3.0)
    assert withp.shape[1] == 43
    assert withp[0, 3] == 5
    assert withp[0, 4] != 5


def test_generate_stream_matches_generate_on_both_paths() -> None:
    """Streaming and batch generation must sample the exact same tokens."""
    model = TinyGPT(tiny_config())
    x = torch.randint(0, 269, (1, 8))
    for use_kv_cache in (True, False):
        expected = model.generate(x, max_new_tokens=6, temperature=0.0, use_kv_cache=use_kv_cache)
        streamed = torch.cat(
            [x, *list(model.generate_stream(x, max_new_tokens=6, temperature=0.0, use_kv_cache=use_kv_cache))],
            dim=1,
        )
        assert torch.equal(streamed, expected), f"kv_cache={use_kv_cache}"


def test_generate_stream_yields_each_token_then_stops_at_eos() -> None:
    model = _loop_model()
    x = torch.tensor([[1, 2, 3]])
    ids = list(model.generate_stream(x, max_new_tokens=8, temperature=0.0))
    assert len(ids) == 8
    assert all(int(token.item()) == 5 for token in ids)

    # eos_id=5: the very first sampled token equals eos, so the stream
    # yields exactly one token and returns (like generate() would break).
    stopped = list(model.generate_stream(x, max_new_tokens=8, temperature=0.0, eos_id=5))
    assert len(stopped) == 1
    assert int(stopped[0].item()) == 5
