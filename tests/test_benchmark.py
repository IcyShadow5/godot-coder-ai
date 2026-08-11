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


def test_run_benchmark_validates_prompt_plus_completion(monkeypatch, tmp_path) -> None:
    """generate() returns only the completion; validation must still see the
    full scaffold (prompt + completion), otherwise every case fails to parse."""
    from godot_coder.benchmark import GOLDEN_TASKS, run_benchmark
    from godot_coder.tokenizer import ByteTokenizer

    completion = chr(9) + "return 42" + chr(10)

    def fake_load(_root, _checkpoint):
        return ({"kind": "golden", "model_config": {"max_seq_len": 256}}, ByteTokenizer(), "golden")

    def fake_generate(self, _checkpoint, _prompt, **_kwargs):
        # chat-flow contract: completion only, no prompt echo
        from godot_coder.ui.services import GenerationResult
        return GenerationResult(text=completion, cancelled=False)

    class FakeGS:
        def __init__(self, _root):
            self.root = _root

    validated: list[str] = []

    def fake_validate(_root, code, _project_path):
        validated.append(code)
        return {"passed": True, "output": "", "return_code": 0, "script": "x.gd", "timed_out": False}

    monkeypatch.setattr("godot_coder.benchmark._load_checkpoint_context", fake_load)
    monkeypatch.setattr("godot_coder.benchmark.GenerationService", type("FakeGS", (FakeGS,), {"generate": fake_generate}))
    monkeypatch.setattr("godot_coder.benchmark.validate_code", fake_validate)

    run_benchmark(tmp_path, "checkpoints/x.pt", mode="golden")

    assert len(validated) == len(GOLDEN_TASKS)
    assert all(code.endswith(completion) for code in validated)
    assert all(len(code) > len(completion) for code in validated)
    assert all(code.startswith("extends") for code in validated)


def test_run_benchmark_reports_context_provenance(monkeypatch, tmp_path) -> None:
    from godot_coder.benchmark import GOLDEN_TASKS, run_benchmark
    from godot_coder.tokenizer import ByteTokenizer

    def fake_load(_root, _checkpoint):
        return ({"kind": "golden", "model_config": {"max_seq_len": 256}}, ByteTokenizer(), "golden")

    def fake_generate(self, _checkpoint, _prompt, **_kwargs):
        from godot_coder.ui.services import GenerationResult
        return GenerationResult(text="\treturn 42\n", cancelled=False)

    class FakeGS:
        def __init__(self, _root):
            self.root = _root

    def fake_validate(_root, code, _project_path):
        return {"passed": True, "output": "", "return_code": 0, "script": "x.gd", "timed_out": False}

    monkeypatch.setattr("godot_coder.benchmark._load_checkpoint_context", fake_load)
    monkeypatch.setattr("godot_coder.benchmark.GenerationService", type("FakeGS", (FakeGS,), {"generate": fake_generate}))
    monkeypatch.setattr("godot_coder.benchmark.validate_code", fake_validate)

    report = run_benchmark(tmp_path, "checkpoints/x.pt", mode="golden")
    results = report["results"]
    assert len(results) == len(GOLDEN_TASKS)
    assert all(item.get("prompt_tokens", 0) > 0 for item in results)
    assert all("context_truncated" in item for item in results)
    assert all("kv_cache_possible" in item for item in results)
