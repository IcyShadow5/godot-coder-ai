from __future__ import annotations

import argparse
import gc
import json
import math
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import torch
import yaml

from .config import ModelConfig, TrainConfig
from .model import TinyGPT
from .runtime import resolve_device
from .tokenizer import load_tokenizer
from .train import amp_settings, make_optimizer

DEFAULT_PROFILE_CONFIGS = (
    "configs/corpus_starter_30m.yaml",
    "configs/corpus_balanced_90m.yaml",
    "configs/corpus_experimental_163m.yaml",
)
_RESULT_PREFIX = "PROBE_RESULT_JSON="


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely probe training profiles and GPU VRAM usage.")
    parser.add_argument("--root", default=".", help="Godot Coder project root")
    parser.add_argument("--config", action="append", default=[], help="Profile config; may be repeated")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--warmup-steps", type=int, default=1)
    parser.add_argument("--measure-steps", type=int, default=2)
    parser.add_argument("--max-batch", type=int, default=None, help="Override profile probe maximum batch")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--batch-size", type=int, default=1, help=argparse.SUPPRESS)
    return parser.parse_args()


def _read_profile(path: Path) -> tuple[dict[str, Any], ModelConfig, TrainConfig]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or "model" not in raw or "train" not in raw:
        raise ValueError(f"{path} must contain model and train mappings")
    profile = dict(raw.get("profile") or {})
    model = ModelConfig(**raw["model"])
    train = TrainConfig.from_mapping(raw["train"])
    vocab_size = int(profile.get("probe_vocab_size", model.vocab_size))
    tokenizer_path = path.parent.parent / train.tokenizer_path
    if tokenizer_path.exists():
        vocab_size = load_tokenizer(tokenizer_path).vocab_size
    model.vocab_size = vocab_size
    model.validate()
    train.validate()
    return profile, model, train


def _parameter_memory(parameter_count: int, bytes_per_parameter: int) -> float:
    return parameter_count * bytes_per_parameter / 1024**3


def _is_oom_error(exc: BaseException) -> bool:
    if isinstance(exc, torch.OutOfMemoryError):
        return True
    message = str(exc).lower()
    return "out of memory" in message or "cuda error: out of memory" in message


def _model_weight_dtype(model: TinyGPT | None) -> str | None:
    if model is None:
        return None
    try:
        return str(next(model.parameters()).dtype).replace("torch.", "")
    except StopIteration:
        return None


def _worker_result(config_path: Path, batch_size: int, device_name: str, warmup_steps: int, measure_steps: int) -> dict[str, Any]:
    profile, model_config, train_config = _read_profile(config_path)
    device = resolve_device(device_name)
    if device.type != "cuda" and device_name != "cpu":
        raise RuntimeError("CUDA is not available. Select CPU explicitly only for a functional test.")

    torch.manual_seed(train_config.seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(train_config.seed)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats(device)

    started = time.perf_counter()
    model: TinyGPT | None = None
    optimizer: torch.optim.Optimizer | None = None
    try:
        model = TinyGPT(model_config).to(device)
        raw_model = model
        compile_enabled = False
        compile_error = None
        if train_config.compile_model and hasattr(torch, "compile"):
            try:
                model = torch.compile(model, mode=train_config.compile_mode, dynamic=False)
                compile_enabled = True
            except Exception as exc:
                compile_error = f"{type(exc).__name__}: {exc}"[:500]
                model = raw_model
        optimizer = make_optimizer(raw_model, train_config, device)
        amp_enabled, amp_dtype = amp_settings(device, train_config.dtype)
        scaler = torch.amp.GradScaler("cuda", enabled=amp_enabled and amp_dtype == torch.float16)

        def autocast_context():
            if not amp_enabled:
                return nullcontext()
            return torch.autocast(device_type="cuda", dtype=amp_dtype)

        x = torch.randint(
            0,
            model_config.vocab_size,
            (batch_size, model_config.max_seq_len),
            device=device,
            dtype=torch.long,
        )
        y = torch.randint(
            0,
            model_config.vocab_size,
            (batch_size, model_config.max_seq_len),
            device=device,
            dtype=torch.long,
        )

        durations: list[float] = []
        losses: list[float] = []
        total_iterations = warmup_steps + measure_steps
        for iteration in range(total_iterations):
            optimizer.zero_grad(set_to_none=True)
            if device.type == "cuda":
                torch.cuda.synchronize(device)
            step_started = time.perf_counter()
            with autocast_context():
                output = model(x, y)
                assert output.loss is not None
                loss = output.loss
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(raw_model.parameters(), train_config.gradient_clip)
            scaler.step(optimizer)
            scaler.update()
            if device.type == "cuda":
                torch.cuda.synchronize(device)
            duration = time.perf_counter() - step_started
            if iteration >= warmup_steps:
                durations.append(duration)
                losses.append(float(loss.detach().cpu()))

        parameter_count = raw_model.parameter_count()
        total_duration = sum(durations)
        measured_tokens = batch_size * model_config.max_seq_len * len(durations)
        result: dict[str, Any] = {
            "status": "pass",
            "profile_id": profile.get("id", config_path.stem),
            "profile_title": profile.get("title", config_path.stem),
            "config": config_path.as_posix(),
            "batch_size": batch_size,
            "sequence_length": model_config.max_seq_len,
            "parameters": parameter_count,
            "amp_compute_dtype": train_config.dtype,
            "model_weight_dtype": _model_weight_dtype(raw_model),
            "compile_requested": train_config.compile_model,
            "compile_enabled": compile_enabled,
            "compile_error": compile_error,
            "attention_backend": "sdpa_auto",
            "gradient_checkpointing": model_config.gradient_checkpointing,
            "measurement_kind": "synthetic-random-token-training-step",
            "measured_steps": len(durations),
            "mean_micro_step_ms": round((total_duration / max(1, len(durations))) * 1000, 2),
            "mean_step_ms": round((total_duration / max(1, len(durations))) * 1000, 2),
            "projected_optimizer_step_ms": round((total_duration / max(1, len(durations))) * 1000 * train_config.gradient_accumulation_steps, 2),
            "gradient_accumulation_steps": train_config.gradient_accumulation_steps,
            "tokens_per_second": round(measured_tokens / max(total_duration, 1e-9), 2),
            "mean_loss": round(sum(losses) / max(1, len(losses)), 6),
            "model_weights_gib_fp32": round(_parameter_memory(parameter_count, 4), 3),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "device": str(device),
        }
        if device.type == "cuda":
            free_bytes, total_bytes = torch.cuda.mem_get_info(device)
            peak_allocated = torch.cuda.max_memory_allocated(device)
            peak_reserved = torch.cuda.max_memory_reserved(device)
            result.update({
                "gpu": torch.cuda.get_device_name(device),
                "vram_total_gib": round(total_bytes / 1024**3, 3),
                "vram_free_after_gib": round(free_bytes / 1024**3, 3),
                "peak_allocated_gib": round(peak_allocated / 1024**3, 3),
                "peak_reserved_gib": round(peak_reserved / 1024**3, 3),
                "peak_reserved_fraction": round(peak_reserved / max(total_bytes, 1), 4),
                "headroom_gib": round(max(0, total_bytes - peak_reserved) / 1024**3, 3),
                "allocator_backend": torch.cuda.get_allocator_backend(),
            })
        return result
    except (torch.OutOfMemoryError, RuntimeError) as exc:
        if not _is_oom_error(exc):
            raise
        total_gib = None
        peak_allocated = None
        peak_reserved = None
        if device.type == "cuda":
            try:
                _, total_bytes = torch.cuda.mem_get_info(device)
                total_gib = round(total_bytes / 1024**3, 3)
                peak_allocated = round(torch.cuda.max_memory_allocated(device) / 1024**3, 3)
                peak_reserved = round(torch.cuda.max_memory_reserved(device) / 1024**3, 3)
            except RuntimeError:
                pass
        return {
            "status": "oom",
            "profile_id": profile.get("id", config_path.stem),
            "profile_title": profile.get("title", config_path.stem),
            "config": config_path.as_posix(),
            "batch_size": batch_size,
            "sequence_length": model_config.max_seq_len,
            "parameters": raw_model.parameter_count() if "raw_model" in locals() else (model.parameter_count() if model is not None and hasattr(model, "parameter_count") else None),
            "amp_compute_dtype": train_config.dtype,
            "model_weight_dtype": _model_weight_dtype(raw_model),
            "compile_requested": train_config.compile_model,
            "compile_enabled": compile_enabled,
            "compile_error": compile_error,
            "attention_backend": "sdpa_auto",
            "gradient_checkpointing": model_config.gradient_checkpointing,
            "vram_total_gib": total_gib,
            "peak_allocated_gib": peak_allocated,
            "peak_reserved_gib": peak_reserved,
            "error": str(exc).splitlines()[0][:500],
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    finally:
        del optimizer
        del model
        gc.collect()
        if device.type == "cuda":
            torch.cuda.empty_cache()


def _run_worker(
    root: Path,
    config: str,
    batch_size: int,
    device: str,
    warmup_steps: int,
    measure_steps: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-u",
        "-m",
        "godot_coder.profile_probe",
        "--root",
        str(root),
        "--worker",
        "--config",
        config,
        "--batch-size",
        str(batch_size),
        "--device",
        device,
        "--warmup-steps",
        str(warmup_steps),
        "--measure-steps",
        str(measure_steps),
    ]
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        check=False,
    )
    combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    result_line = next((line for line in reversed(combined.splitlines()) if line.startswith(_RESULT_PREFIX)), None)
    if result_line is None:
        return {
            "status": "error",
            "config": config,
            "batch_size": batch_size,
            "return_code": completed.returncode,
            "error": combined[-2000:] or "probe worker returned no result",
        }
    result = json.loads(result_line[len(_RESULT_PREFIX):])
    result["return_code"] = completed.returncode
    return result


def _batch_candidates(configured_batch: int, maximum: int) -> list[int]:
    candidates = [value for value in (1, 2, 4, 6, 8) if value <= maximum]
    if maximum not in candidates:
        candidates.append(maximum)
    if configured_batch <= maximum and configured_batch not in candidates:
        candidates.append(configured_batch)
    return sorted(set(value for value in candidates if value > 0))


def _recommendation(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    passing = [item for item in profiles if (item.get("configured_result") or {}).get("status") == "pass"]
    if not passing:
        return {
            "profile_id": None,
            "reason": "Keines der Profile bestand den Probelauf. Reduziere Modell oder Kontext; CPU ist kein sinnvoller Ersatz für den Hauptlauf.",
        }

    comfortable = [
        item for item in passing
        if float((item.get("configured_result") or {}).get("peak_reserved_fraction") or 0.0) <= 0.9
    ]
    designed = next((item for item in comfortable if item.get("recommended_by_design")), None)
    if designed is not None:
        selected = designed
    elif comfortable:
        selected = max(comfortable, key=lambda item: int(item.get("beginner_order", 0)))
    else:
        selected = min(passing, key=lambda item: int(item.get("beginner_order", 999)))

    result = selected["configured_result"]
    headroom = result.get("headroom_gib")
    experimental = next((item for item in passing if item.get("profile_id") == "experimental"), None)
    extra = ""
    if experimental is not None and selected.get("profile_id") != "experimental":
        extra = " Das Experimental-Profil bestand ebenfalls, bleibt aber bewusst ein Grenztest statt der Standardempfehlung."
    return {
        "profile_id": selected.get("profile_id"),
        "profile_title": selected.get("profile_title"),
        "reason": (
            "Empfohlenes Profil mit kontrollierter VRAM-Reserve"
            + (f" ({headroom:.2f} GiB gemessener Headroom)." if isinstance(headroom, (int, float)) else ".")
            + extra
        ),
    }


def run_probe(
    root: Path,
    config_paths: list[str],
    *,
    device: str,
    warmup_steps: int,
    measure_steps: int,
    max_batch_override: int | None,
) -> dict[str, Any]:
    started = time.time()
    profiles: list[dict[str, Any]] = []
    for profile_index, relative in enumerate(config_paths, start=1):
        config_path = (root / relative).resolve()
        profile_meta, model_config, train_config = _read_profile(config_path)
        configured_batch = train_config.batch_size
        maximum = max_batch_override or int(profile_meta.get("probe_max_batch_size", configured_batch))
        maximum = max(configured_batch, maximum)
        print(
            f"profile_probe={profile_index}/{len(config_paths)} id={profile_meta.get('id', config_path.stem)} "
            f"params_pending context={model_config.max_seq_len} configured_batch={configured_batch}",
            flush=True,
        )
        attempts: list[dict[str, Any]] = []
        for batch_size in _batch_candidates(configured_batch, maximum):
            print(f"  batch={batch_size} phase=probe", flush=True)
            result = _run_worker(root, relative, batch_size, device, warmup_steps, measure_steps)
            attempts.append(result)
            if result.get("status") == "pass":
                print(
                    f"  batch={batch_size} status=pass peak_reserved={result.get('peak_reserved_gib', 'n/a')}GiB "
                    f"tok/s={result.get('tokens_per_second', 'n/a')}",
                    flush=True,
                )
            else:
                print(f"  batch={batch_size} status={result.get('status')} error={result.get('error', '')[:180]}", flush=True)
                if result.get("status") == "oom":
                    break
        configured_result = next((item for item in attempts if item.get("batch_size") == configured_batch), None)
        if configured_result is None:
            configured_result = attempts[0] if attempts else {"status": "error", "error": "no probe attempt"}
        passed_batches = [int(item["batch_size"]) for item in attempts if item.get("status") == "pass"]
        first_failed = next((int(item["batch_size"]) for item in attempts if item.get("status") == "oom"), None)
        profiles.append({
            "profile_id": profile_meta.get("id", config_path.stem),
            "profile_title": profile_meta.get("title", config_path.stem),
            "method": profile_meta.get("method", ""),
            "description": profile_meta.get("description", ""),
            "beginner_order": int(profile_meta.get("beginner_order", profile_index)),
            "recommended_by_design": bool(profile_meta.get("recommended", False)),
            "risk": profile_meta.get("risk", "unknown"),
            "config": relative,
            "configured_batch_size": configured_batch,
            "gradient_accumulation_steps": train_config.gradient_accumulation_steps,
            "effective_batch_sequences": configured_batch * train_config.gradient_accumulation_steps,
            "effective_tokens_per_optimizer_step": configured_batch * train_config.gradient_accumulation_steps * model_config.max_seq_len,
            "attempts": attempts,
            "configured_result": configured_result,
            "largest_passing_micro_batch": max(passed_batches) if passed_batches else None,
            "first_oom_micro_batch": first_failed,
            "limit_is_lower_bound": bool(passed_batches) and first_failed is None and max(passed_batches) >= maximum,
        })

    report = {
        "format": "godot-coder-vram-profile-probe",
        "format_version": 1,
        "created_at": time.time(),
        "duration_seconds": round(time.time() - started, 3),
        "device_request": device,
        "warmup_steps": warmup_steps,
        "measure_steps": measure_steps,
        "profiles": profiles,
    }
    report["recommendation"] = _recommendation(profiles)
    reports = root / "reports" / "hardware"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report_path = reports / f"vram_probe_{stamp}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (reports / "vram_probe_latest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"report: {report_path.relative_to(root).as_posix()}", flush=True)
    recommendation = report["recommendation"]
    print(
        f"recommendation={recommendation.get('profile_id') or 'none'} reason={recommendation.get('reason')}",
        flush=True,
    )
    return report


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    configs = args.config or list(DEFAULT_PROFILE_CONFIGS)
    if args.worker:
        if len(configs) != 1:
            raise ValueError("worker mode requires exactly one --config")
        config_path = (root / configs[0]).resolve()
        try:
            result = _worker_result(
                config_path,
                args.batch_size,
                args.device,
                max(0, args.warmup_steps),
                max(1, args.measure_steps),
            )
            print(_RESULT_PREFIX + json.dumps(result, ensure_ascii=True), flush=True)
        except Exception as exc:
            result = {
                "status": "error",
                "config": configs[0],
                "batch_size": args.batch_size,
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(_RESULT_PREFIX + json.dumps(result, ensure_ascii=True), flush=True)
            raise SystemExit(1)
        return
    run_probe(
        root,
        configs,
        device=args.device,
        warmup_steps=max(0, args.warmup_steps),
        measure_steps=max(1, args.measure_steps),
        max_batch_override=args.max_batch,
    )


if __name__ == "__main__":
    main()
