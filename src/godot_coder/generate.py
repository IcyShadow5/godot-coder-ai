from __future__ import annotations
"""CLI entry point for text generation with a trained checkpoint."""

import argparse
import math
from pathlib import Path

import torch

from .checkpoint import load_checkpoint
from .config import ModelConfig
from .model import TinyGPT
from .project import find_project_root, resolve_project_path
from .runtime import resolve_device
from .sampling import DEFAULT_REPETITION_PENALTY, DEFAULT_TEMPERATURE, DEFAULT_TOP_K, DEFAULT_TOP_P
from .tokenizer import load_tokenizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate GDScript from a trained checkpoint.")
    parser.add_argument("--checkpoint", default="checkpoints/tiny/latest.pt")
    parser.add_argument("--tokenizer", default=None, help="Override tokenizer; otherwise use the checkpoint config")
    parser.add_argument("--prompt", default="extends Node\n\n")
    parser.add_argument("--prompt-file", default=None)
    parser.add_argument("--max-new-tokens", type=int, default=300)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--top-p", type=float, default=DEFAULT_TOP_P)
    parser.add_argument("--repetition-penalty", type=float, default=DEFAULT_REPETITION_PENALTY)
    parser.add_argument("--output", default=None)
    parser.add_argument("--suffix-only", action="store_true", help="Print/write only tokens generated after the prompt")
    parser.add_argument("--device", default="auto")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.max_new_tokens < 1:
        raise ValueError("max_new_tokens must be positive")
    if not math.isfinite(args.temperature) or args.temperature < 0:
        raise ValueError("temperature must be finite and non-negative")
    if args.top_k < 0:
        raise ValueError("top_k cannot be negative")

    checkpoint_path = Path(args.checkpoint).expanduser().resolve()
    try:
        project_root = find_project_root(start=checkpoint_path)
    except FileNotFoundError:
        project_root = Path.cwd().resolve()
    device = resolve_device(args.device)
    payload = load_checkpoint(checkpoint_path, map_location=device)

    configured_tokenizer = payload.get("train_config", {}).get("tokenizer_path", "artifacts/tokenizer.json")
    tokenizer_path = resolve_project_path(project_root, args.tokenizer or configured_tokenizer)
    tokenizer = load_tokenizer(tokenizer_path)
    if payload["tokenizer_fingerprint"] != tokenizer.fingerprint():
        raise ValueError("checkpoint and tokenizer do not match")

    config = ModelConfig(**payload["model_config"])
    model = TinyGPT(config).to(device)
    model.load_state_dict(payload["model_state"])
    model.eval()

    prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else args.prompt
    prompt_ids = tokenizer.encode(prompt, add_bos=True)
    input_ids = torch.tensor([prompt_ids], dtype=torch.long, device=device)
    generated = model.generate(
        input_ids,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        top_k=args.top_k,
        top_p=args.top_p,
        repetition_penalty=args.repetition_penalty,
        eos_id=tokenizer.eos_id,
    )
    all_ids = generated[0].tolist()
    output_ids = all_ids[len(prompt_ids):] if args.suffix_only else all_ids
    text = tokenizer.decode(output_ids, skip_special_tokens=True)
    print(text)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
