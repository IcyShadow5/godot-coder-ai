from __future__ import annotations

import argparse
import json
import math
import time
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import torch

from .checkpoint import load_checkpoint
from .config import ModelConfig
from .data import TokenStream
from .model import TinyGPT
from .project import find_project_root, project_relative, resolve_project_path
from .runtime import resolve_device
from .tokenizer import load_tokenizer
from .train import amp_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure loss for a checkpoint on validation or test data.")
    parser.add_argument("--checkpoint", default="checkpoints/tiny/latest.pt")
    parser.add_argument("--tokenizer", default=None, help="Override tokenizer; otherwise use the checkpoint config")
    parser.add_argument("--data-dir", default=None, help="Override dataset; otherwise use the checkpoint config")
    parser.add_argument("--split", choices=("val", "test"), default="val")
    parser.add_argument("--batches", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=None, help="Defaults to the checkpoint training batch size")
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()
    if args.batches <= 0:
        raise ValueError("batches must be positive")

    checkpoint_path = Path(args.checkpoint).expanduser().resolve()
    try:
        project_root = find_project_root(start=checkpoint_path)
    except FileNotFoundError:
        project_root = Path.cwd().resolve()
    device = resolve_device(args.device)
    payload = load_checkpoint(checkpoint_path, map_location=device)
    train_config = payload.get("train_config", {})

    tokenizer_path = resolve_project_path(project_root, args.tokenizer or train_config.get("tokenizer_path", "artifacts/tokenizer.json"))
    data_dir = resolve_project_path(project_root, args.data_dir or train_config.get("data_dir", "data/processed"))
    tokenizer = load_tokenizer(tokenizer_path)
    if payload["tokenizer_fingerprint"] != tokenizer.fingerprint():
        raise ValueError("checkpoint and tokenizer do not match")
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("tokenizer_fingerprint") != tokenizer.fingerprint():
        raise ValueError("dataset and tokenizer do not match")

    config = ModelConfig(**payload["model_config"])
    model = TinyGPT(config).to(device)
    model.load_state_dict(payload["model_state"])
    model.eval()
    stream = TokenStream.from_data_dir(data_dir, args.split)
    rng = np.random.default_rng(2026)
    batch_size = args.batch_size or int(train_config.get("batch_size", 1))
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    dtype_name = str(train_config.get("dtype", "float32"))
    amp_enabled, amp_dtype = amp_settings(device, dtype_name)
    losses: list[float] = []
    started = time.perf_counter()
    with torch.inference_mode():
        for _ in range(args.batches):
            x, y = stream.sample_batch(batch_size, config.max_seq_len, device, rng)
            context = torch.autocast("cuda", dtype=amp_dtype) if amp_enabled else nullcontext()
            with context:
                output = model(x, y)
            if output.loss is None:
                raise RuntimeError("evaluation produced no loss")
            losses.append(float(output.loss.cpu()))
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    elapsed = time.perf_counter() - started
    loss = sum(losses) / len(losses)
    tokens = args.batches * batch_size * config.max_seq_len
    result = {
        "checkpoint": project_relative(checkpoint_path, project_root),
        "split": args.split,
        "batches": args.batches,
        "batch_size": batch_size,
        "tokens": tokens,
        "loss": loss,
        "perplexity": math.exp(min(loss, 20)),
        "seconds": elapsed,
        "tokens_per_second": tokens / max(elapsed, 1e-9),
        "dtype": dtype_name,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
