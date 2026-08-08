from pathlib import Path

from fastapi.testclient import TestClient

from godot_coder import __version__
from godot_coder.ui.server import create_app


def test_studio_serves_index_and_lists_project(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (tmp_path / "data" / "raw" / "example.gd").write_text("extends Node\n", encoding="utf-8")

    client = TestClient(create_app(tmp_path))

    index = client.get("/")
    assert index.status_code == 200
    assert "Godot Coder Studio" in index.text

    files = client.get("/api/data/files")
    assert files.status_code == 200
    assert files.json()[0]["path"] == "data/raw/example.gd"


def test_curriculum_status_endpoint(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw" / "curriculum_v03").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (tmp_path / "data" / "raw" / "curriculum_v03" / "curriculum_manifest.json").write_text(
        '{"total_lessons": 1, "split_counts": {"train": 1, "val": 0, "test": 0}, "topic_counts": {}, "topics": []}',
        encoding="utf-8",
    )
    client = TestClient(create_app(tmp_path))
    response = client.get("/api/curriculum/status")
    assert response.status_code == 200
    assert response.json()["exists"] is True
    assert response.json()["manifest"]["total_lessons"] == 1


def test_hardware_probe_and_training_report_endpoints(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "reports" / "hardware").mkdir(parents=True)
    (tmp_path / "reports" / "training").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (tmp_path / "reports" / "hardware" / "vram_probe_latest.json").write_text(
        '{"profiles": [], "recommendation": {"profile_id": null}}', encoding="utf-8"
    )
    (tmp_path / "reports" / "training" / "run.json").write_text(
        '{"run_id": "run", "finished_at": 2, "cumulative_tokens_seen": 128}', encoding="utf-8"
    )
    client = TestClient(create_app(tmp_path))
    probe = client.get("/api/hardware/probe")
    assert probe.status_code == 200
    assert probe.json()["profiles"] == []
    reports = client.get("/api/training/reports")
    assert reports.status_code == 200
    assert reports.json()[0]["run_id"] == "run"


def test_manifest_endpoint_prefers_corpus_dataset(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    legacy = tmp_path / "data" / "processed"
    corpus = legacy / "corpus_v04"
    corpus.mkdir(parents=True)
    (legacy / "manifest.json").write_text('{"train_tokens": 1}', encoding="utf-8")
    (corpus / "manifest.json").write_text('{"train_tokens": 999}', encoding="utf-8")
    with TestClient(create_app(tmp_path)) as client:
        response = client.get("/api/data/manifest")
    assert response.status_code == 200
    assert response.json()["train_tokens"] == 999


def test_studio_brand_version_is_runtime_driven(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    with TestClient(create_app(tmp_path)) as client:
        index = client.get("/")
        overview = client.get("/api/overview")
    assert 'id="brand-version"' in index.text
    assert overview.json()["app_version"] == __version__


def test_professional_training_core_uses_visual_workflow(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    with TestClient(create_app(tmp_path)) as client:
        index = client.get("/")
    assert 'class="professional-steps"' in index.text
    assert 'id="professional-run"' in index.text
    assert 'data-workflow-step="smoke"' in index.text


def test_private_project_import_ui_and_status_endpoint(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    with TestClient(create_app(tmp_path)) as client:
        index = client.get("/")
        local = client.get("/api/corpus/local")
        denied = client.post("/api/jobs/corpus/local-import", json={"confirm_owned": False})
    assert 'id="import-local-sources"' in index.text
    assert local.status_code == 200
    assert local.json()["inbox_items"] == []
    assert denied.status_code == 400


def test_job_log_export_endpoint_returns_persisted_text_and_jsonl(tmp_path: Path) -> None:
    import time

    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    app = create_app(tmp_path)
    with TestClient(app) as client:
        started = app.state.jobs.start("test", ["-c", "print('export me')"])
        deadline = time.time() + 5
        while time.time() < deadline:
            current = client.get("/api/jobs/current").json()
            if current["status"] == "completed":
                break
            time.sleep(0.03)
        text = client.get(f"/api/jobs/{started['id']}/export?format=text")
        jsonl = client.get(f"/api/jobs/{started['id']}/export?format=jsonl")
    assert text.status_code == 200
    assert "export me" in text.text
    assert jsonl.status_code == 200
    assert '"record_type": "log"' in jsonl.text


def test_remote_self_check_uses_actual_local_request_port(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    captured = {}
    def fake_check(root, *, port=None):
        captured["port"] = port
        return {"ok": True, "port": port, "checks": []}
    monkeypatch.setattr("godot_coder.ui.server.remote_self_check", fake_check)
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:9988") as client:
        response = client.get("/api/remote/self-check")
    assert response.status_code == 200
    assert captured["port"] == 9988


def test_local_import_extra_env_maps_request_flags() -> None:
    from godot_coder.ui.server import _local_import_extra_env

    assert _local_import_extra_env(
        skip_project_import=False, fast_static=False, error_abort_threshold=None
    ) == {}
    env = _local_import_extra_env(skip_project_import=True, fast_static=True, error_abort_threshold=60)
    assert env == {
        "GODOT_CODER_SKIP_PROJECT_IMPORT": "1",
        "GODOT_CODER_FAST_STATIC": "1",
        "GODOT_CODER_ERROR_ABORT_THRESHOLD": "60",
    }



def test_train_endpoint_accepts_config_with_null_max_steps(tmp_path: Path, monkeypatch) -> None:
    """A config with max_steps: null (passes-driven run) must not crash the handler."""
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (tmp_path / "configs" / "null_steps.yaml").write_text(
        "model:\n"
        "  vocab_size: 256\n"
        "  max_seq_len: 32\n"
        "  n_layers: 1\n"
        "  d_model: 64\n"
        "  d_ff: 128\n"
        "  n_heads: 2\n"
        "train:\n"
        "  output_dir: checkpoints/null_steps\n"
        "  batch_size: 1\n"
        "  gradient_accumulation_steps: 1\n"
        "  max_steps: null\n"
        "  target_dataset_passes: 1\n"
        "  dtype: float32\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("godot_coder.ui.server.build_preflight", lambda *a, **k: {"can_start": True})
    started: dict[str, object] = {}

    def fake_start(kind, args, *, max_steps=None, extra_env=None):
        started["kind"] = kind
        started["max_steps"] = max_steps
        return {"id": "fake", "kind": kind, "status": "starting"}

    app = create_app(tmp_path)
    app.state.jobs.start = fake_start  # type: ignore[method-assign]
    with TestClient(app) as client:
        response = client.post("/api/jobs/train", json={"config": "configs/null_steps.yaml", "resume": None})
    assert response.status_code == 200
    assert started["kind"] == "training"
    assert started["max_steps"] is None


def test_train_endpoint_passes_explicit_max_steps_through(tmp_path: Path, monkeypatch) -> None:
    """An explicit max_steps in the config is forwarded to the job progress bar."""
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (tmp_path / "configs" / "fixed_steps.yaml").write_text(
        "model:\n"
        "  vocab_size: 256\n"
        "  max_seq_len: 32\n"
        "  n_layers: 1\n"
        "  d_model: 64\n"
        "  d_ff: 128\n"
        "  n_heads: 2\n"
        "train:\n"
        "  output_dir: checkpoints/fixed_steps\n"
        "  batch_size: 1\n"
        "  gradient_accumulation_steps: 1\n"
        "  max_steps: 600\n"
        "  dtype: float32\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("godot_coder.ui.server.build_preflight", lambda *a, **k: {"can_start": True})
    started: dict[str, object] = {}

    def fake_start(kind, args, *, max_steps=None, extra_env=None):
        started["kind"] = kind
        started["max_steps"] = max_steps
        return {"id": "fake", "kind": kind, "status": "starting"}

    app = create_app(tmp_path)
    app.state.jobs.start = fake_start  # type: ignore[method-assign]
    with TestClient(app) as client:
        response = client.post("/api/jobs/train", json={"config": "configs/fixed_steps.yaml", "resume": None})
    assert response.status_code == 200
    assert started["kind"] == "training"
    assert started["max_steps"] == 600
