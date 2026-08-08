from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F

from . import __version__
from .project import find_project_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the Godot Coder AI runtime and project installation.")
    parser.add_argument("--root", default=None, help="Project root; normally detected automatically")
    parser.add_argument("--json", action="store_true", help="Print one machine-readable JSON object")
    return parser.parse_args()


def _find_godot() -> str | None:
    for name in ("godot", "godot4", "godot.CMD", "godot4.CMD"):
        found = shutil.which(name)
        if found:
            return found
    return None


def collect_status(root: Path) -> tuple[dict[str, Any], bool]:
    status: dict[str, Any] = {
        "app_version": __version__,
        "project_root": str(root),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "pytorch": torch.__version__,
        "pytorch_cuda_build": torch.version.cuda,
        "pytorch_hip_build": getattr(torch.version, "hip", None),
        "cuda_available": torch.cuda.is_available(),
        "cuda_test": None,
        "gpu": None,
        "godot": None,
        "project_files": None,
        "ok": True,
        "problems": [],
    }

    if torch.cuda.is_available():
        try:
            device = torch.device("cuda")
            properties = torch.cuda.get_device_properties(device)
            status["gpu"] = {
                "name": properties.name,
                "compute_capability": f"{properties.major}.{properties.minor}",
                "vram_gib": round(properties.total_memory / 1024**3, 2),
            }
            q = torch.randn(1, 2, 16, 32, device=device, dtype=torch.float16)
            result = F.scaled_dot_product_attention(q, q, q, is_causal=True)
            torch.cuda.synchronize(device)
            status["cuda_test"] = "passed" if bool(torch.isfinite(result).all()) else "failed"
            if status["cuda_test"] != "passed":
                status["problems"].append("CUDA attention returned non-finite values")
        except Exception as exc:  # doctor must report unsupported wheels instead of crashing
            status["cuda_test"] = "failed"
            status["problems"].append(f"CUDA runtime test failed: {type(exc).__name__}: {exc}")
    elif torch.version.cuda is None:
        status["problems"].append("CPU-only PyTorch is active; GPU training is unavailable")
    else:
        status["problems"].append("PyTorch has a CUDA build, but CUDA is not available")

    godot = _find_godot()
    if godot:
        try:
            completed = subprocess.run(
                [godot, "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=False, timeout=15,
            )
            status["godot"] = {
                "path": godot,
                "version": (completed.stdout or completed.stderr).strip(),
                "returncode": completed.returncode,
            }
            if completed.returncode != 0:
                status["problems"].append("Godot was found but its version command failed")
        except (OSError, subprocess.TimeoutExpired) as exc:
            status["problems"].append(f"Godot check failed: {exc}")
    else:
        status["problems"].append("Godot was not found in PATH")

    expected = [
        root / "pyproject.toml",
        root / "configs" / "corpus_starter_30m.yaml",
        root / "src" / "godot_coder" / "model.py",
        root / "LICENSE",
    ]
    missing = [str(path.relative_to(root)) for path in expected if not path.exists()]
    status["project_files"] = {"passed": not missing, "missing": missing}
    if missing:
        status["problems"].append("Missing project files: " + ", ".join(missing))

    # A CPU-only setup is still usable for tests, so only hard failures make doctor non-zero.
    hard_failure = bool(missing) or status["cuda_test"] == "failed" or status["godot"] is None
    status["ok"] = not hard_failure
    return status, not hard_failure


def main() -> None:
    args = parse_args()
    root = find_project_root(args.root)
    status, ok = collect_status(root)
    if args.json:
        print(json.dumps(status, ensure_ascii=True))
    else:
        print(f"Godot Coder AI: {status['app_version']}")
        print(f"Project: {status['project_root']}")
        print(f"Python: {status['python']} ({status['python_executable']})")
        print(f"Platform: {status['platform']}")
        print(f"PyTorch: {status['pytorch']}")
        print(f"PyTorch CUDA build: {status['pytorch_cuda_build']}")
        print(f"CUDA available: {status['cuda_available']}")
        if status["gpu"]:
            gpu = status["gpu"]
            print(f"GPU: {gpu['name']}")
            print(f"Compute capability: {gpu['compute_capability']}")
            print(f"VRAM: {gpu['vram_gib']:.2f} GiB")
            print(f"CUDA causal-attention smoke test: {status['cuda_test']}")
        else:
            print("GPU training: unavailable")
        if status["godot"]:
            print(f"Godot: {status['godot']['version']} ({status['godot']['path']})")
        else:
            print("Godot: not found")
        print(f"Project files: {'passed' if status['project_files']['passed'] else 'failed'}")
        for problem in status["problems"]:
            print(f"WARNING: {problem}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
