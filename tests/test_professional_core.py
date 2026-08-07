import json
from pathlib import Path

import numpy as np
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


def test_preflight_reports_yellow_without_hardware_result(tmp_path: Path) -> None:
    audit = {"summary": {"group_leaks": 0, "content_leaks": 0, "train_projects": 12, "val_projects": 3, "test_projects": 3, "parser_pass_rate": 0.95, "near_duplicates": 0, "records": 20, "accepted": 20, "warning": 0}}
    audit_path = tmp_path / "reports" / "audit"; audit_path.mkdir(parents=True)
    corpus = tmp_path / "data" / "corpus"; corpus.mkdir(parents=True)
    artifacts = tmp_path / "artifacts"; artifacts.mkdir()
    (corpus / "validation_report.json").write_text(json.dumps({"validator": "project-aware-v2", "records": 20, "passed": 20, "failed": 0, "context_warnings": 0, "prepared": 20}), encoding="utf-8")
    (corpus / "tokenizer_report.json").write_text(json.dumps({"fingerprint": "tok"}), encoding="utf-8")
    (artifacts / "tokenizer_bpe_godot.json").write_text("{}", encoding="utf-8")
    (audit_path / "corpus_audit_latest.json").write_text(json.dumps(audit), encoding="utf-8")
    data = tmp_path / "data" / "processed" / "corpus_v06"; data.mkdir(parents=True)
    configs = tmp_path / "configs"; configs.mkdir()
    config = configs / "night.yaml"
    config.write_text(yaml.safe_dump({
        "profile": {"id": "legacy"},
        "model": {"max_seq_len": 128, "n_layers": 1, "d_model": 32, "n_heads": 4, "d_ff": 64},
        "train": {"max_steps": None, "target_dataset_passes": 2.0, "batch_size": 1, "gradient_accumulation_steps": 1, "data_dir": "data/processed/corpus_v06", "tokenizer_path": "artifacts/tokenizer_bpe_godot.json"},
    }), encoding="utf-8")
    manifest = data / "manifest.json"
    manifest.write_text(json.dumps({"dataset_fingerprint": "d", "tokenizer_fingerprint": "tok", "train_tokens": 1_000_000, "val_tokens": 10_000, "test_tokens": 10_000}), encoding="utf-8")
    # The prepared stream must be the newest artifact in the pipeline.
    import os, time
    newest = time.time() + 2
    os.utime(manifest, (newest, newest))
    report = build_preflight(tmp_path, config_path=config)
    assert report["status"] == "yellow"
    assert not report["blockers"]


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
