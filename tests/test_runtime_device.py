from __future__ import annotations

import torch

from godot_coder.runtime import resolve_device


def test_auto_prefers_cuda_when_available(monkeypatch) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        "godot_coder.runtime.mps_available",
        lambda: True,
    )
    assert resolve_device("auto").type == "cuda"


def test_auto_uses_mps_on_apple_silicon_without_cuda(monkeypatch) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(
        "godot_coder.runtime.mps_available",
        lambda: True,
    )
    assert resolve_device("auto").type == "mps"


def test_auto_falls_back_to_cpu_without_gpu(monkeypatch) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(
        "godot_coder.runtime.mps_available",
        lambda: False,
    )
    assert resolve_device("auto").type == "cpu"


def test_explicit_mps_requested_without_backend_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        "godot_coder.runtime.mps_available",
        lambda: False,
    )
    try:
        resolve_device("mps")
    except RuntimeError as exc:
        assert "MPS" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for missing MPS backend")
