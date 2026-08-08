from __future__ import annotations

import torch


def mps_available() -> bool:
    backend = getattr(torch.backends, "mps", None)
    return bool(backend is not None and backend.is_available())


def rocm_available() -> bool:
    """True when this torch build targets AMD GPUs via ROCm (HIP).

    ROCm builds expose AMD devices under the *cuda* device namespace, so
    torch.cuda.is_available() is already true there; the tell is a HIP
    version being set while torch.version.cuda stays None.
    """
    return bool(getattr(torch.version, "hip", None))


def resolve_device(requested: str) -> torch.device:
    if requested == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if mps_available():
            return torch.device("mps")
        return torch.device("cpu")
    if requested == "rocm":
        # There is no torch.device("rocm"): ROCm builds use the cuda device
        # namespace, so an explicit ROCm request resolves to cuda.
        if rocm_available():
            return torch.device("cuda")
        raise RuntimeError("ROCm was requested, but no AMD ROCm (HIP) build is installed")
    device = torch.device(requested)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested, but torch.cuda.is_available() is false")
    if device.type == "mps" and not mps_available():
        raise RuntimeError("MPS was requested, but no Apple Silicon (MPS) backend is available")
    return device
