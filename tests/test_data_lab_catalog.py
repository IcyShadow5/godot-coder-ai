from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from godot_coder.data import prepare_dataset
from godot_coder.tokenizer import ByteTokenizer
from godot_coder.ui.server import create_app
from godot_coder.ui.services import (
    build_data_catalog,
    delete_dataset_file,
    read_manifest,
)


def _prepare_corpus(root: Path) -> dict:
    audited = root / "data" / "corpus" / "audited"
    for split, name in (("train", "train_script.gd"), ("val", "val_script.gd"), ("test", "test_script.gd")):
        directory = audited / split / "example-source"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(
            (f"extends Node\nfunc {split}_value() -> int:\n\treturn 1\n" * 30),
            encoding="utf-8",
        )
    output = root / "data" / "processed" / "corpus_v076"
    return prepare_dataset(audited, output, ByteTokenizer(), shard_tokens=1024)


def test_data_catalog_lists_every_prepared_document_and_split_tokens(tmp_path: Path) -> None:
    manifest = _prepare_corpus(tmp_path)
    catalog = build_data_catalog(tmp_path)

    training = [item for item in catalog["entries"] if item["kind"] == "training"]
    assert len(training) == 3
    assert {item["split"] for item in training} == {"train", "val", "test"}
    assert all(item["tokens"] > 0 for item in training)
    assert all(item["storage_path"].startswith("data/corpus/audited/") for item in training)
    assert catalog["summary"]["train_tokens"] == manifest["train_tokens"]
    assert catalog["summary"]["val_tokens"] == manifest["val_tokens"]
    assert catalog["summary"]["test_tokens"] == manifest["test_tokens"]
    assert catalog["summary"]["total_tokens"] == sum(
        manifest[f"{split}_tokens"] for split in ("train", "val", "test")
    )


def test_data_catalog_hot_revision_detects_external_raw_file(tmp_path: Path) -> None:
    _prepare_corpus(tmp_path)
    before = build_data_catalog(tmp_path)
    manifest_mtime = float(before["manifest"]["manifest_modified_at"])

    external = tmp_path / "data" / "raw" / "external" / "added_while_open.gd"
    external.parent.mkdir(parents=True)
    external.write_text("extends Node\n", encoding="utf-8")
    os.utime(external, (manifest_mtime + 10, manifest_mtime + 10))

    after = build_data_catalog(tmp_path)
    assert after["revision"] != before["revision"]
    assert after["stale"] is True
    pending = [item for item in after["entries"] if item.get("storage_path") == "data/raw/external/added_while_open.gd"]
    assert pending and pending[0]["status"] == "pending"



def test_data_catalog_shows_new_audited_documents_before_retokenization(tmp_path: Path) -> None:
    _prepare_corpus(tmp_path)
    new_doc = tmp_path / "data" / "corpus" / "audited" / "train" / "new-source" / "new_system.gd"
    new_doc.parent.mkdir(parents=True)
    new_doc.write_text("extends Node\nfunc new_system() -> void:\n\tpass\n", encoding="utf-8")
    catalog = build_data_catalog(tmp_path)
    pending = [item for item in catalog["entries"] if item.get("storage_path") == "data/corpus/audited/train/new-source/new_system.gd"]
    assert pending and pending[0]["status"] == "pending"
    assert pending[0]["split"] == "train"
    assert pending[0]["editable"] is False
    assert catalog["summary"]["pending_documents"] >= 1

def test_delete_raw_file_creates_backup_and_marks_stream_stale(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw" / "curriculum" / "basic_example.gd"
    raw.parent.mkdir(parents=True)
    raw.write_text("extends Node\n", encoding="utf-8")

    result = delete_dataset_file(tmp_path, "data/raw/curriculum/basic_example.gd")

    assert not raw.exists()
    backup = tmp_path / result["backup"]
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "extends Node\n"
    state = json.loads((tmp_path / "data" / "data_lab_state.json").read_text(encoding="utf-8"))
    assert state["action"] == "delete"
    assert state["derived_data_stale"] is True


def test_delete_refuses_audited_training_document(tmp_path: Path) -> None:
    audited = tmp_path / "data" / "corpus" / "audited" / "train" / "source" / "safe.gd"
    audited.parent.mkdir(parents=True)
    audited.write_text("extends Node\n", encoding="utf-8")
    with pytest.raises(ValueError, match="only user-editable"):
        delete_dataset_file(tmp_path, "data/corpus/audited/train/source/safe.gd")


def test_manifest_selection_prefers_corpus_over_larger_named_curriculum(tmp_path: Path) -> None:
    curriculum = tmp_path / "data" / "processed" / "curriculum_v03"
    corpus = tmp_path / "data" / "processed" / "corpus_v076"
    curriculum.mkdir(parents=True)
    corpus.mkdir(parents=True)
    (curriculum / "manifest.json").write_text(json.dumps({"train_tokens": 9999999}), encoding="utf-8")
    (corpus / "manifest.json").write_text(json.dumps({"train_tokens": 531000}), encoding="utf-8")
    selected = read_manifest(tmp_path)
    assert selected is not None
    assert selected["manifest_path"] == "data/processed/corpus_v076/manifest.json"
    assert selected["train_tokens"] == 531000


def test_data_lab_api_catalog_and_delete_round_trip(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    raw = tmp_path / "data" / "raw" / "example.gd"
    raw.parent.mkdir(parents=True)
    raw.write_text("extends Node\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")

    with TestClient(create_app(tmp_path)) as client:
        catalog = client.get("/api/data/catalog")
        deleted = client.delete("/api/data/file", params={"path": "data/raw/example.gd"})
        refreshed = client.get("/api/data/catalog")

    assert catalog.status_code == 200
    assert any(item["storage_path"] == "data/raw/example.gd" for item in catalog.json()["entries"])
    assert deleted.status_code == 200
    assert not any(item.get("storage_path") == "data/raw/example.gd" for item in refreshed.json()["entries"])
