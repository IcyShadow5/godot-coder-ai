"""Tests for verify.py — the adversarial read-only verification mode."""

from __future__ import annotations

from pathlib import Path

import pytest

from godot_coder.verify import (
    VERDICT_FALSIFIED,
    VERDICT_UNRESOLVED,
    VERDICT_VERIFIED,
    check_leak,
    check_mutation,
    check_repetition,
    check_temperature,
    check_trivial_pass,
    is_trivial_completion,
    leak_index,
    mutation_variants,
    pass_rate,
    run_verification,
    substance_score,
)


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def test_is_trivial_completion_detects_hollow_output() -> None:
    assert is_trivial_completion("")
    assert is_trivial_completion("   \n  ")
    assert is_trivial_completion("# just a comment\n# another\n")
    assert is_trivial_completion("func _ready() -> void:\n")
    assert is_trivial_completion("\tpass\n\tpass\n")
    assert is_trivial_completion("func _ready() -> void:\n\tpass\n")
    assert is_trivial_completion("class Foo:\n\tpass\n")
    assert not is_trivial_completion("var x := 42\n")
    assert not is_trivial_completion("func add(a: int, b: int) -> int:\n\treturn a + b\n")


def test_substance_score_ranks_code_over_shells() -> None:
    assert substance_score("") == 0.0
    assert substance_score("# comment\n") == 0.0
    assert substance_score("var x := 1\n") == 1 / 3
    assert substance_score("var a := 1\nvar b := 2\nvar c := 3\nreturn a + b + c\n") == 1.0


def test_leak_index_detects_overlap() -> None:
    prompt = "extends Node\n\nfunc clamp_value(value: float, minimum: float) -> float:\n"
    corpus = ["Some other script about movement.\n", prompt]
    assert leak_index(prompt, corpus) >= 0.9
    assert leak_index(prompt, ["unrelated text about particles"]) < 0.3


def test_leak_index_ignores_common_boilerplate() -> None:
    # A corpus full of near-identical Godot boilerplate must not make a prompt
    # look leaked: every shared n-gram shows up in many files and is dropped.
    boilerplate = "extends Node2D\n\nfunc _ready() -> void:\n\tprint(\"ready\")\n"
    corpus = [boilerplate.replace("ready", f"ready_{index}") for index in range(6)]
    prompt = "extends Node2D\n\nfunc _ready() -> void:\n\tprint(\"custom\")\n"
    assert leak_index(prompt, corpus) < 0.3


def test_leak_index_flags_verbatim_copy_among_boilerplate() -> None:
    # A scaffold that actually leaked into the training data still scores high
    # even when the corpus is mostly generic boilerplate.
    boilerplate = "extends Node2D\n\nfunc _ready() -> void:\n\tprint(\"ready\")\n"
    corpus = [boilerplate.replace("ready", f"ready_{index}") for index in range(6)]
    prompt = "extends Node2D\n\nfunc clamp_timer(value: float) -> float:\n\treturn value * 2.0\n"
    corpus.append(prompt)
    assert leak_index(prompt, corpus) >= 0.6


def test_mutation_variants_produce_valid_alternatives() -> None:
    scaffold = "extends Node\n\nfunc clamp_value(value: float) -> float:\n\t"
    variants = mutation_variants(scaffold)
    assert variants  # at least the leading-comment variant
    assert any(variant.startswith("# verify:") for variant in variants)
    assert any("clampValueImpl" in variant for variant in variants)
    assert any("_unused: int = 0" in variant for variant in variants)
    # Semantics preserved: original scaffold text still present in each.
    for variant in variants:
        assert "func " in variant


def test_pass_rate_counts_parser_passes() -> None:
    results = [{"parser_passed": True}, {"parser_passed": False}, {"parser_passed": True}]
    assert pass_rate(results) == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# the five checks
# ---------------------------------------------------------------------------

def _result(parser_passed: bool, suffix: str, **extra) -> dict[str, object]:
    return {"parser_passed": parser_passed, "generated_suffix": suffix, **extra}


def test_check_trivial_pass_falsifies_hollow_advantage() -> None:
    # A wins the raw pass rate, but every pass is an empty output - its
    # advantage evaporates once hollow passes are removed.
    a = [_result(True, ""), _result(True, ""), _result(True, ""), _result(False, "x")]
    b = [_result(True, "var x := 1\n"), _result(True, "var y := 2\n"), _result(False, ""), _result(False, "")]
    report = check_trivial_pass(a, b)
    assert report["verdict"] == VERDICT_FALSIFIED
    assert "hollow" in str(report["reason"]).lower()


def test_check_trivial_pass_verifies_substantive_advantage() -> None:
    a = [_result(True, "var x := 1\nreturn x\n"), _result(True, "var y := 2\n")]
    b = [_result(True, "pass\n"), _result(False, "")]
    report = check_trivial_pass(a, b)
    assert report["verdict"] == VERDICT_VERIFIED


def test_check_leak_falsifies_memorized_prompts() -> None:
    prompt = "extends Node\n\nfunc clamp_value(value: float, minimum: float) -> float:\n"
    a = [{"id": "leaky_task", "prompt": prompt, "parser_passed": True}]
    b = [{"id": "other", "prompt": "unrelated", "parser_passed": True}]
    report = check_leak(a, b, [prompt])
    assert report["verdict"] == VERDICT_FALSIFIED
    assert report["evidence"]["leaking_count"] == 1


def test_check_leak_unresolved_without_corpus() -> None:
    a = [{"id": "x", "prompt": "extends Node", "parser_passed": True}]
    report = check_leak(a, a, [])
    assert report["verdict"] == VERDICT_UNRESOLVED


def test_check_mutation_falsifies_phrasing_dependence() -> None:
    report = check_mutation(
        {"a_rate": 0.9, "b_rate": 0.4},   # original: A clearly better
        {"a_rate": 0.3, "b_rate": 0.5},   # mutated: A collapses
    )
    assert report["verdict"] == VERDICT_FALSIFIED


def test_check_temperature_falsifies_sampling_dependence() -> None:
    report = check_temperature(
        {"a_rate": 0.8, "b_rate": 0.3},   # cold: A better
        {"a_rate": 0.3, "b_rate": 0.4},   # warm: edge gone
    )
    assert report["verdict"] == VERDICT_FALSIFIED


def test_check_repetition_falsifies_shell_reference_match() -> None:
    # A echoes the reference shape (high prefix) but has no more substance.
    a = [_result(True, "pass\n", token_prefix_accuracy=0.9)]
    b = [_result(True, "var x := 1\nreturn x\n", token_prefix_accuracy=0.5)]
    report = check_repetition(a, b)
    assert report["verdict"] == VERDICT_FALSIFIED


# ---------------------------------------------------------------------------
# orchestration (fake generate/validate, no torch, no Godot)
# ---------------------------------------------------------------------------

def _scaffold_project(tmp_path: Path) -> Path:
    (tmp_path / "checkpoints").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "seed_project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "raw" / "seed_project" / "project.godot").write_text(
        "config_version=5\n", encoding="utf-8"
    )
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    return tmp_path


def _fake_generate(completion_for: dict[str, str]):
    def fake(root, checkpoint, prompt, *, max_new_tokens, temperature):
        key = completion_for.get(checkpoint, "fallback")
        if key == "hollow":
            return ""
        if key == "real":
            return "\treturn 42\n"
        return "\tpass\n"
    return fake


def _make_fake_validate(work_dir: Path):
    def fake(root, wd, code, project_path):
        # The read-only promise: the work dir is where scripts may live.
        assert Path(wd).resolve() == Path(work_dir).resolve()
        return {"passed": code.strip().endswith("return 42") or code.strip().endswith("pass"), "output": ""}
    return fake


def test_run_verification_comparison_end_to_end(tmp_path: Path) -> None:
    root = _scaffold_project(tmp_path / "proj")
    generate = _fake_generate({"A": "real", "B": "hollow"})
    validate = _make_fake_validate(tmp_path / "outside")
    report = run_verification(
        root,
        "A",
        "B",
        work_dir=tmp_path / "outside",
        validation_project="data/raw/seed_project",
        max_new_tokens=8,
        generate=generate,
        validate=validate,
    )
    assert report["verdict"] in (VERDICT_VERIFIED, VERDICT_FALSIFIED, VERDICT_UNRESOLVED)
    assert report["claim"]
    assert set(report["checks"]) >= {"trivial_pass", "leak", "repetition", "mutation", "temperature"}
    # The work dir was removed again (read-only footprint).
    assert not (tmp_path / "outside").exists()


def test_run_verification_single_checkpoint_skips_comparison_checks(tmp_path: Path) -> None:
    root = _scaffold_project(tmp_path / "proj")
    generate = _fake_generate({"A": "real"})
    validate = _make_fake_validate(tmp_path / "outside")
    report = run_verification(
        root,
        "A",
        None,
        work_dir=tmp_path / "outside",
        validation_project="data/raw/seed_project",
        max_new_tokens=8,
        generate=generate,
        validate=validate,
    )
    assert "mutation" not in report["checks"]
    assert "temperature" not in report["checks"]
    # Without a baseline B the repetition check cannot compare - honest UNRESOLVED.
    assert report["checks"]["repetition"]["verdict"] == VERDICT_UNRESOLVED


def _results_with_rate(passed_count: int, total: int) -> list[dict[str, object]]:
    """Deterministic result lists with exactly passed_count/total passes."""
    return [
        {"parser_passed": index < passed_count, "generated_suffix": "\treturn 42\n" if index < passed_count else ""}
        for index in range(total)
    ]


def test_check_trivial_pass_absolute_only_for_single_mode() -> None:
    # A beats B relatively (0.4 vs 0.3) but both sit below the 0.5 claim:
    # the comparison claim must survive, only a single-mode claim may fail.
    a = _results_with_rate(2, 5)
    b = _results_with_rate(3, 10)
    assert check_trivial_pass(a, b)["verdict"] == VERDICT_VERIFIED
    assert check_trivial_pass(a, b, expected_rate=0.5)["verdict"] == VERDICT_FALSIFIED


def test_run_verification_single_checkpoint_falsifies_low_pass_rate(tmp_path: Path) -> None:
    root = _scaffold_project(tmp_path / "proj")
    work = tmp_path / "outside"
    work.mkdir()
    generate = _fake_generate({"A": "hollow"})
    validate = _make_fake_validate(work)
    report = run_verification(
        root,
        "A",
        None,
        work_dir=work,
        validation_project="data/raw/seed_project",
        max_new_tokens=8,
        generate=generate,
        validate=validate,
    )
    assert report["verdict"] == VERDICT_FALSIFIED
    assert report["checks"]["trivial_pass"]["verdict"] == VERDICT_FALSIFIED


def test_run_verification_rejects_work_dir_inside_project(tmp_path: Path) -> None:
    root = _scaffold_project(tmp_path)
    generate = _fake_generate({"A": "real"})
    validate = _make_fake_validate(tmp_path)
    with pytest.raises(ValueError, match="outside the project"):
        run_verification(
            root,
            "A",
            None,
            work_dir=root / "data",
            validation_project="data/raw/seed_project",
            max_new_tokens=8,
            generate=generate,
            validate=validate,
        )


def test_run_verification_cleanup_even_on_error(tmp_path: Path) -> None:
    root = _scaffold_project(tmp_path / "proj")
    work = tmp_path / "outside"
    work.mkdir()

    def exploding(root, checkpoint, prompt, **kwargs):
        raise RuntimeError("model exploded")

    validate = _make_fake_validate(work)
    with pytest.raises(RuntimeError, match="model exploded"):
        run_verification(
            root,
            "A",
            None,
            work_dir=work,
            validation_project="data/raw/seed_project",
            max_new_tokens=8,
            generate=exploding,
            validate=validate,
        )
    assert not work.exists()
