from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import yaml

from godot_coder.corpus import status as corpus_status
from godot_coder.corpus_audit import _fragment_reasons, _normalize_for_duplicate, audit_corpus, build_preflight
from godot_coder.remote_access import configure_remote_access, load_remote_config, remote_self_check


def test_audit_ignores_hashes_and_delimiters_inside_strings() -> None:
    text = 'extends Node\nvar label := "Preis (inkl. Bonus) # sichtbar [x]" # echter Kommentar\nfunc ok() -> void:\n\tprint("}")\n'
    reasons = _fragment_reasons(text)
    assert "unbalanced_delimiters" not in reasons
    assert "encoding_damage" not in reasons
    assert "# sichtbar" in _normalize_for_duplicate(text)
    assert "echter Kommentar" not in _normalize_for_duplicate(text)


def test_valid_pass_callback_is_not_quarantined_as_damage(tmp_path: Path) -> None:
    corpus = tmp_path / "data" / "corpus"
    staged = corpus / "staged" / "src"
    staged.mkdir(parents=True)
    (staged / "callback.gd").write_text("extends Node\nfunc optional_hook() -> void:\n\tpass\n", encoding="utf-8")
    manifest = {
        "records": [{"record_id": "r", "source_id": "src", "group_id": "g", "kind": "godot_projects", "original_path": "callback.gd", "staged_path": "src/callback.gd", "split": "train", "license": "MIT", "validation_status": "passed"}],
        "sources": [{"id": "src", "license": "MIT", "url": "https://example.invalid", "commit": "abc"}],
        "skipped": [],
    }
    (corpus / "corpus_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = audit_corpus(tmp_path)
    assert report["summary"]["quarantine"] == 0
    assert report["summary"]["warning"] == 1


def _ready_training_workspace(root: Path, *, profile: str, train_tokens: int) -> Path:
    corpus = root / "data" / "corpus"; corpus.mkdir(parents=True)
    audit_dir = root / "reports" / "audit"; audit_dir.mkdir(parents=True)
    artifacts = root / "artifacts"; artifacts.mkdir()
    (corpus / "validation_report.json").write_text(json.dumps({"validator": "project-aware-v2", "prepared": 100, "records": 100, "passed": 100, "failed": 0, "context_warnings": 0}), encoding="utf-8")
    (corpus / "audit_report.json").write_text("{}", encoding="utf-8")
    (corpus / "tokenizer_report.json").write_text(json.dumps({"fingerprint": "tok"}), encoding="utf-8")
    (audit_dir / "corpus_audit_latest.json").write_text(json.dumps({"summary": {"group_leaks": 0, "content_leaks": 0, "train_projects": 20, "val_projects": 3, "test_projects": 3, "parser_pass_rate": 1.0, "near_duplicates": 0, "records": 100, "accepted": 100, "warning": 0}}), encoding="utf-8")
    (artifacts / "tokenizer_bpe_godot.json").write_text("{}", encoding="utf-8")
    data = root / "data" / "processed" / "corpus_v06"; data.mkdir(parents=True)
    manifest = data / "manifest.json"
    manifest.write_text(json.dumps({"train_tokens": train_tokens, "val_tokens": 1000, "test_tokens": 1000, "tokenizer_fingerprint": "tok"}), encoding="utf-8")
    configs = root / "configs"; configs.mkdir()
    config = configs / "run.yaml"
    config.write_text(yaml.safe_dump({
        "profile": {"id": profile},
        "model": {"max_seq_len": 128, "n_layers": 1, "d_model": 32, "n_heads": 4, "d_ff": 64},
        "train": {"max_steps": 1000, "batch_size": 1, "gradient_accumulation_steps": 1, "data_dir": "data/processed/corpus_v06", "tokenizer_path": "artifacts/tokenizer_bpe_godot.json", "output_dir": "checkpoints/test"},
    }), encoding="utf-8")
    newest = time.time() + 3
    os.utime(manifest, (newest, newest))
    return config


def test_preflight_allows_smoke_but_blocks_underfilled_balanced_training(tmp_path: Path) -> None:
    config = _ready_training_workspace(tmp_path, profile="balanced", train_tokens=100_000)
    smoke = build_preflight(tmp_path, config_path=config, mode="smoke")
    full = build_preflight(tmp_path, config_path=config, mode="full")
    assert smoke["can_start"] is True
    assert full["can_start"] is False
    assert any("5.000.000" in item for item in full["blockers"])


def test_remote_configuration_persists_port_and_self_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configure_remote_access(tmp_path, allowed_users=["owner@example.com"], pin="123456", port=9876)
    assert load_remote_config(tmp_path)["port"] == 9876
    class DummySocket:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr("godot_coder.remote_access.socket.create_connection", lambda *args, **kwargs: DummySocket())
    monkeypatch.setattr("godot_coder.remote_access.tailscale_status", lambda: {"online": True, "serve_url": "https://pc.example.ts.net", "error": None, "backend_state": "Running"})
    monkeypatch.setattr("godot_coder.remote_access.tailscale_serve_status", lambda port: {"configured": True, "target": f"http://127.0.0.1:{port}", "error": None})
    report = remote_self_check(tmp_path)
    assert report["ok"] is True
    assert report["port"] == 9876


def test_corpus_status_selects_highest_token_manifest(tmp_path: Path) -> None:
    corpus = tmp_path / "data" / "corpus"; corpus.mkdir(parents=True)
    (corpus / "source_registry.json").write_text(json.dumps({"sources": []}), encoding="utf-8")
    small = tmp_path / "data" / "processed" / "corpus_v04" / "manifest.json"
    large = tmp_path / "data" / "processed" / "corpus_v77" / "manifest.json"
    small.parent.mkdir(parents=True); large.parent.mkdir(parents=True)
    small.write_text(json.dumps({"train_tokens": 12}), encoding="utf-8")
    large.write_text(json.dumps({"train_tokens": 1234}), encoding="utf-8")
    result = corpus_status(tmp_path)
    assert result["processed"]["train_tokens"] == 1234
    assert result["processed_manifest_path"] == "data/processed/corpus_v77/manifest.json"
