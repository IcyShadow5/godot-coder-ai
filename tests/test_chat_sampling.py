"""Tests for the chat-sampling improvements.

Coverage: completion cleanup in services.py (_normalize_blank_lines,
_collapse_repeated_blocks, _clean_completion) and the GenerateRequest
defaults/validation for top_p and repetition_penalty.
"""

import pytest

from godot_coder.ui.schemas import GenerateRequest
from godot_coder.ui.services import (
    _clean_completion,
    _collapse_repeated_blocks,
    _normalize_blank_lines,
)


def test_normalize_blank_lines_collapses_runs() -> None:
    text = "a\n\n\n\nb\n\n\nc"
    assert _normalize_blank_lines(text) == "a\n\nb\n\nc"


def test_collapse_repeated_blocks_cuts_at_first_occurrence() -> None:
    text = "func a() -> void:\n\tpass\nfunc a() -> void:\n\tpass\nfunc b() -> void:\n\tpass\n"
    assert _collapse_repeated_blocks(text) == "func a() -> void:\n\tpass"


def test_clean_completion_handles_loop_output() -> None:
    loop = (
        "return value.x\n\n\n\nfunc _get_value() -> float:\n\treturn _value.y\n\n\n\n"
        "func _get_value() -> float:\n\treturn _value.y\n\n\n\n"
        "func _get_value() -> float:\n\treturn _value.y\n"
    )
    out = _clean_completion(loop)
    assert out.count("func _get_value") == 1
    assert "\n\n\n\n" not in out
    assert not out.endswith("\n")


def test_collapse_single_line_comment_loop() -> None:
    text = "# - max_value\n# - max_value\n# - float\n"
    assert _collapse_repeated_blocks(text) == "# - max_value"


def test_collapse_keeps_identical_code_line_pairs() -> None:
    text = "var x = 1\nvar x = 1\nvar y = 2\n"
    assert _collapse_repeated_blocks(text) == text


def test_collapse_breaks_code_line_triple_loop() -> None:
    text = "\tpass\n\tpass\n\tpass\n"
    assert _collapse_repeated_blocks(text) == "\tpass"


def test_clean_completion_leaves_normal_code_alone() -> None:
    code = "extends Node\n\nfunc _ready() -> void:\n\tprint(\"hi\")\n"
    assert _clean_completion(code) == code.rstrip()


def test_generate_request_defaults_include_sampling_fixes() -> None:
    req = GenerateRequest(checkpoint="c.pt", prompt="x")
    assert req.top_p == 1.0
    assert req.repetition_penalty == 1.15


def test_generate_request_rejects_invalid_penalty() -> None:
    with pytest.raises(ValueError):
        GenerateRequest(checkpoint="c.pt", prompt="x", repetition_penalty=0.5)


def test_generate_request_rejects_invalid_top_p() -> None:
    with pytest.raises(ValueError):
        GenerateRequest(checkpoint="c.pt", prompt="x", top_p=1.5)
