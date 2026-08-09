from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
import uuid
import warnings
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable

import numpy as np
import torch

from .checkpoint import load_checkpoint, restore_rng_state, save_checkpoint
from .config import ModelConfig, TrainConfig, load_config
from .data import TokenStream
from .model import TinyGPT
from .project import find_project_root, project_relative, resolve_project_path
from .runtime import resolve_device
from .tokenizer import load_tokenizer
from .metrics import MetricEvent, MetricsCollector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train Godot Coder AI from random weights.")
    parser.add_argument("--config", default="configs/tiny.yaml")
    parser.add_argument("--resume", default=None)
    parser.add_argument("--max-steps", type=int, default=None, help="Safe temporary cap, used by smoke runs")
    return parser.parse_args()


def set_seeds(seed: int) -> None:
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def learning_rate(step: int, config: TrainConfig, max_steps: int) -> float:
    if step < config.warmup_steps:
        return config.learning_rate * (step + 1) / max(1, config.warmup_steps)
    decay_steps = max(1, max_steps - config.warmup_steps)
    progress = min(1.0, (step - config.warmup_steps) / decay_steps)
    coefficient = 0.5 * (1.0 + math.cos(math.pi * progress))
    return config.min_learning_rate + coefficient * (config.learning_rate - config.min_learning_rate)


def make_optimizer(model: TinyGPT, config: TrainConfig, device: torch.device) -> torch.optim.AdamW:
    # When tie_embeddings is on, the lm_head and token_embedding share the same
    # 2D weight. Embeddings should never receive weight decay — exclude the
    # shared weight from the decay group.
    tied_weight = model.token_embedding.weight if model.config.tie_embeddings else None
    decay = [p for p in model.parameters() if p.requires_grad and p.dim() >= 2 and p is not tied_weight]
    no_decay = [p for p in model.parameters() if p.requires_grad and (p.dim() < 2 or p is tied_weight)]
    groups = [{"params": decay, "weight_decay": config.weight_decay}, {"params": no_decay, "weight_decay": 0.0}]
    kwargs = {"lr": config.learning_rate, "betas": (config.beta1, config.beta2)}
    if device.type == "cuda":
        try:
            return torch.optim.AdamW(groups, fused=True, **kwargs)
        except (TypeError, RuntimeError):
            pass
    return torch.optim.AdamW(groups, **kwargs)


def amp_settings(device: torch.device, dtype_name: str) -> tuple[bool, torch.dtype | None]:
    if device.type != "cuda" or dtype_name == "float32":
        return False, None
    dtype = torch.float16 if dtype_name == "float16" else torch.bfloat16
    if dtype == torch.bfloat16 and not torch.cuda.is_bf16_supported():
        raise RuntimeError("bfloat16 requested, but this CUDA device does not support it")
    return True, dtype


def _eval_cache_path(data_dir: Path, stream: TokenStream, config: TrainConfig, model_config: ModelConfig) -> Path:
    fingerprint = (stream.dataset_fingerprint or "legacy")[:16]
    return data_dir / "eval_cache" / f"fixed_{fingerprint}_{model_config.max_seq_len}_{config.eval_batches}_{config.batch_size}_{config.evaluation_seed}.npy"


def fixed_evaluation_windows(data_dir: Path, stream: TokenStream, config: TrainConfig, model_config: ModelConfig) -> np.ndarray:
    path = _eval_cache_path(data_dir, stream, config, model_config)
    count = config.eval_batches * config.batch_size
    if path.exists():
        windows = np.load(path, allow_pickle=False)
        if windows.shape == (count, 2):
            return windows
    windows = stream.fixed_windows(model_config.max_seq_len, count, config.evaluation_seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.npy")
    np.save(temporary, windows, allow_pickle=False)
    temporary.replace(path)
    return windows


@torch.no_grad()
def evaluate(
    model: torch.nn.Module,
    stream: TokenStream,
    config: TrainConfig,
    model_config: ModelConfig,
    device: torch.device,
    rng: np.random.Generator,
    autocast_context: Callable[[], Any],
    fixed_windows: np.ndarray | None = None,
) -> float:
    model.eval()
    losses: list[float] = []
    if config.evaluation_mode == "fixed":
        if fixed_windows is None:
            raise ValueError("fixed evaluation requires cached windows")
        for offset in range(0, len(fixed_windows), config.batch_size):
            x, y = stream.batch_at(fixed_windows[offset:offset + config.batch_size], model_config.max_seq_len, device)
            with autocast_context():
                output = model(x, y)
            losses.append(float(output.loss.detach().cpu()))
    elif config.evaluation_mode == "sliding":
        stride = config.evaluation_stride or model_config.max_seq_len
        remaining = config.eval_batches
        window_buffer: list[list[int]] = []
        for shard_index, shard in enumerate(stream.shards):
            for start in range(0, max(1, len(shard) - model_config.max_seq_len - 1), stride):
                window_buffer.append([shard_index, start])
                if len(window_buffer) >= config.batch_size or len(window_buffer) >= remaining:
                    consumed = min(config.batch_size, len(window_buffer))
                    batch = np.asarray(window_buffer[:consumed], dtype=np.int64)
                    x, y = stream.batch_at(batch, model_config.max_seq_len, device)
                    with autocast_context():
                        output = model(x, y)
                    batch_loss = float(output.loss.detach().cpu())
                    losses.extend([batch_loss] * consumed)
                    window_buffer = window_buffer[consumed:]
                    remaining -= consumed
                if remaining <= 0:
                    break
            if remaining <= 0:
                break
    else:
        for _ in range(config.eval_batches):
            x, y = stream.sample_batch(config.batch_size, model_config.max_seq_len, device, rng)
            with autocast_context():
                output = model(x, y)
            losses.append(float(output.loss.detach().cpu()))
    model.train()
    if not losses:
        raise RuntimeError("evaluation produced no loss")
    return sum(losses) / len(losses)


class BatchPrefetcher:
    """One-worker CPU sampler. CUDA transfer remains on the training thread."""
    def __init__(self, stream: TokenStream, batch_size: int, seq_len: int, rng: np.random.Generator, enabled: bool) -> None:
        self.stream, self.batch_size, self.seq_len, self.rng = stream, batch_size, seq_len, rng
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="batch-prefetch") if enabled else None
        self.future: Future[tuple[torch.Tensor, torch.Tensor]] | None = None
        if self.executor:
            self.future = self.executor.submit(self._prefetch_sample)

    def _prefetch_sample(self) -> tuple[torch.Tensor, torch.Tensor]:
        x, y = self.stream.sample_batch(self.batch_size, self.seq_len, torch.device("cpu"), self.rng)
        # Pin on the worker thread so the main thread only does the async transfer.
        return x.pin_memory(), y.pin_memory()

    def next(self, device: torch.device) -> tuple[torch.Tensor, torch.Tensor]:
        if self.executor is None or self.future is None:
            return self.stream.sample_batch(self.batch_size, self.seq_len, device, self.rng)
        x, y = self.future.result()
        self.future = self.executor.submit(self._prefetch_sample)
        if device.type == "cuda":
            return x.to(device, non_blocking=True), y.to(device, non_blocking=True)
        return x.to(device), y.to(device)

    def close(self) -> None:
        if self.executor:
            self.executor.shutdown(wait=True, cancel_futures=True)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n"); handle.flush()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _profile_metadata(config_path: Path) -> dict[str, Any]:
    try:
        import yaml
        return dict((yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}).get("profile") or {})
    except (OSError, ValueError, TypeError):
        return {}


def _project_root_for(config_path: Path) -> Path:
    if config_path.parent.name.lower() == "configs":
        return config_path.parent.parent.resolve()
    try:
        return find_project_root(start=config_path)
    except FileNotFoundError:
        return Path.cwd().resolve()


def _require_under(path: Path, parent: Path, label: str) -> None:
    try:
        path.relative_to(parent.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must stay under {parent}") from exc


def _try_compile(model: TinyGPT, config: TrainConfig) -> tuple[torch.nn.Module, dict[str, Any]]:
    info = {"requested": config.compile_model, "enabled": False, "mode": config.compile_mode, "fallback_error": None}
    if not config.compile_model:
        return model, info
    if not hasattr(torch, "compile"):
        info["fallback_error"] = "torch.compile unavailable"
        return model, info
    try:
        compiled = torch.compile(model, mode=config.compile_mode, dynamic=False)
        info["enabled"] = True
        return compiled, info
    except Exception as exc:  # compilation must never make a stable profile unusable
        info["fallback_error"] = f"{type(exc).__name__}: {exc}"[:500]
        warnings.warn(f"torch.compile fallback to eager: {info['fallback_error']}")
        return model, info


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()
    project_root = _project_root_for(config_path)
    # Resolve the resume step before validating the config so a resume that is
    # already past warmup is not rejected by the fresh-run warmup check.
    # This deliberately loads the checkpoint twice: a cheap CPU-only peek for
    # the step number here, then the real device load after validation. The
    # peek is far cheaper than the full load and keeps the validation order
    # simple instead of threading the resume path through load_config.
    resume_start_step = 0
    if args.resume:
        resume_path = resolve_project_path(project_root, args.resume)
        _require_under(resume_path, project_root / "checkpoints", "resume checkpoint")
        peek = load_checkpoint(resume_path, map_location="cpu")
        resume_start_step = int(peek["step"])
        del peek
    model_config, train_config = load_config(config_path, start_step=resume_start_step)
    profile = _profile_metadata(config_path)
    tokenizer_path = resolve_project_path(project_root, train_config.tokenizer_path)
    data_dir = resolve_project_path(project_root, train_config.data_dir)
    output_dir = resolve_project_path(project_root, train_config.output_dir)
    _require_under(tokenizer_path, project_root / "artifacts", "tokenizer_path")
    _require_under(data_dir, project_root / "data", "data_dir")
    _require_under(output_dir, project_root / "checkpoints", "output_dir")

    tokenizer = load_tokenizer(tokenizer_path)
    model_config.vocab_size = tokenizer.vocab_size; model_config.validate()
    manifest_path = data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("tokenizer_fingerprint") != tokenizer.fingerprint():
        raise ValueError("dataset and tokenizer fingerprints do not match; prepare the dataset again")
    if int(manifest.get("vocab_size", -1)) != tokenizer.vocab_size:
        raise ValueError("dataset vocabulary metadata does not match the tokenizer")

    device = resolve_device(train_config.device)
    set_seeds(train_config.seed)
    train_rng, eval_rng = np.random.default_rng(train_config.seed), np.random.default_rng(train_config.seed + 1)
    train_stream, val_stream = TokenStream.from_data_dir(data_dir, "train"), TokenStream.from_data_dir(data_dir, "val")
    raw_model = TinyGPT(model_config).to(device)
    optimizer = make_optimizer(raw_model, train_config, device)
    amp_enabled, amp_dtype = amp_settings(device, train_config.dtype)
    scaler = torch.amp.GradScaler("cuda", enabled=amp_enabled and amp_dtype == torch.float16)

    def autocast_context():
        return torch.autocast(device_type="cuda", dtype=amp_dtype) if amp_enabled else nullcontext()

    start_step = 0; best_val_loss = float("inf"); best_step: int | None = None; resume_path: Path | None = None
    if args.resume:
        resume_path = resolve_project_path(project_root, args.resume); _require_under(resume_path, project_root / "checkpoints", "resume checkpoint")
        payload = load_checkpoint(resume_path, map_location=device)
        if payload["tokenizer_fingerprint"] != tokenizer.fingerprint():
            raise ValueError("checkpoint tokenizer does not match current tokenizer")
        if payload["model_config"] != model_config.to_dict():
            raise ValueError("checkpoint model configuration does not match current configuration")
        raw_model.load_state_dict(payload["model_state"]); optimizer.load_state_dict(payload["optimizer_state"]); scaler.load_state_dict(payload["scaler_state"])
        restore_rng_state(payload["rng_state"])
        if payload.get("data_rng_state"):
            train_rng.bit_generator.state = payload["data_rng_state"]["train"]; eval_rng.bit_generator.state = payload["data_rng_state"]["eval"]
        start_step = int(payload["step"]); best_val_loss = float(payload.get("best_val_loss", float("inf"))); best_step = payload.get("best_step")

    tokens_per_micro_batch = train_config.batch_size * model_config.max_seq_len
    tokens_per_optimizer_step = tokens_per_micro_batch * train_config.gradient_accumulation_steps
    max_steps = train_config.resolve_max_steps(train_tokens=len(train_stream), tokens_per_optimizer_step=tokens_per_optimizer_step, start_step=start_step)
    if args.max_steps is not None:
        max_steps = min(max_steps, start_step + args.max_steps)
    passes = max_steps * tokens_per_optimizer_step / max(1, len(train_stream))
    if passes > train_config.max_dataset_passes_block and not train_config.allow_excessive_dataset_passes:
        raise ValueError(
            f"planned dataset passes ({passes:.2f}) exceed safety limit {train_config.max_dataset_passes_block:.2f}; "
            "increase data, lower token budget, or explicitly allow excessive passes"
        )
    if passes > train_config.max_dataset_passes_warning:
        warnings.warn(f"high planned dataset passes: {passes:.2f}")
    if start_step >= max_steps:
        raise ValueError(f"checkpoint is already at step {start_step}, resolved end step is {max_steps}")
    eval_interval_steps = max(1, math.ceil(train_config.validation_interval_tokens / tokens_per_optimizer_step)) if train_config.validation_interval_tokens else train_config.eval_interval
    fixed_windows = fixed_evaluation_windows(data_dir, val_stream, train_config, model_config) if train_config.evaluation_mode == "fixed" else None
    model, compile_info = _try_compile(raw_model, train_config)

    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{output_dir.name}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    metrics_path = output_dir / f"metrics_{run_id}.jsonl"
    summary_latest_path = output_dir / "training_summary_latest.json"
    report_path = project_root / "reports" / "training" / f"{run_id}.json"
    planned_run_tokens = (max_steps - start_step) * tokens_per_optimizer_step
    manifest_hash = _file_sha256(manifest_path)
    gpu_info = None
    if device.type == "cuda":
        properties = torch.cuda.get_device_properties(device)
        gpu_info = {"name": torch.cuda.get_device_name(device), "compute_capability": ".".join(map(str, torch.cuda.get_device_capability(device))), "total_vram_gib": round(properties.total_memory / 1024**3, 3), "cuda_build": torch.version.cuda, "attention_backend": "sdpa_auto"}
        torch.cuda.reset_peak_memory_stats(device)
    header: dict[str, Any] = {
        "format": "godot-coder-training-run", "format_version": 3, "run_id": run_id, "started_at": time.time(),
        "project_root": str(project_root), "config": project_relative(config_path, project_root), "profile": profile,
        "resume": project_relative(resume_path, project_root) if resume_path else None, "start_step": start_step, "max_steps": max_steps,
        "model": model_config.to_dict(), "train": train_config.to_dict(), "compile": compile_info,
        "parameters": raw_model.parameter_count(), "device": str(device), "gpu": gpu_info,
        "dataset": {"manifest": project_relative(manifest_path, project_root), "manifest_sha256": manifest_hash, "dataset_fingerprint": manifest.get("dataset_fingerprint"), "tokenizer_fingerprint": tokenizer.fingerprint(), "vocab_size": tokenizer.vocab_size, "train_tokens_unique_stream": len(train_stream), "validation_tokens_unique_stream": len(val_stream), "train_files": len(manifest.get("train_files", [])), "validation_files": len(manifest.get("val_files", [])), "format_version": manifest.get("format_version"), "shards": len(train_stream.shards)},
        "token_accounting": {"micro_batch_sequences": train_config.batch_size, "sequence_length": model_config.max_seq_len, "gradient_accumulation_steps": train_config.gradient_accumulation_steps, "effective_batch_sequences": train_config.batch_size * train_config.gradient_accumulation_steps, "tokens_per_micro_batch": tokens_per_micro_batch, "tokens_per_optimizer_step": tokens_per_optimizer_step, "planned_run_tokens": planned_run_tokens, "cumulative_planned_tokens": max_steps * tokens_per_optimizer_step, "equivalent_dataset_passes_planned": round(passes, 4)},
        "evaluation": {"mode": train_config.evaluation_mode, "interval_steps": eval_interval_steps, "fixed_cache": project_relative(_eval_cache_path(data_dir, val_stream, train_config, model_config), project_root) if fixed_windows is not None else None},
    }
    print(f"run_id={run_id}\nDevice: {device}")
    if gpu_info: print(f"GPU: {gpu_info['name']} | CC {gpu_info['compute_capability']} | VRAM {gpu_info['total_vram_gib']:.2f} GiB | CUDA {gpu_info['cuda_build']}")
    print(f"Profile: {profile.get('title', config_path.stem)} | Parameters: {raw_model.parameter_count():,}")
    print(f"Dataset: train_unique={len(train_stream):,} val_unique={len(val_stream):,} shards={len(train_stream.shards)} vocab={tokenizer.vocab_size:,}")
    print(f"Plan: start_step={start_step:,} end_step={max_steps:,} run_tokens={planned_run_tokens:,} dataset_passes~={passes:.2f}")
    print(f"Precision: AMP_compute={train_config.dtype} weights=float32 checkpointing={model_config.gradient_checkpointing} compile={compile_info['enabled']} prefetch={train_config.prefetch_batches > 0}")
    print("RUN_HEADER_JSON=" + json.dumps(header, ensure_ascii=True)); _append_jsonl(metrics_path, {"event": "run_start", **header})
    run_metrics = MetricsCollector(output_dir / f"events_{run_id}.jsonl")

    model.train(); prefetcher = BatchPrefetcher(train_stream, train_config.batch_size, model_config.max_seq_len, train_rng, train_config.prefetch_batches > 0)
    run_started = time.perf_counter(); interval_started = time.perf_counter(); total_training_seconds = total_validation_seconds = total_checkpoint_seconds = 0.0
    running_loss = 0.0; interval_steps = 0; last_step_completed = start_step; final_val_loss = None; status = "completed"; error_message = None
    run_peak_allocated_gib = run_peak_reserved_gib = 0.0; no_improvement = 0
    try:
        for step in range(start_step, max_steps):
            step_started = time.perf_counter(); optimizer.zero_grad(set_to_none=True); accumulated_loss = 0.0
            for _ in range(train_config.gradient_accumulation_steps):
                x, y = prefetcher.next(device)
                with autocast_context():
                    output = model(x, y)
                    if output.loss is None: raise RuntimeError("training produced no loss")
                    loss = output.loss / train_config.gradient_accumulation_steps
                scaler.scale(loss).backward(); accumulated_loss += float(loss.detach().cpu())
            scaler.unscale_(optimizer); gradient_norm = torch.nn.utils.clip_grad_norm_(raw_model.parameters(), train_config.gradient_clip)
            current_lr = learning_rate(step, train_config, max_steps)
            for group in optimizer.param_groups: group["lr"] = current_lr
            scaler.step(optimizer); scaler.update()
            if device.type == "cuda": torch.cuda.synchronize(device)
            elapsed = time.perf_counter() - step_started; total_training_seconds += elapsed
            running_loss += accumulated_loss; interval_steps += 1; last_step_completed = step + 1
            should_log = interval_steps >= train_config.log_interval or step + 1 == max_steps
            should_eval = (step + 1) % eval_interval_steps == 0 or step + 1 == max_steps
            if should_log:
                interval_seconds = time.perf_counter() - interval_started
                interval_tokens = interval_steps * tokens_per_optimizer_step
                tps = interval_tokens / max(interval_seconds, 1e-9); average_loss = running_loss / interval_steps
                total_tokens = (step + 1) * tokens_per_optimizer_step; run_tokens = (step + 1 - start_step) * tokens_per_optimizer_step
                metric = {"event": "train_interval", "time": time.time(), "step": step + 1, "interval_steps": interval_steps, "progress": round((step + 1) / max_steps, 6), "loss": average_loss, "learning_rate": current_lr, "gradient_norm": float(gradient_norm), "training_seconds": interval_seconds, "mean_optimizer_step_ms": interval_seconds / interval_steps * 1000, "interval_tokens": interval_tokens, "run_tokens_seen": run_tokens, "cumulative_tokens_seen": total_tokens, "tokens_per_second": tps, "equivalent_dataset_passes_seen": total_tokens / max(1, len(train_stream))}
                memory = ""
                if device.type == "cuda":
                    alloc = torch.cuda.max_memory_allocated(device) / 1024**3; reserved = torch.cuda.max_memory_reserved(device) / 1024**3
                    run_peak_allocated_gib = max(run_peak_allocated_gib, alloc); run_peak_reserved_gib = max(run_peak_reserved_gib, reserved)
                    metric.update({"peak_vram_allocated_gib": alloc, "peak_vram_reserved_gib": reserved}); memory = f" vram_alloc={alloc:.2f}GiB vram_reserved={reserved:.2f}GiB"; torch.cuda.reset_peak_memory_stats(device)
                _append_jsonl(metrics_path, metric)
                run_metrics.record(MetricEvent.TOKEN_USAGE, step=step + 1, tokens=interval_tokens)
                print(f"step={step + 1} progress={(step + 1) / max_steps * 100:.1f}% loss={average_loss:.4f} lr={current_lr:.2e} grad={float(gradient_norm):.3f} tokens={total_tokens:,} tok/s={tps:,.0f} step_ms={metric['mean_optimizer_step_ms']:.1f}{memory}")
                running_loss = 0.0; interval_steps = 0; interval_started = time.perf_counter()
            improved = False
            if should_eval:
                started = time.perf_counter(); val_loss = evaluate(model, val_stream, train_config, model_config, device, eval_rng, autocast_context, fixed_windows)
                if device.type == "cuda": torch.cuda.synchronize(device)
                duration = time.perf_counter() - started; total_validation_seconds += duration; final_val_loss = val_loss
                significant = val_loss < best_val_loss - train_config.early_stopping_min_delta
                improved = val_loss < best_val_loss
                if improved: best_val_loss = val_loss; best_step = step + 1
                no_improvement = 0 if significant else no_improvement + 1
                run_metrics.record(MetricEvent.RUNTIME_SUCCESS if val_loss < 100 else MetricEvent.RUNTIME_ERROR, step=step + 1, duration_seconds=duration, details={"loss": val_loss, "perplexity": math.exp(min(val_loss, 20))})
                _append_jsonl(metrics_path, {"event": "validation", "time": time.time(), "step": step + 1, "loss": val_loss, "perplexity": math.exp(min(val_loss, 20)), "duration_seconds": duration, "improved": improved, "best_val_loss": best_val_loss, "best_step": best_step, "no_improvement_count": no_improvement})
                print(f"validation step={step + 1} loss={val_loss:.4f} perplexity={math.exp(min(val_loss,20)):.2f} seconds={duration:.2f} best_step={best_step or '-'} patience={no_improvement}/{train_config.early_stopping_patience}")
            periodic_save = (step + 1) % train_config.save_interval == 0 or step + 1 == max_steps
            should_save = improved or (periodic_save and not train_config.save_best_only)
            if should_save:
                started = time.perf_counter(); saved = save_checkpoint(output_dir, step=step + 1, model=raw_model, optimizer=optimizer, scaler=scaler, model_config=model_config.to_dict(), train_config=train_config.to_dict(), tokenizer_fingerprint=tokenizer.fingerprint(), best_val_loss=best_val_loss, best_step=best_step, data_rng_state={"train": train_rng.bit_generator.state, "eval": eval_rng.bit_generator.state}, is_best=improved, keep_last=train_config.keep_last_checkpoints)
                duration = time.perf_counter() - started; total_checkpoint_seconds += duration
                _append_jsonl(metrics_path, {"event": "checkpoint", "time": time.time(), "step": step + 1, "path": project_relative(saved, project_root), "is_best": improved, "duration_seconds": duration})
                print(f"checkpoint: {project_relative(saved, project_root)}{' (best)' if improved else ''} seconds={duration:.2f}")
            if train_config.early_stopping_enabled and should_eval and no_improvement >= train_config.early_stopping_patience:
                status = "early_stopped"; print(f"early_stopping: step={step + 1} best_step={best_step} best_val_loss={best_val_loss:.4f}"); break
    except KeyboardInterrupt:
        status = "stopped"; error_message = "Training interrupted by user"
    except Exception as exc:
        status = "failed"; error_message = f"{type(exc).__name__}: {exc}"
        # Emergency checkpoint — save model state before crashing so the user
        # can resume from the last completed step instead of the last save_interval.
        try:
            emergency_path = output_dir / f"emergency_step{last_step_completed}.pt"
            saved = save_checkpoint(
                output_dir,
                step=last_step_completed,
                model=raw_model,
                optimizer=optimizer,
                scaler=scaler,
                model_config=model_config.to_dict(),
                train_config=train_config.to_dict(),
                tokenizer_fingerprint=tokenizer.fingerprint(),
                best_val_loss=best_val_loss,
                best_step=best_step,
                data_rng_state={"train": train_rng.bit_generator.state, "eval": eval_rng.bit_generator.state},
                is_best=False,
                keep_last=0,
            )
            saved.replace(emergency_path)
            print(f"EMERGENCY_CHECKPOINT saved: {emergency_path}")
        except Exception:
            pass  # Don't let a failed emergency save mask the original error
        raise
    finally:
        prefetcher.close()
        wall_seconds = time.perf_counter() - run_started
        if device.type == "cuda":
            try:
                torch.cuda.synchronize(device); run_peak_allocated_gib = max(run_peak_allocated_gib, torch.cuda.max_memory_allocated(device) / 1024**3); run_peak_reserved_gib = max(run_peak_reserved_gib, torch.cuda.max_memory_reserved(device) / 1024**3)
            except RuntimeError: pass
        run_steps = max(0, last_step_completed - start_step); run_tokens = run_steps * tokens_per_optimizer_step; cumulative_tokens = last_step_completed * tokens_per_optimizer_step
        summary = {**header, "status": status, "error": error_message, "finished_at": time.time(), "wall_seconds": round(wall_seconds, 3), "training_seconds": round(total_training_seconds, 3), "validation_seconds": round(total_validation_seconds, 3), "checkpoint_seconds": round(total_checkpoint_seconds, 3), "last_step": last_step_completed, "run_steps_completed": run_steps, "best_step": best_step, "best_val_loss": None if math.isinf(best_val_loss) else best_val_loss, "final_val_loss": final_val_loss, "run_tokens_seen": run_tokens, "cumulative_tokens_seen": cumulative_tokens, "equivalent_dataset_passes_seen": round(cumulative_tokens / max(1, len(train_stream)), 6), "average_tokens_per_second": round(run_tokens / max(total_training_seconds, 1e-9), 3), "average_training_tokens_per_second": round(run_tokens / max(total_training_seconds, 1e-9), 3), "average_wall_tokens_per_second": round(run_tokens / max(wall_seconds, 1e-9), 3), "peak_vram_allocated_gib": round(run_peak_allocated_gib, 3) if device.type == "cuda" else None, "peak_vram_reserved_gib": round(run_peak_reserved_gib, 3) if device.type == "cuda" else None, "metrics_jsonl": project_relative(metrics_path, project_root)}
        _append_jsonl(metrics_path, {"event": "run_end", **summary}); _atomic_json(summary_latest_path, summary); _atomic_json(report_path, summary)
        print("TRAINING_SUMMARY_JSON=" + json.dumps(summary, ensure_ascii=True)); print(f"training_report: {project_relative(report_path, project_root)}")


if __name__ == "__main__":
    main()
