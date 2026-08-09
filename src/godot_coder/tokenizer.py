from __future__ import annotations
"""BPE tokenizer (train, save, load, encode, decode) backed by HuggingFace tokenizers."""

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable


SPECIAL_TOKENS = (
    "<pad>",
    "<bos>",
    "<eos>",
    "<file_sep>",
    "<task>",
    "</task>",
    "<context>",
    "</context>",
    "<answer>",
    "</answer>",
    "<fim_prefix>",
    "<fim_suffix>",
    "<fim_middle>",
)


@runtime_checkable
class TokenizerLike(Protocol):
    @property
    def vocab_size(self) -> int: ...

    @property
    def pad_id(self) -> int: ...

    @property
    def bos_id(self) -> int: ...

    @property
    def eos_id(self) -> int: ...

    @property
    def file_sep_id(self) -> int: ...

    def encode(self, text: str, *, add_bos: bool = False, add_eos: bool = False) -> list[int]: ...

    def decode(self, ids: Iterable[int], *, skip_special_tokens: bool = False) -> str: ...

    def fingerprint(self) -> str: ...

    def save(self, path: str | Path) -> None: ...


class ByteTokenizer:
    """Lossless UTF-8 byte tokenizer with reserved structural tokens."""

    FORMAT_VERSION = 1
    SPECIAL_TOKENS = SPECIAL_TOKENS

    def __init__(self, special_tokens: Iterable[str] | None = None) -> None:
        tokens = tuple(special_tokens or self.SPECIAL_TOKENS)
        if len(tokens) != len(set(tokens)):
            raise ValueError("special tokens must be unique")
        self.special_tokens = tokens
        self._token_to_id = {token: index for index, token in enumerate(tokens)}
        self._byte_offset = len(tokens)
        pattern = "|".join(re.escape(token) for token in sorted(tokens, key=len, reverse=True))
        self._special_pattern = re.compile(f"({pattern})")

    @property
    def vocab_size(self) -> int:
        return self._byte_offset + 256

    @property
    def pad_id(self) -> int:
        return self._token_to_id["<pad>"]

    @property
    def bos_id(self) -> int:
        return self._token_to_id["<bos>"]

    @property
    def eos_id(self) -> int:
        return self._token_to_id["<eos>"]

    @property
    def file_sep_id(self) -> int:
        return self._token_to_id["<file_sep>"]

    def token_to_id(self, token: str) -> int:
        return self._token_to_id[token]

    def encode(self, text: str, *, add_bos: bool = False, add_eos: bool = False) -> list[int]:
        ids: list[int] = []
        if add_bos:
            ids.append(self.bos_id)
        for part in self._special_pattern.split(text):
            if not part:
                continue
            special_id = self._token_to_id.get(part)
            if special_id is not None:
                ids.append(special_id)
            else:
                ids.extend(self._byte_offset + byte for byte in part.encode("utf-8"))
        if add_eos:
            ids.append(self.eos_id)
        return ids

    def decode(self, ids: Iterable[int], *, skip_special_tokens: bool = False) -> str:
        chunks: list[str] = []
        byte_buffer = bytearray()

        def flush_bytes() -> None:
            if byte_buffer:
                chunks.append(byte_buffer.decode("utf-8", errors="replace"))
                byte_buffer.clear()

        for raw_id in ids:
            token_id = int(raw_id)
            if 0 <= token_id < self._byte_offset:
                flush_bytes()
                if not skip_special_tokens:
                    chunks.append(self.special_tokens[token_id])
            elif self._byte_offset <= token_id < self.vocab_size:
                byte_buffer.append(token_id - self._byte_offset)
            else:
                raise ValueError(f"token id {token_id} is outside vocabulary")
        flush_bytes()
        return "".join(chunks)

    def to_dict(self) -> dict[str, object]:
        return {
            "format": "godot-coder-byte-tokenizer",
            "format_version": self.FORMAT_VERSION,
            "special_tokens": list(self.special_tokens),
            "encoding": "utf-8-bytes",
            "vocab_size": self.vocab_size,
        }

    def fingerprint(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def save(self, path: str | Path) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "ByteTokenizer":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("format") != "godot-coder-byte-tokenizer":
            raise ValueError("unsupported byte tokenizer format")
        if raw.get("format_version") != cls.FORMAT_VERSION:
            raise ValueError("unsupported byte tokenizer version")
        tokenizer = cls(raw["special_tokens"])
        if raw.get("vocab_size") != tokenizer.vocab_size:
            raise ValueError("tokenizer vocabulary metadata is inconsistent")
        return tokenizer


class BPETokenizer:
    """Inspectable byte-level BPE wrapper backed by Hugging Face Tokenizers."""

    FORMAT_VERSION = 1

    def __init__(self, backend, special_tokens: Iterable[str] = SPECIAL_TOKENS) -> None:
        self.backend = backend
        self.special_tokens = tuple(special_tokens)
        self._token_to_id: dict[str, int] = {}
        for token in self.special_tokens:
            token_id = backend.token_to_id(token)
            if token_id is None:
                raise ValueError(f"BPE tokenizer is missing required special token {token}")
            self._token_to_id[token] = int(token_id)

    @property
    def vocab_size(self) -> int:
        return int(self.backend.get_vocab_size(with_added_tokens=True))

    @property
    def pad_id(self) -> int:
        return self._token_to_id["<pad>"]

    @property
    def bos_id(self) -> int:
        return self._token_to_id["<bos>"]

    @property
    def eos_id(self) -> int:
        return self._token_to_id["<eos>"]

    @property
    def file_sep_id(self) -> int:
        return self._token_to_id["<file_sep>"]

    def token_to_id(self, token: str) -> int:
        return self._token_to_id[token]

    def encode(self, text: str, *, add_bos: bool = False, add_eos: bool = False) -> list[int]:
        ids = list(map(int, self.backend.encode(text, add_special_tokens=False).ids))
        if add_bos:
            ids.insert(0, self.bos_id)
        if add_eos:
            ids.append(self.eos_id)
        return ids

    def decode(self, ids: Iterable[int], *, skip_special_tokens: bool = False) -> str:
        return self.backend.decode(list(map(int, ids)), skip_special_tokens=skip_special_tokens)

    def to_dict(self) -> dict[str, object]:
        return {
            "format": "godot-coder-bpe-tokenizer",
            "format_version": self.FORMAT_VERSION,
            "special_tokens": list(self.special_tokens),
            "vocab_size": self.vocab_size,
            "backend": json.loads(self.backend.to_str()),
        }

    def fingerprint(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def save(self, path: str | Path) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "BPETokenizer":
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("BPE tokenizers require the 'tokenizers' package") from exc
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("format") != "godot-coder-bpe-tokenizer":
            raise ValueError("unsupported BPE tokenizer format")
        if raw.get("format_version") != cls.FORMAT_VERSION:
            raise ValueError("unsupported BPE tokenizer version")
        backend = Tokenizer.from_str(json.dumps(raw["backend"]))
        tokenizer = cls(backend, raw["special_tokens"])
        if raw.get("vocab_size") != tokenizer.vocab_size:
            raise ValueError("BPE tokenizer vocabulary metadata is inconsistent")
        return tokenizer

    @classmethod
    def train(
        cls,
        files: Iterable[str | Path],
        *,
        vocab_size: int = 8192,
        min_frequency: int = 2,
        special_tokens: Iterable[str] = SPECIAL_TOKENS,
    ) -> "BPETokenizer":
        try:
            from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("BPE tokenizers require the 'tokenizers' package") from exc
        if vocab_size < 512:
            raise ValueError("vocab_size must be at least 512")
        if vocab_size > 2**32 - 1:
            raise ValueError("vocab_size exceeds supported uint32 token ids")
        if min_frequency < 1:
            raise ValueError("min_frequency must be positive")
        tokens = tuple(special_tokens)
        backend = Tokenizer(models.BPE(unk_token="<unk>"))
        backend.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
        backend.decoder = decoders.ByteLevel()
        trainer = trainers.BpeTrainer(
            vocab_size=vocab_size,
            min_frequency=min_frequency,
            special_tokens=[*tokens, "<unk>"],
            initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
            max_token_length=64,
            show_progress=True,
        )
        paths = [str(Path(path)) for path in files]
        if not paths:
            raise ValueError("at least one text file is required to train BPE")
        backend.train(paths, trainer)
        return cls(backend, tokens)


def load_tokenizer(path: str | Path) -> TokenizerLike:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    tokenizer_format = raw.get("format")
    if tokenizer_format == "godot-coder-byte-tokenizer":
        return ByteTokenizer.load(path)
    if tokenizer_format == "godot-coder-bpe-tokenizer":
        return BPETokenizer.load(path)
    raise ValueError(f"unsupported tokenizer format: {tokenizer_format!r}")
