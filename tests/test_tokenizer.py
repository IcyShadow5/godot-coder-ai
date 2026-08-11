import pytest
from pathlib import Path

from godot_coder.tokenizer import (
    ByteTokenizer,
    load_tokenizer,
    resolve_tokenizer_for_fingerprint,
)


def test_resolve_tokenizer_keeps_matching_tokenizer(tmp_path: Path) -> None:
    tokenizer = ByteTokenizer(["<a>", "<b>"])
    path = tmp_path / "tok.json"
    tokenizer.save(path)
    loaded = load_tokenizer(path)
    assert resolve_tokenizer_for_fingerprint(path, loaded, tokenizer.fingerprint()) is loaded


def test_resolve_tokenizer_falls_back_to_versioned_sibling(tmp_path: Path) -> None:
    new_tok = ByteTokenizer(["<new>"])
    old_tok = ByteTokenizer(["<old>"])
    current = tmp_path / "tok.json"
    new_tok.save(current)
    old_tok.save(tmp_path / f"tok_{old_tok.fingerprint()}.json")
    resolved = resolve_tokenizer_for_fingerprint(current, load_tokenizer(current), old_tok.fingerprint())
    assert resolved.fingerprint() == old_tok.fingerprint()


def test_resolve_tokenizer_raises_without_fallback(tmp_path: Path) -> None:
    tokenizer = ByteTokenizer(["<a>"])
    current = tmp_path / "tok.json"
    tokenizer.save(current)
    with pytest.raises(ValueError, match="do not match"):
        resolve_tokenizer_for_fingerprint(current, load_tokenizer(current), "deadbeef")


def test_round_trip_unicode_and_gdscript() -> None:
    tokenizer = ByteTokenizer()
    text = 'func _ready() -> void:\n\tprint("Grüße 🌑")\n'
    ids = tokenizer.encode(text, add_bos=True, add_eos=True)
    assert tokenizer.decode(ids, skip_special_tokens=True) == text


def test_structural_token_is_single_id() -> None:
    tokenizer = ByteTokenizer()
    ids = tokenizer.encode("<task>move player</task>")
    assert ids[0] == tokenizer.token_to_id("<task>")
    assert ids[-1] == tokenizer.token_to_id("</task>")
