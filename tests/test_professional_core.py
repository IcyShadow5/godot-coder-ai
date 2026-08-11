import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch
import yaml

from godot_coder.autotune import _variant
from godot_coder.config import ModelConfig, TrainConfig
from godot_coder.corpus_audit import audit_corpus, build_preflight
from godot_coder.data import TokenStream, prepare_dataset
from godot_coder.model import TinyGPT
from godot_coder.tokenizer import ByteTokenizer


def test_train_config_supports_token_budget_and_nested_early_stopping() -> None:
    cfg = TrainConfig.from_mapping({
        "max_steps": None,
        "max_tokens": 1000,
        "batch_size": 2,
        "gradient_accumulation_steps": 2,
        "early_stopping": {"enabled": True, "patience": 3, "min_delta": 0.02},
        "compile": {"enabled": True, "mode": "reduce-overhead"},
    })
    cfg.validate()
    assert cfg.resolve_max_steps(train_tokens=10_000, tokens_per_optimizer_step=128) == 8
    assert cfg.early_stopping_enabled and cfg.early_stopping_patience == 3
    assert cfg.compile_model and cfg.compile_mode == "reduce-overhead"


def test_train_config_uses_smallest_professional_limit() -> None:
    cfg = TrainConfig(max_steps=100, max_tokens=500, target_dataset_passes=2.0, batch_size=1, gradient_accumulation_steps=1, warmup_steps=10)
    cfg.validate()
    assert cfg.resolve_max_steps(train_tokens=1000, tokens_per_optimizer_step=100) == 5


def test_train_config_warmup_check_allows_resume_past_warmup() -> None:
    """A config whose warmup exceeds max_steps is invalid for a fresh run but
    must not block a resume that already sits past the warmup phase."""
    cfg = TrainConfig(max_steps=100, warmup_steps=250, batch_size=1, gradient_accumulation_steps=1)
    with pytest.raises(ValueError, match="warmup_steps must be smaller"):
        cfg.validate()
    cfg.validate(start_step=300)  # warmup already finished; resume is fine
    cfg.validate(start_step=250)  # exactly at warmup end is also fine


def test_dataset_v2_contains_document_index_and_deterministic_windows(tmp_path: Path) -> None:
    raw = tmp_path / "raw"; raw.mkdir()
    for index in range(6):
        (raw / f"s{index}.gd").write_text((f"extends Node\nvar value := {index}\n" * 60), encoding="utf-8")
    out = tmp_path / "processed"
    manifest = prepare_dataset(raw, out, ByteTokenizer(), val_ratio=0.33, shard_tokens=1500)
    assert manifest["format_version"] == 2
    assert manifest["splits"]["train"]["documents"]
    assert len(manifest["splits"]["train"]["shards"]) >= 2
    stream = TokenStream.from_data_dir(out, "train")
    first = stream.fixed_windows(32, 5, 77)
    second = stream.fixed_windows(32, 5, 77)
    assert np.array_equal(first, second)
    x, y = stream.batch_at(first[:2], 32, torch.device("cpu"))
    assert x.shape == y.shape == (2, 32)


def test_kv_cache_matches_full_forward() -> None:
    torch.manual_seed(4)
    model = TinyGPT(ModelConfig(vocab_size=64, max_seq_len=32, n_layers=2, d_model=32, n_heads=4, d_ff=64, dropout=0.0))
    model.eval()
    tokens = torch.randint(0, 64, (1, 8))
    full = model(tokens).logits[:, -1]
    _, cache = model.forward_cached(tokens[:, :-1])
    cached, _ = model.forward_cached(tokens[:, -1:], cache)
    assert torch.allclose(full, cached[:, -1], atol=1e-5, rtol=1e-4)


def test_greedy_cached_generation_matches_fallback() -> None:
    torch.manual_seed(9)
    model = TinyGPT(ModelConfig(vocab_size=48, max_seq_len=24, n_layers=1, d_model=32, n_heads=4, d_ff=64, dropout=0.0))
    prompt = torch.tensor([[1, 2, 3, 4]])
    cached = model.generate(prompt.clone(), max_new_tokens=5, temperature=0, use_kv_cache=True)
    fallback = model.generate(prompt.clone(), max_new_tokens=5, temperature=0, use_kv_cache=False)
    assert torch.equal(cached, fallback)


def _make_audit_workspace(tmp_path: Path, *, leak: bool = False) -> Path:
    root = tmp_path
    corpus = root / "data" / "corpus"; staged = corpus / "staged" / "src"; staged.mkdir(parents=True)
    records = []
    for index, split in enumerate(("train", "val", "test", "train")):
        text = "extends Node\nfunc value() -> int:\n\treturn 1\n" if index in {0, 3} else f"extends Node\nfunc value_{index}() -> int:\n\treturn {index}\n"
        name = f"r{index}.gd"; (staged / name).write_text(text, encoding="utf-8")
        records.append({
            "record_id": f"r{index}", "source_id": "src", "source_title": "Source",
            "group_id": "same" if leak and index < 2 else f"group-{index}", "kind": "godot_projects",
            "original_path": name, "staged_path": f"src/{name}", "split": split,
            "content_sha256": "x", "bytes": len(text), "license": "MIT", "attribution": "Tester",
            "validation_status": "passed", "validation_error": None,
        })
    manifest = {"format": "godot-coder-licensed-corpus", "format_version": 2, "records": records,
                "sources": [{"id": "src", "url": "https://example.invalid/src.git", "branch": "v1", "commit": "abc", "license": "MIT", "attribution": "Tester"}]}
    (corpus / "corpus_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_corpus_audit_quarantines_normalized_duplicates(tmp_path: Path) -> None:
    report = audit_corpus(_make_audit_workspace(tmp_path))
    assert report["summary"]["exact_duplicates"] == 1
    assert report["summary"]["quarantine"] >= 1
    assert (tmp_path / "data" / "corpus" / "audited" / "train").exists()


def test_corpus_audit_detects_group_leakage(tmp_path: Path) -> None:
    report = audit_corpus(_make_audit_workspace(tmp_path, leak=True))
    assert report["summary"]["group_leaks"] == 1


def test_audit_snapshot_cleanup_on_success(tmp_path: Path) -> None:
    """Stale audit_checkpoint.json from a prior crash is removed after success."""
    root = _make_audit_workspace(tmp_path)
    snapshot = root / "data" / "corpus" / "audit_checkpoint.json"
    snapshot.write_text('{"format":"godot-coder-audit-checkpoint","dirty":true}', encoding="utf-8")
    assert snapshot.exists()
    audit_corpus(root)
    assert not snapshot.exists(), "stale audit_checkpoint.json should be cleaned after success"


def _make_preflight_workspace(tmp_path: Path, *, validator: str = "project-aware-v2", data_dir: str = "data/processed/corpus_v06", train_tokens: int = 1_000_000) -> Path:
    audit = {"summary": {"group_leaks": 0, "content_leaks": 0, "train_projects": 12, "val_projects": 3, "test_projects": 3, "parser_pass_rate": 0.95, "near_duplicates": 0, "records": 20, "accepted": 20, "warning": 0}}
    audit_path = tmp_path / "reports" / "audit"; audit_path.mkdir(parents=True)
    corpus = tmp_path / "data" / "corpus"; corpus.mkdir(parents=True)
    artifacts = tmp_path / "artifacts"; artifacts.mkdir()
    (corpus / "validation_report.json").write_text(json.dumps({"validator": validator, "records": 20, "passed": 20, "failed": 0, "context_warnings": 0, "prepared": 20}), encoding="utf-8")
    (corpus / "tokenizer_report.json").write_text(json.dumps({"fingerprint": "tok"}), encoding="utf-8")
    (artifacts / "tokenizer_bpe_godot.json").write_text("{}", encoding="utf-8")
    (audit_path / "corpus_audit_latest.json").write_text(json.dumps(audit), encoding="utf-8")
    data = tmp_path / "data" / "processed" / Path(data_dir).name; data.mkdir(parents=True)
    configs = tmp_path / "configs"; configs.mkdir()
    config = configs / "night.yaml"
    config.write_text(yaml.safe_dump({
        "profile": {"id": "legacy"},
        "model": {"max_seq_len": 128, "n_layers": 1, "d_model": 32, "n_heads": 4, "d_ff": 64},
        "train": {"max_steps": None, "target_dataset_passes": 2.0, "batch_size": 1, "gradient_accumulation_steps": 1, "data_dir": data_dir, "tokenizer_path": "artifacts/tokenizer_bpe_godot.json"},
    }), encoding="utf-8")
    manifest = data / "manifest.json"
    manifest.write_text(json.dumps({"dataset_fingerprint": "d", "tokenizer_fingerprint": "tok", "train_tokens": train_tokens, "val_tokens": 10_000, "test_tokens": 10_000}), encoding="utf-8")
    # The prepared stream must be the newest artifact in the pipeline.
    import os, time
    newest = time.time() + 2
    os.utime(manifest, (newest, newest))
    return tmp_path


def test_preflight_reports_yellow_without_hardware_result(tmp_path: Path) -> None:
    root = _make_preflight_workspace(tmp_path)
    report = build_preflight(root, config_path=root / "configs" / "night.yaml")
    assert report["status"] == "yellow"
    assert not report["blockers"]


def test_preflight_accepts_current_project_aware_validator(tmp_path: Path) -> None:
    """The v0.10.4+ pipeline writes project-aware-v4 reports; the preflight must
    accept every project-aware revision instead of pinning the old v2 string."""
    root = _make_preflight_workspace(tmp_path, validator="project-aware-v4")
    report = build_preflight(root, config_path=root / "configs" / "night.yaml")
    assert not any("project-based validation" in blocker for blocker in report["blockers"])


def test_preflight_does_not_gate_synthetic_dataset_on_corpus_stages(tmp_path: Path) -> None:
    """A curriculum/synthetic stream is independent of the corpus pipeline and
    must not be flagged stale when validation or audit artifacts are newer."""
    root = _make_preflight_workspace(tmp_path, validator="project-aware-v4", data_dir="data/processed/curriculum_v03")
    import os
    # Push the corpus stages well past the stream: Windows clock granularity is
    # coarse, so an explicit offset from the manifest is deterministic.
    manifest = root / "data" / "processed" / "curriculum_v03" / "manifest.json"
    newest = manifest.stat().st_mtime + 100.0
    for rel in ("data/corpus/validation_report.json", "data/corpus/corpus_manifest.json", "data/corpus/audit_report.json"):
        stage = root / rel
        stage.parent.mkdir(parents=True, exist_ok=True)
        if not stage.exists():
            stage.write_text("{}", encoding="utf-8")
        os.utime(stage, (newest, newest))
    report = build_preflight(root, config_path=root / "configs" / "night.yaml")
    assert not any("older than the validation" in blocker for blocker in report["blockers"])


def test_preflight_still_gates_corpus_stream_on_newer_pipeline_stages(tmp_path: Path) -> None:
    """Corpus-derived streams keep the freshness gate: a validation report newer
    than the prepared stream must still block training."""
    root = _make_preflight_workspace(tmp_path, validator="project-aware-v4")
    import os
    manifest = root / "data" / "processed" / "corpus_v06" / "manifest.json"
    newest = manifest.stat().st_mtime + 100.0
    stage = root / "data" / "corpus" / "validation_report.json"
    os.utime(stage, (newest, newest))
    report = build_preflight(root, config_path=root / "configs" / "night.yaml")
    assert any("older than the validation" in blocker for blocker in report["blockers"])



def test_preflight_full_allows_small_synthetic_dataset(tmp_path: Path) -> None:
    """Synthetic streams (curriculum) are deliberately small: the profile token
    gate must not block them, even in full mode."""
    root = _make_preflight_workspace(tmp_path, validator="project-aware-v4", data_dir="data/processed/curriculum_v03", train_tokens=88_000)
    report = build_preflight(root, config_path=root / "configs" / "night.yaml")
    assert not any("needs at least" in blocker for blocker in report["blockers"])


def test_preflight_full_keeps_token_gate_for_corpus_stream(tmp_path: Path) -> None:
    """Corpus-derived streams keep the profile token gate: a small corpus in
    full mode must still block."""
    root = _make_preflight_workspace(tmp_path, validator="project-aware-v4", train_tokens=88_000)
    report = build_preflight(root, config_path=root / "configs" / "night.yaml")
    gate = [blocker for blocker in report["blockers"] if "needs at least" in blocker]
    assert gate
    # English sentence -> English thousands separators, not German dots.
    assert "," in gate[0] and "." not in gate[0]


def test_autotune_variant_changes_context_checkpointing_and_batch(tmp_path: Path) -> None:
    configs = tmp_path / "configs"; configs.mkdir(parents=True)
    base = {"profile": {"id": "balanced"}, "model": {"max_seq_len": 1024, "n_layers": 1, "d_model": 32, "n_heads": 4, "d_ff": 64, "gradient_checkpointing": False}, "train": {"max_steps": 10, "batch_size": 1, "gradient_accumulation_steps": 1}}
    (configs / "corpus_balanced_90m.yaml").write_text(yaml.safe_dump(base), encoding="utf-8")
    target = _variant(tmp_path, "B91-2048", "configs/corpus_balanced_90m.yaml", 2048, True, False, 4)
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert raw["model"]["max_seq_len"] == 2048
    assert raw["model"]["gradient_checkpointing"] is True
    assert raw["train"]["batch_size"] == 4


def test_autotune_marks_vram_oversubscription_unsafe() -> None:
    from godot_coder.autotune import _mark_safety

    result = _mark_safety({"status": "pass", "peak_reserved_fraction": 1.17})
    assert result["status"] == "unsafe"
    assert "shared RAM" in result["unsafe_reason"]


def test_job_manager_parses_autotune_progress_pattern() -> None:
    from godot_coder.ui.jobs import _AUTOTUNE_PATTERN

    match = _AUTOTUNE_PATTERN.search("autotune=43/80 profile=C163-1024")
    assert match is not None
    assert match.group(1) == "43"
    assert match.group(2) == "80"



def test_audit_resume_skips_processed_records(tmp_path: Path) -> None:
    """A stale-but-valid checkpoint must be read and resumed, not discarded.

    Regression: the checkpoint used to be written and deleted on success but
    never loaded, so a mid-audit crash restarted the whole corpus from record
    1. Now the enriched records from the checkpoint are replayed and the run
    continues from where it stopped.
    """
    root = _make_audit_workspace(tmp_path)
    from godot_coder.corpus_audit import _manifest_fingerprint, audit_corpus

    # Simulate a crash after the first two records: hand-write a checkpoint
    # with two enriched records + matching manifest fingerprint.
    manifest = json.loads((root / "data" / "corpus" / "corpus_manifest.json").read_text(encoding="utf-8"))
    snapshot = root / "data" / "corpus" / "audit_checkpoint.json"

    # Build a minimal but valid enriched record set by replaying the first two
    # staged files through the same normalization the audit uses.
    first_two = manifest["records"][:2]
    enriched_fake: list[dict[str, Any]] = []
    for rec in first_two:
        enriched_fake.append({
            "record_id": rec["record_id"],
            "source_id": rec["source_id"],
            "group_id": rec["group_id"],
            "split": rec["split"],
            "staged_path": rec["staged_path"],
            "normalized_sha256": "a" * 64,
            "simhash64": "0" * 16,
            "token_estimate": 3,
            "duplicate_of": None,
            "quality_status": "accepted",
            "quality_reasons": [],
        })
    snapshot.write_text(json.dumps({
        "format": "godot-coder-audit-checkpoint",
        "progress": {"records": 2, "total": len(manifest["records"])},
        "enriched": enriched_fake,
        "near_pairs": [],
        "manifest_fingerprint": _manifest_fingerprint(manifest),
    }), encoding="utf-8")

    report = audit_corpus(root)
    # All 4 records must be present (2 resumed + 2 fresh).
    assert report["summary"]["records"] == 4
    assert not snapshot.exists(), "checkpoint must be cleaned after a successful resume"


def test_pipeline_freshness_stages_for_each_manifest_state(tmp_path: Path) -> None:
    """_pipeline_freshness reports stages that match the stream kind:
    none for a fresh project, raw_source only for synthetic streams, and the
    full corpus set for corpus-derived streams."""
    from godot_coder.corpus_audit import _pipeline_freshness

    # 1. No manifest at all (fresh project) -> no stages listed.
    fresh = _pipeline_freshness(tmp_path, None)
    assert fresh["stages"] == {}
    assert fresh["stale"] is False
    assert fresh["processed_mtime"] is None

    # 2. Synthetic stream (curriculum) -> raw_source only.
    curriculum = tmp_path / "data" / "processed" / "curriculum_v03" / "manifest.json"
    curriculum.parent.mkdir(parents=True, exist_ok=True)
    curriculum.write_text("{}", encoding="utf-8")
    raw = tmp_path / "data" / "raw" / "curriculum_v03"
    raw.mkdir(parents=True, exist_ok=True)
    synthetic = _pipeline_freshness(tmp_path, curriculum)
    assert set(synthetic["stages"]) == {"raw_source"}
    assert synthetic["stages"]["raw_source"]["exists"] is True

    # 3. Corpus stream -> full pipeline stage set.
    corpus_manifest = tmp_path / "data" / "processed" / "corpus_v06" / "manifest.json"
    corpus_manifest.parent.mkdir(parents=True, exist_ok=True)
    corpus_manifest.write_text("{}", encoding="utf-8")
    corpus = _pipeline_freshness(tmp_path, corpus_manifest)
    assert set(corpus["stages"]) == {"scan", "validation", "audit", "tokenizer", "data_changes"}
    assert set(corpus["stages"]["scan"]) == {"exists", "modified_at"}
