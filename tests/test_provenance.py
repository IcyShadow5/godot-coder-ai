"""Tests for the context provenance module.

Coverage: token accounting per source, window fitting (incl. the exact BOS
token), the head-preserving truncation policy, strict mode, and the chat/
CLI part builders. Uses the real ByteTokenizer (ASCII bytes are one token
each), so the expected numbers are exact and deterministic.
"""

import pytest

from godot_coder.provenance import (
    HEAD_PRESERVING,
    STRICT,
    PromptPart,
    chat_parts,
    cli_parts,
    compose_prompt,
)
from godot_coder.tokenizer import ByteTokenizer


def _tok() -> ByteTokenizer:
    return ByteTokenizer()


def test_chat_parts_plain_without_task_format() -> None:
    parts = chat_parts("extends Node", task_format=False)
    assert [part.source for part in parts] == ["user_prompt"]
    assert parts[0].text == "extends Node"


def test_chat_parts_wraps_with_task_format() -> None:
    parts = chat_parts("func f():\n\tpass", task_format=True)
    assert [part.source for part in parts] == ["task_header", "user_prompt", "task_tail"]
    assert "".join(part.text for part in parts) == "# file: chat/generated\n# task: func f():\n\tpass\n"
    assert parts[0].keep and parts[2].keep
    assert not parts[1].keep


def test_chat_parts_does_not_double_wrap() -> None:
    wrapped = "# file: chat/generated\n# task: extends Node\n"
    parts = chat_parts(wrapped, task_format=True)
    assert len(parts) == 1
    assert parts[0].source == "user_prompt"
    assert parts[0].text == wrapped


def test_cli_parts_tags_the_source() -> None:
    assert cli_parts("x", None)[0].source == "cli_prompt"
    part = cli_parts("x", "prompt.gd")[0]
    assert part.source == "prompt_file"
    assert part.meta == {"file": "prompt.gd"}


def test_compose_counts_exact_tokens_including_bos() -> None:
    tokenizer = _tok()
    prompt, report = compose_prompt(
        [PromptPart("user_prompt", "extends Node")],
        tokenizer,
        max_seq_len=64,
        max_new_tokens=16,
    )
    assert prompt == "extends Node"
    # ASCII bytes are one token each in the byte tokenizer, plus one BOS.
    assert report.prompt_tokens == len("extends Node") + 1
    assert report.parts[0].tokens == len("extends Node")
    assert report.kv_cache_possible is True
    assert report.truncated is False


def test_compose_head_preserving_trims_the_request_tail() -> None:
    tokenizer = _tok()
    parts = chat_parts("x" * 200, task_format=True)
    prompt, report = compose_prompt(parts, tokenizer, max_seq_len=64, max_new_tokens=8)
    header_tokens = len("# file: chat/generated\n# task: ")
    assert report.truncated is True
    # _trim_tail may drop a token or two more than the overflow when token
    # boundaries do not fall on character boundaries - that is the safe
    # direction (the prompt never exceeds the window).
    assert report.dropped_tokens >= report.overflow_tokens
    # The header survived; the request text lost its tail.
    assert prompt.startswith("# file: chat/generated\n# task: ")
    assert prompt.endswith("\n")
    assert report.prompt_tokens <= 64
    assert report.parts[0].tokens == header_tokens  # header intact
    assert report.parts[1].dropped_tokens > 0
    assert report.parts[1].tokens < 200
    assert report.parts[2].tokens == 1  # trailing newline intact


def test_compose_keeps_head_of_flexible_part() -> None:
    tokenizer = _tok()
    text = "0123456789" * 20  # 200 chars
    prompt, report = compose_prompt(
        [PromptPart("user_prompt", text)],
        tokenizer,
        max_seq_len=50,
        max_new_tokens=8,
    )
    assert prompt.startswith("0123456789")  # the head survives
    assert len(prompt) < 200
    assert report.prompt_tokens <= 50
    assert report.truncated is True


def test_compose_trims_reverse_order_across_parts() -> None:
    tokenizer = _tok()
    prompt, report = compose_prompt(
        [PromptPart("first", "a" * 100), PromptPart("second", "b" * 100)],
        tokenizer,
        max_seq_len=80,
        max_new_tokens=8,
    )
    first, second = report.parts
    assert second.tokens == 0  # the last part lost its tail first (all of it)
    assert second.dropped_tokens == 100
    assert first.tokens == 79  # the first part was trimmed only as needed
    assert report.prompt_tokens <= 80
    assert report.truncated is True


def test_compose_strict_refuses_overflow() -> None:
    tokenizer = _tok()
    with pytest.raises(ValueError, match="context would exceed"):
        compose_prompt(
            [PromptPart("user_prompt", "x" * 100)],
            tokenizer,
            max_seq_len=32,
            max_new_tokens=8,
            policy=STRICT,
        )


def test_compose_strict_allows_fitting_context() -> None:
    tokenizer = _tok()
    _, report = compose_prompt(
        [PromptPart("user_prompt", "x" * 10)],
        tokenizer,
        max_seq_len=32,
        max_new_tokens=8,
        policy=STRICT,
    )
    assert report.truncated is False


def test_compose_reports_kv_cache_possible() -> None:
    tokenizer = _tok()
    _, fitting = compose_prompt(
        [PromptPart("user_prompt", "x" * 10)],
        tokenizer,
        max_seq_len=32,
        max_new_tokens=16,
    )
    assert fitting.kv_cache_possible is True
    _, overflow = compose_prompt(
        [PromptPart("user_prompt", "x" * 10)],
        tokenizer,
        max_seq_len=32,
        max_new_tokens=64,
    )
    assert overflow.kv_cache_possible is False
    # The prompt fits; only the generation budget does not, so no trimming.
    assert overflow.truncated is False


def test_compose_percentages_sum_to_roughly_100() -> None:
    tokenizer = _tok()
    _, report = compose_prompt(
        [PromptPart("user_prompt", "func add(a, b):\n\treturn a + b\n")],
        tokenizer,
        max_seq_len=64,
        max_new_tokens=16,
    )
    assert sum(part.pct for part in report.parts) == pytest.approx(100.0, abs=1.0)


def test_compose_part_sum_matches_joined() -> None:
    """Per-part tokens + BOS must equal the exact joined count for real
    prompts, including a request that ends on a newline (the chat tail part
    borders it directly)."""
    tokenizer = _tok()
    parts = chat_parts("func f() -> void:\n\tpass\n", task_format=True)
    assert parts[1].text.endswith("\n")  # the boundary is exercised
    _, report = compose_prompt(parts, tokenizer, max_seq_len=128, max_new_tokens=16)
    assert sum(part.tokens for part in report.parts) + 1 == report.prompt_tokens


def test_compose_trims_multibyte_prompt_without_breaking_utf8() -> None:
    tokenizer = _tok()
    # Um+lauts are two UTF-8 bytes each, so token boundaries do not fall on
    # character boundaries while trimming.
    text = "äöü" * 40
    prompt, report = compose_prompt(
        [PromptPart("user_prompt", text)],
        tokenizer,
        max_seq_len=50,
        max_new_tokens=8,
    )
    assert report.prompt_tokens <= 50
    assert report.truncated is True
    assert report.dropped_tokens >= report.overflow_tokens
    prompt.encode("utf-8")  # must still be valid UTF-8 after the cut
    assert prompt


def test_compose_without_bos_gains_one_token() -> None:
    tokenizer = _tok()
    _, report = compose_prompt(
        [PromptPart("user_prompt", "abc")],
        tokenizer,
        max_seq_len=32,
        max_new_tokens=8,
        add_bos=False,
    )
    assert report.prompt_tokens == 3


def test_compose_rejects_invalid_inputs() -> None:
    tokenizer = _tok()
    with pytest.raises(ValueError, match="max_seq_len"):
        compose_prompt([PromptPart("user_prompt", "x")], tokenizer, max_seq_len=1, max_new_tokens=1)
    with pytest.raises(ValueError, match="unknown policy"):
        compose_prompt([PromptPart("user_prompt", "x")], tokenizer, max_seq_len=32, max_new_tokens=1, policy="bogus")
    with pytest.raises(ValueError, match="context window too small"):
        compose_prompt(
            [PromptPart("task_header", "h" * 32, keep=True)],
            tokenizer,
            max_seq_len=32,
            max_new_tokens=8,
        )


def test_report_to_dict_is_json_serialisable() -> None:
    import json

    tokenizer = _tok()
    _, report = compose_prompt(
        [PromptPart("user_prompt", "x" * 10, meta={"id": "t1"})],
        tokenizer,
        max_seq_len=32,
        max_new_tokens=8,
    )
    payload = json.dumps(report.to_dict())
    assert '"prompt_tokens"' in payload
    assert '"source": "user_prompt"' in payload
