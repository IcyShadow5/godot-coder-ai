from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Any

import yaml

from .profile_probe import _run_worker

MATRIX = (
    ("A91-1024", "configs/corpus_balanced_90m.yaml", 1024),
    ("B91-2048", "configs/corpus_balanced_90m.yaml", 2048),
    ("C163-1024", "configs/corpus_experimental_163m.yaml", 1024),
    ("C163-2048", "configs/corpus_experimental_163m.yaml", 2048),
)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def normalize_autotuned_config(root: Path) -> bool:
    """Repair metadata in an older generated autotune config without touching training values."""
    path = root / "configs" / "autotuned_night.yaml"
    if not path.exists():
        return False
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            return False
        profile = dict(raw.get("profile") or {})
        recommendation_path = root / "reports" / "hardware" / "autotune_latest.json"
        matrix_label = None
        if recommendation_path.exists():
            report = json.loads(recommendation_path.read_text(encoding="utf-8"))
            matrix_label = (report.get("recommendation") or {}).get("matrix_label")
        matrix_label = str(matrix_label or profile.get("title") or profile.get("id") or "A91-1024")
        if "·" in matrix_label:
            matrix_label = matrix_label.rsplit("·", 1)[-1].strip()
        base_id = str(profile.get("base_id") or profile.get("id") or matrix_label.lower())
        desired = {
            "generated": True,
            "base_id": base_id if base_id != "autotuned-night" else matrix_label.lower(),
            "id": "autotuned-night",
            "title": f"Autotuned Night · {matrix_label}",
            "method": "Hardware-Autotuner",
            "description": "Measured local hardware recommendation. Batch, context and memory technique come from the isolated autotuner.",
            "beginner_order": 0,
            "recommended": True,
        }
        changed = any(profile.get(key) != value for key, value in desired.items())
        if not changed:
            return False
        profile.update(desired)
        raw["profile"] = profile
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
        os.replace(temporary, path)
        return True
    except (OSError, ValueError, TypeError, json.JSONDecodeError, yaml.YAMLError):
        return False


def _variant(root: Path, label: str, base_path: str, context: int, checkpointing: bool, compile_enabled: bool, batch: int) -> Path:
    raw = yaml.safe_load((root / base_path).read_text(encoding="utf-8"))
    raw["profile"] = dict(raw.get("profile") or {})
    raw["profile"].update({"id": label.lower(), "title": label, "probe_max_batch_size": batch})
    raw["model"]["max_seq_len"] = context
    raw["model"]["gradient_checkpointing"] = checkpointing
    raw["train"]["batch_size"] = batch
    raw["train"]["gradient_accumulation_steps"] = max(1, math.ceil(8192 / (batch * context)))
    raw["train"]["compile"] = {"enabled": compile_enabled, "mode": "default"}
    raw["train"].pop("compile_model", None)
    target = root / "reports" / "hardware" / "autotune_configs" / f"{label.lower()}-ckpt{int(checkpointing)}-compile{int(compile_enabled)}-b{batch}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return target




def _mark_safety(result: dict[str, Any]) -> dict[str, Any]:
    """Classify technically completed probes that exceed the physical VRAM safety limit."""
    if result.get("status") != "pass":
        return result
    fraction = float(result.get("peak_reserved_fraction") or 0.0)
    if fraction > 0.90:
        result["status"] = "unsafe"
        result["unsafe_reason"] = (
            f"Peak reserved VRAM was {fraction * 100:.1f}% of device memory; "
            "Windows may page CUDA memory into shared RAM, which is not suitable for training."
        )
    return result

def _score(result: dict[str, Any]) -> tuple[float, int, int]:
    # Speed is primary, then model capacity and context. Results over 90% VRAM are excluded earlier.
    return (float(result.get("tokens_per_second") or 0), int(result.get("parameters") or 0), int(result.get("sequence_length") or 0))


def run_autotune(root: Path, *, full: bool, warmup_steps: int, measure_steps: int) -> dict[str, Any]:
    started = time.time()
    batches = (1, 2, 4, 6, 8) if full else (1, 2, 4)
    compile_modes = (False, True) if full else (False,)
    attempts: list[dict[str, Any]] = []
    total = len(MATRIX) * 2 * len(compile_modes) * len(batches)
    index = 0
    compile_disabled_reason: str | None = None
    for label, base, context in MATRIX:
        for checkpointing in (False, True):
            for compile_enabled in compile_modes:
                for batch in batches:
                    index += 1
                    config = _variant(root, label, base, context, checkpointing, compile_enabled, batch)
                    relative = config.relative_to(root).as_posix()
                    print(f"autotune={index}/{total} profile={label} context={context} checkpointing={checkpointing} compile={compile_enabled} batch={batch}", flush=True)
                    if compile_enabled and compile_disabled_reason is not None:
                        result = {
                            "status": "skipped",
                            "config": relative,
                            "batch_size": batch,
                            "sequence_length": context,
                            "compile_requested": True,
                            "compile_enabled": False,
                            "error": compile_disabled_reason,
                        }
                    else:
                        result = _run_worker(root, relative, batch, "auto", warmup_steps, measure_steps)
                        if compile_enabled and result.get("status") == "error":
                            compile_disabled_reason = str(result.get("error") or "torch.compile probe failed")[:1000]
                    result.update({"matrix_label": label, "checkpointing": checkpointing, "compile_requested": compile_enabled, "config": relative})
                    _mark_safety(result)
                    attempts.append(result)
                    print(f"  status={result.get('status')} vram={result.get('peak_reserved_gib')} tok/s={result.get('tokens_per_second')}", flush=True)
    safe = [item for item in attempts if item.get("status") == "pass" and float(item.get("peak_reserved_fraction") or 0) <= 0.90]
    recommended = max(safe, key=_score) if safe else None
    recommendation = None
    if recommended:
        source = root / str(recommended["config"])
        destination = root / "configs" / "autotuned_night.yaml"
        generated_config = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        generated_profile = dict(generated_config.get("profile") or {})
        generated_profile.update(
            {
                "generated": True,
                "base_id": generated_profile.get("id"),
                "id": "autotuned-night",
                "title": f"Autotuned Night · {recommended['matrix_label']}",
                "method": "Hardware-Autotuner",
                "beginner_order": 0,
                "recommended": True,
            }
        )
        generated_config["profile"] = generated_profile
        destination.write_text(yaml.safe_dump(generated_config, sort_keys=False, allow_unicode=True), encoding="utf-8")
        recommendation = {
            "config": destination.relative_to(root).as_posix(),
            "matrix_label": recommended["matrix_label"],
            "batch_size": recommended["batch_size"],
            "context": recommended["sequence_length"],
            "parameters": recommended["parameters"],
            "gradient_checkpointing": recommended["checkpointing"],
            "compile_enabled": recommended.get("compile_enabled", False),
            "peak_reserved_gib": recommended.get("peak_reserved_gib"),
            "peak_reserved_fraction": recommended.get("peak_reserved_fraction"),
            "tokens_per_second": recommended.get("tokens_per_second"),
            "reason": "Fastest measured configuration below the 90% VRAM safety ceiling.",
        }
    report = {
        "format": "godot-coder-hardware-autotune",
        "format_version": 1,
        "created_at": time.time(),
        "duration_seconds": round(time.time() - started, 3),
        "full_matrix": full,
        "warmup_steps": warmup_steps,
        "measure_steps": measure_steps,
        "attempts": attempts,
        "compile_available": compile_disabled_reason is None,
        "compile_disabled_reason": compile_disabled_reason,
        "recommendation": recommendation,
    }
    reports = root / "reports" / "hardware"
    _atomic_json(reports / f"autotune_{time.strftime('%Y%m%d-%H%M%S')}.json", report)
    _atomic_json(reports / "autotune_latest.json", report)
    print("AUTOTUNE_SUMMARY_JSON=" + json.dumps({"attempts": len(attempts), "safe": len(safe), "recommendation": recommendation}, ensure_ascii=True))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated VRAM and throughput autotuner.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--warmup-steps", type=int, default=1)
    parser.add_argument("--measure-steps", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_autotune(Path(args.root).resolve(), full=args.full, warmup_steps=args.warmup_steps, measure_steps=args.measure_steps)


if __name__ == "__main__":
    main()
