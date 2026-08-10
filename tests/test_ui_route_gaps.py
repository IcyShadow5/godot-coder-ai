"""Tests for UI router endpoints that had no coverage.

Covers the non-streaming chat endpoint, the Godot validation endpoint,
config read/write with path security, preflight structure, and the
jobs history/export endpoints - all deterministic without Godot or a GPU.
"""

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from godot_coder.ui.server import create_app

MINIMAL_YAML = """\
profile:
  id: test
  title: Test
model:
  max_seq_len: 16
  n_layers: 1
  d_model: 32
  n_heads: 4
  d_ff: 64
  dropout: 0.0
  rope_base: 10000.0
  tie_embeddings: true
  gradient_checkpointing: false
train:
  tokenizer_path: artifacts/missing.json
  data_dir: data/processed
  output_dir: checkpoints/test
  device: cpu
  dtype: float32
  seed: 1
  batch_size: 1
  gradient_accumulation_steps: 1
  max_steps: 1
  learning_rate: 0.001
  min_learning_rate: 0.0001
  warmup_steps: 0
  weight_decay: 0.0
  beta1: 0.9
  beta2: 0.95
  gradient_clip: 1.0
  log_interval: 1
  eval_interval: 1
  eval_batches: 1
  save_interval: 1
"""


def _scaffold(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")


def _fake_generation(app, text: str = "return a + b\n"):
    def fake_generate(checkpoint, prompt, *, max_new_tokens, temperature, top_k, device_name="auto"):
        return text

    def fake_unload():
        pass

    app.state.generation = SimpleNamespace(generate=fake_generate, unload=fake_unload)


def _client(app):
    return TestClient(app)


def test_chat_generate_non_stream_success(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    _fake_generation(app)
    with _client(app) as client:
        response = client.post(
            "/api/chat/generate",
            json={
                "checkpoint": "checkpoints/v06_balanced/best.pt",
                "prompt": "func add(a: int, b: int) -> int:\n",
                "max_new_tokens": 8,
            },
        )
    assert response.status_code == 200
    assert response.json()["text"] == "return a + b\n"
    assert response.json()["checkpoint"] == "checkpoints/v06_balanced/best.pt"


def test_chat_generate_rejects_while_job_running(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    _fake_generation(app)

    def fake_current():
        return {"kind": "training", "status": "running"}

    app.state.jobs.current = fake_current  # type: ignore[method-assign]
    with _client(app) as client:
        response = client.post(
            "/api/chat/generate",
            json={"checkpoint": "checkpoints/x.pt", "prompt": "func f():\n", "max_new_tokens": 4},
        )
    assert response.status_code == 409


def test_chat_generate_400_on_generation_error(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)

    def failing_generate(checkpoint, prompt, *, max_new_tokens, temperature, top_k, device_name="auto"):
        raise ValueError("checkpoint not found")

    def fake_unload():
        pass

    app.state.generation = SimpleNamespace(generate=failing_generate, unload=fake_unload)
    with _client(app) as client:
        response = client.post(
            "/api/chat/generate",
            json={"checkpoint": "checkpoints/missing.pt", "prompt": "func f():\n"},
        )
    assert response.status_code == 400
    assert "checkpoint not found" in response.json()["detail"]


def test_chat_validate_passthrough(tmp_path: Path, monkeypatch) -> None:
    _scaffold(tmp_path)

    def fake_validate(project_root, code, project_path="data/raw/seed_project"):
        return {"passed": True, "return_code": 0, "timed_out": False, "output": "ok"}

    monkeypatch.setattr("godot_coder.ui.routers.chat.validate_code", fake_validate)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.post("/api/chat/validate", json={"code": "extends Node\n"})
    assert response.status_code == 200
    assert response.json()["passed"] is True


def test_chat_validate_400_on_error(tmp_path: Path, monkeypatch) -> None:
    _scaffold(tmp_path)

    def fake_validate(project_root, code, project_path="data/raw/seed_project"):
        raise FileNotFoundError("Godot executable was not found")

    monkeypatch.setattr("godot_coder.ui.routers.chat.validate_code", fake_validate)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.post("/api/chat/validate", json={"code": "extends Node\n"})
    assert response.status_code == 400


def test_config_raw_get_existing_file(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "configs" / "smoke.yaml").write_text(MINIMAL_YAML, encoding="utf-8")
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.get("/api/config/raw", params={"path": "configs/smoke.yaml"})
    assert response.status_code == 200
    assert response.json()["path"] == "configs/smoke.yaml"
    assert "model:" in response.json()["content"]


def test_config_raw_get_missing_file_400(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.get("/api/config/raw", params={"path": "configs/nope.yaml"})
    assert response.status_code == 400


def test_config_raw_get_escape_rejected(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "pyproject.toml").write_text("secret", encoding="utf-8")
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.get("/api/config/raw", params={"path": "../pyproject.toml"})
    assert response.status_code == 400


def test_config_raw_put_valid_saves(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.put(
            "/api/config/raw",
            json={"path": "configs/my_run.yaml", "content": MINIMAL_YAML},
        )
    assert response.status_code == 200
    assert response.json()["saved"] is True
    saved = (tmp_path / "configs" / "my_run.yaml").read_text(encoding="utf-8")
    assert "model:" in saved


def test_config_raw_put_invalid_yaml_400(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.put(
            "/api/config/raw",
            json={"path": "configs/bad.yaml", "content": "model: [unclosed"},
        )
    assert response.status_code == 400


def test_config_raw_put_non_config_path_400(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.put(
            "/api/config/raw",
            json={"path": "data/raw/script.gd", "content": MINIMAL_YAML},
        )
    assert response.status_code == 400


def test_config_raw_put_wrong_extension_400(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.put(
            "/api/config/raw",
            json={"path": "configs/notes.txt", "content": MINIMAL_YAML},
        )
    assert response.status_code == 400


def test_preflight_returns_structure(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.get("/api/preflight", params={"mode": "smoke"})
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("can_start"), bool)
    assert isinstance(payload.get("blockers"), list)
    assert isinstance(payload.get("warnings"), list)


def test_jobs_history_empty(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.get("/api/jobs/history")
    assert response.status_code == 200
    assert response.json() == []


def test_job_export_unknown_404(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.get("/api/jobs/does-not-exist/export")
    assert response.status_code == 404


def test_checkpoints_and_configs_lists(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        checkpoints = client.get("/api/checkpoints")
        configs = client.get("/api/configs")
    assert checkpoints.status_code == 200
    assert configs.status_code == 200
    assert isinstance(checkpoints.json(), list)
    assert isinstance(configs.json(), list)
