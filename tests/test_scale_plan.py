import json
from pathlib import Path

from godot_coder.scale_plan import build_scale_plan


def test_scale_plan_distinguishes_unique_and_processed_tokens(tmp_path: Path) -> None:
    manifest = tmp_path / "data" / "processed" / "corpus_v06" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"train_tokens": 287_952}), encoding="utf-8")
    autotune = tmp_path / "reports" / "hardware" / "autotune_latest.json"
    autotune.parent.mkdir(parents=True)
    autotune.write_text(json.dumps({"recommendation": {"tokens_per_second": 23_414.415}}), encoding="utf-8")
    report = build_scale_plan(tmp_path, passes=4.0)
    target = next(item for item in report["targets"] if item["target_unique_tokens"] == 20_000_000)
    assert target["planned_processed_tokens"] == 80_000_000
    assert target["missing_unique_tokens"] == 19_712_048
    assert 0.94 <= target["estimated_training_hours"] <= 0.96
    assert report["recommended_first_target"] == 5_000_000
    assert (tmp_path / "reports" / "corpus" / "scale_plan_latest.json").exists()


def test_scale_plan_selects_largest_active_corpus_manifest(tmp_path: Path) -> None:
    small = tmp_path / "data" / "processed" / "corpus_v04" / "manifest.json"
    large = tmp_path / "data" / "processed" / "corpus_v99" / "manifest.json"
    small.parent.mkdir(parents=True); large.parent.mkdir(parents=True)
    small.write_text(json.dumps({"train_tokens": 100}), encoding="utf-8")
    large.write_text(json.dumps({"train_tokens": 900}), encoding="utf-8")
    report = build_scale_plan(tmp_path)
    assert report["current_train_tokens"] == 900
    assert report["manifest_path"] == "data/processed/corpus_v99/manifest.json"
