from pathlib import Path

from godot_coder.benchmark import (
    CURRICULUM_PROMPTS,
    TASK_TRANSFER_PROMPTS,
    _failure_kind,
    _first_error,
    _last_function_completion,
    _summary,
)
from godot_coder.tokenizer import ByteTokenizer


def test_benchmark_has_two_distinct_static_tiers() -> None:
    assert len(CURRICULUM_PROMPTS) == 8
    assert len(TASK_TRANSFER_PROMPTS) == 8
    assert {item["id"] for item in CURRICULUM_PROMPTS}.isdisjoint(
        {item["id"] for item in TASK_TRANSFER_PROMPTS}
    )
    assert "func spend(amount: float)" in CURRICULUM_PROMPTS[0]["prompt"]
    assert "func spend_energy(amount: float)" in TASK_TRANSFER_PROMPTS[0]["prompt"]
    assert CURRICULUM_PROMPTS[0]["prompt"].endswith("\t")


def test_benchmark_summary_error_and_failure_classification() -> None:
    result = _summary([{"parser_passed": True}, {"parser_passed": False}])
    assert result["parser_passed"] == 1
    assert result["parser_pass_rate"] == 0.5
    assert _first_error("SCRIPT ERROR: Parse Error: bad syntax") == "Parse Error: bad syntax"
    assert _failure_kind('Parse Error: Expected statement, found "..." instead.') == "ellipsis"
    assert _failure_kind("Identifier x not declared in the current scope") == "unresolved_symbol"


def test_last_function_completion_uses_real_file_suffix() -> None:
    tokenizer = ByteTokenizer()
    text = (
        "extends Node\n\n"
        "func first() -> void:\n\tpass\n\n"
        "func final(value: int) -> int:\n\tvar doubled := value * 2\n\treturn doubled\n"
    )
    result = _last_function_completion(text, tokenizer, 256)
    assert result is not None
    prompt, suffix, token_count = result
    assert prompt.endswith("func final(value: int) -> int:\n")
    assert suffix == "\tvar doubled := value * 2\n\treturn doubled\n"
    assert token_count == len(tokenizer.encode(suffix))
