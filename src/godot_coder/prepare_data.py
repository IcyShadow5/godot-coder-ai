from __future__ import annotations

import argparse
import json
from pathlib import Path

from .data import prepare_dataset
from .tokenizer import ByteTokenizer, load_tokenizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare GDScript files as train/validation token streams.")
    parser.add_argument("--input", default="data/raw", help="Root directory containing source files")
    parser.add_argument("--output", default="data/processed", help="Output directory for binary token streams")
    parser.add_argument("--tokenizer", default="artifacts/tokenizer.json", help="Existing tokenizer or byte-tokenizer output")
    parser.add_argument(
        "--create-byte-tokenizer",
        action="store_true",
        help="Replace/create the tokenizer with the inspectable byte tokenizer before preparing data",
    )
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--extensions", nargs="+", default=[".gd"])
    parser.add_argument("--shard-tokens", type=int, default=8_000_000)
    parser.add_argument("--sampling-policy", choices=["packed_with_file_sep", "document"], default="packed_with_file_sep")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tokenizer_path = Path(args.tokenizer)
    if args.create_byte_tokenizer or not tokenizer_path.exists():
        tokenizer = ByteTokenizer()
        tokenizer.save(tokenizer_path)
        action = "created"
    else:
        tokenizer = load_tokenizer(tokenizer_path)
        action = "loaded"
    manifest = prepare_dataset(
        args.input,
        args.output,
        tokenizer,
        val_ratio=args.val_ratio,
        extensions=args.extensions,
        shard_tokens=args.shard_tokens,
        sampling_policy=args.sampling_policy,
    )
    print(json.dumps(manifest, indent=2))
    print(f"Tokenizer {action}: {tokenizer_path.resolve()} ({tokenizer.vocab_size:,} tokens)")


if __name__ == "__main__":
    main()
