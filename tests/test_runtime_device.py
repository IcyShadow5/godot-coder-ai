from __future__ import annotations

import torch

from godot_coder.runtime import resolve_device, rocm_available


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


def test_rocm_detected_when_hip_build(monkeypatch) -> None:
    monkeypatch.setattr(torch.version, "hip", "6.2", raising=False)
    assert rocm_available() is True


def test_rocm_absent_without_hip(monkeypatch) -> None:
    monkeypatch.setattr(torch.version, "hip", None, raising=False)
    assert rocm_available() is False


def test_explicit_rocm_maps_to_cuda_when_hip_build(monkeypatch) -> None:
    monkeypatch.setattr(torch.version, "hip", "6.2", raising=False)
    assert resolve_device("rocm").type == "cuda"


def test_explicit_rocm_without_hip_raises(monkeypatch) -> None:
    monkeypatch.setattr(torch.version, "hip", None, raising=False)
    try:
        resolve_device("rocm")
    except RuntimeError as exc:
        assert "ROCm" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for missing ROCm build")


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
