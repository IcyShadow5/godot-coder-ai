"""Tests for the hardware autotuner (autotune.py).

The important part: unsafe (over 90% VRAM), oom and error probes must
never make it into the recommendation, and the fastest safe pass wins.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from godot_coder import autotune


def _fake_variant(root: Path, label: str, base_path: str, context: int, checkpointing: bool, compile_enabled: bool, batch: int) -> Path:
    """Write a minimal variant config and return its path, like the real _variant."""
    path = root / "variants" / f"{label}-b{batch}-ckpt{int(checkpointing)}-compile{int(compile_enabled)}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("model: {}\ntrain: {}\nprofile:\n  id: x\n", encoding="utf-8")
    return path


def _fake_worker(root: Path, relative: str, batch: int, device: str, warmup: int, measure: int) -> dict:
    """Canned probe results: no GPU needed, but realistic shapes."""
    name = Path(relative).name.upper()
    if "C163" in name and "CKPT1" in name:
        return {"status": "error", "error": "probe crashed", "batch_size": batch}
    if "B91" in name and "CKPT0" in name:
        return {"status": "oom", "error": "cuda error: out of memory", "batch_size": batch}
    if batch == 4:
        # Fastest on paper, but way over the VRAM safety ceiling -> must be excluded.
        return {
            "status": "pass", "batch_size": batch, "tokens_per_second": 99999.0,
            "peak_reserved_gib": 12.0, "peak_reserved_fraction": 1.5,
            "parameters": 90_000_000, "sequence_length": 1024,
        }
    if batch == 2:
        return {
            "status": "pass", "batch_size": batch, "tokens_per_second": 50000.0,
            "peak_reserved_gib": 5.0, "peak_reserved_fraction": 0.6,
            "parameters": 90_000_000, "sequence_length": 1024,
        }
    return {
        "status": "pass", "batch_size": batch, "tokens_per_second": 30000.0,
        "peak_reserved_gib": 2.0, "peak_reserved_fraction": 0.3,
        "parameters": 90_000_000, "sequence_length": 1024,
    }


def test_mark_safety_flags_over_90_percent_vram():
    result = {"status": "pass", "peak_reserved_fraction": 0.95}
    marked = autotune._mark_safety(result)
    assert marked["status"] == "unsafe"
    assert "95.0%" in marked["unsafe_reason"]


def test_mark_safety_leaves_safe_and_non_pass_results_alone():
    safe = {"status": "pass", "peak_reserved_fraction": 0.5}
    assert autotune._mark_safety(safe)["status"] == "pass"

    error = {"status": "error", "peak_reserved_fraction": 0.99}
    assert autotune._mark_safety(error)["status"] == "error"


def test_score_ranks_speed_then_size_then_context():
    fast_small = {"tokens_per_second": 10, "parameters": 1000, "sequence_length": 128}
    slow_big = {"tokens_per_second": 9, "parameters": 2000, "sequence_length": 256}
    assert autotune._score(fast_small) > autotune._score(slow_big)

    same_speed_more_params = {"tokens_per_second": 10, "parameters": 2000, "sequence_length": 128}
    assert autotune._score(same_speed_more_params) > autotune._score(fast_small)

    same_speed_more_context = {"tokens_per_second": 10, "parameters": 2000, "sequence_length": 256}
    assert autotune._score(same_speed_more_context) > autotune._score(same_speed_more_params)


def test_recommendation_excludes_unsafe_oom_and_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(autotune, "_variant", _fake_variant)
    monkeypatch.setattr(autotune, "_run_worker", _fake_worker)

    report = autotune.run_autotune(tmp_path, full=False, warmup_steps=1, measure_steps=1)

    # 4 matrix entries x 2 checkpointing x 1 compile x 3 batches
    assert len(report["attempts"]) == 24

    statuses = {a["status"] for a in report["attempts"]}
    assert "unsafe" in statuses  # batch 4 -> over 90% VRAM
    assert "oom" in statuses
    assert "error" in statuses

    rec = report["recommendation"]
    assert rec is not None
    # The fastest SAFE variant (A91-1024, batch 2, 0.6 fraction) wins.
    # Batch 4 is faster on paper but gets excluded for exceeding 90% VRAM.
    assert rec["matrix_label"] == "A91-1024"
    assert rec["batch_size"] == 2
    assert rec["peak_reserved_fraction"] == 0.6

    # The unsafe/oom/error configs are not the recommended one.
    for attempt in report["attempts"]:
        if attempt["status"] in ("unsafe", "oom", "error"):
            assert attempt["config"] != rec["config"]

    # The generated config was written with the right profile metadata.
    generated = tmp_path / "configs" / "autotuned_night.yaml"
    assert generated.exists()
    raw = yaml.safe_load(generated.read_text(encoding="utf-8"))
    assert raw["profile"]["id"] == "autotuned-night"
    assert raw["profile"]["generated"] is True


def test_compile_disabled_after_first_failure_skips_the_rest(tmp_path, monkeypatch):
    compile_calls = {"n": 0}

    def failing_worker(root, relative, batch, device, warmup, measure):
        is_compile = "compile1" in Path(relative).name.lower()
        if is_compile:
            compile_calls["n"] += 1
        # the real worker does not fail: it falls back to eager and reports
        # compile_enabled=False + compile_error while status stays "pass"
        return {
            "status": "pass",
            "batch_size": batch,
            "tokens_per_second": 20000.0,
            "peak_reserved_gib": 2.0,
            "peak_reserved_fraction": 0.3,
            "parameters": 90_000_000,
            "sequence_length": 1024,
            # like the real worker: non-compile configs never attempt compile
            "compile_enabled": False,
            "compile_error": "TritonMissing: no triton" if is_compile else None,
        }

    monkeypatch.setattr(autotune, "_variant", _fake_variant)
    monkeypatch.setattr(autotune, "_run_worker", failing_worker)

    report = autotune.run_autotune(tmp_path, full=True, warmup_steps=1, measure_steps=1)

    assert report["compile_available"] is False
    assert "TritonMissing" in report["compile_disabled_reason"]
    assert compile_calls["n"] == 1  # only the first compile probe actually ran

    compile_probes = [a for a in report["attempts"] if a["compile_requested"]]
    assert all(a["status"] == "skipped" for a in compile_probes[1:])


def test_atomic_json_writes_into_nested_dirs(tmp_path) -> None:
    target = tmp_path / "reports" / "hardware" / "report.json"
    autotune._atomic_json(target, {"a": 1, "nested": {"b": 2}})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "nested": {"b": 2}}
    assert not list(target.parent.glob("*.tmp"))


def test_variant_writes_expected_fields(tmp_path) -> None:
    base = tmp_path / "configs"
    base.mkdir(parents=True)
    (base / "corpus_balanced_90m.yaml").write_text(
        "profile:\n  id: base\nmodel:\n  max_seq_len: 512\n  gradient_checkpointing: false\n"
        "train:\n  batch_size: 4\n  compile_model: true\n",
        encoding="utf-8",
    )
    target = autotune._variant(tmp_path, "A91-1024", "configs/corpus_balanced_90m.yaml", 1024, True, True, 2)
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert raw["model"]["max_seq_len"] == 1024
    assert raw["model"]["gradient_checkpointing"] is True
    assert raw["train"]["batch_size"] == 2
    assert raw["train"]["gradient_accumulation_steps"] == 4  # ceil(8192 / (2 * 1024))
    assert raw["train"]["compile"] == {"enabled": True, "mode": "default"}
    assert "compile_model" not in raw["train"]
    assert raw["profile"]["id"] == "a91-1024"
    assert raw["profile"]["title"] == "A91-1024"
    assert raw["profile"]["probe_max_batch_size"] == 2
    assert target.name == "a91-1024-ckpt1-compile1-b2.yaml"


def test_normalize_autotuned_config_missing_file_returns_false(tmp_path) -> None:
    assert autotune.normalize_autotuned_config(tmp_path) is False


def test_normalize_autotuned_config_corrupt_yaml_returns_false(tmp_path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir(parents=True)
    (configs / "autotuned_night.yaml").write_text("profile: [unclosed", encoding="utf-8")
    assert autotune.normalize_autotuned_config(tmp_path) is False


def test_normalize_autotuned_config_repairs_metadata(tmp_path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir(parents=True)
    (configs / "autotuned_night.yaml").write_text(
        "profile:\n  id: autotuned-night\n  title: Old Title\nmodel:\n  n_layers: 4\n",
        encoding="utf-8",
    )
    report = tmp_path / "reports" / "hardware"
    report.mkdir(parents=True)
    (report / "autotune_latest.json").write_text(
        json.dumps({"recommendation": {"matrix_label": "Autotuned Night · C163-2048"}}),
        encoding="utf-8",
    )
    assert autotune.normalize_autotuned_config(tmp_path) is True
    raw = yaml.safe_load((configs / "autotuned_night.yaml").read_text(encoding="utf-8"))
    profile = raw["profile"]
    assert profile["generated"] is True
    assert profile["id"] == "autotuned-night"
    assert profile["title"] == "Autotuned Night · C163-2048"
    assert profile["base_id"] == "c163-2048"
    assert profile["method"] == "Hardware-Autotuner"
    assert profile["recommended"] is True
    assert raw["model"]["n_layers"] == 4  # training values untouched


def test_normalize_autotuned_config_is_idempotent(tmp_path) -> None:
    # An old generated config with missing metadata gets repaired once; a
    # second pass must find nothing left to do. Idempotency beats pinning
    # the exact prose of the repaired profile in the test.
    configs = tmp_path / "configs"
    configs.mkdir(parents=True)
    (configs / "autotuned_night.yaml").write_text(
        "profile:\n  id: autotuned-night\n  title: Old Title\nmodel:\n  n_layers: 4\n",
        encoding="utf-8",
    )
    assert autotune.normalize_autotuned_config(tmp_path) is True
    assert autotune.normalize_autotuned_config(tmp_path) is False
    raw = yaml.safe_load((configs / "autotuned_night.yaml").read_text(encoding="utf-8"))
    assert raw["profile"]["generated"] is True
    assert raw["model"]["n_layers"] == 4  # training values untouched


def test_normalize_autotuned_config_defaults_to_a91_without_report(tmp_path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir(parents=True)
    # No report and no title/id in the profile -> falls back to the A91-1024 default.
    (configs / "autotuned_night.yaml").write_text("profile:\n  method: legacy\n", encoding="utf-8")
    assert autotune.normalize_autotuned_config(tmp_path) is True
    raw = yaml.safe_load((configs / "autotuned_night.yaml").read_text(encoding="utf-8"))
    assert raw["profile"]["title"] == "Autotuned Night · A91-1024"
    assert raw["profile"]["base_id"] == "a91-1024"


def test_parse_args_defaults(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["autotune"])
    args = autotune.parse_args()
    assert args.root == "."
    assert args.full is False
    assert args.warmup_steps == 1
    assert args.measure_steps == 2


def test_parse_args_override(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["autotune", "--root", "lab", "--full", "--warmup-steps", "3", "--measure-steps", "5"])
    args = autotune.parse_args()
    assert args.root == "lab"
    assert args.full is True
    assert args.warmup_steps == 3
    assert args.measure_steps == 5


def test_main_calls_run_autotune_with_resolved_root(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(root, *, full, warmup_steps, measure_steps):
        captured["root"] = root
        captured["full"] = full
        captured["warmup_steps"] = warmup_steps
        captured["measure_steps"] = measure_steps
        return {"ok": True}

    monkeypatch.setattr(autotune, "run_autotune", fake_run)
    monkeypatch.setattr(
        autotune, "parse_args",
        lambda: argparse.Namespace(root=str(tmp_path), full=True, warmup_steps=3, measure_steps=5),
    )
    autotune.main()
    assert captured["root"] == tmp_path.resolve()
    assert captured["full"] is True
    assert captured["warmup_steps"] == 3
    assert captured["measure_steps"] == 5
