"""Tests for the training router's job-starting POST handlers.

Each handler kicks off a real background job through ``app.state.jobs.start``.
We never want a real subprocess in the test suite, so the JobManager is
replaced by a recording fake that captures the kind, args and max_steps the
router hands over - the actual launch contract. Preflight and the generation
service are stubbed the same way.
"""

from pathlib import Path

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
  tokenizer_path: artifacts/tokenizer.json
  data_dir: data/processed
  output_dir: checkpoints/test
  device: cpu
  dtype: float32
  seed: 1
  batch_size: 1
  gradient_accumulation_steps: 1
  max_steps: 5
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


class FakeJobs:
    """Records start() calls instead of launching real subprocesses."""

    def __init__(self) -> None:
        self.starts: list[dict] = []
        self._busy = False

    def stop(self) -> None:
        """Lifespan teardown calls this; nothing is running."""

    def start(self, kind, args, *, max_steps=None, extra_env=None, initial_progress=None):
        if self._busy:
            raise RuntimeError("another studio task is already running")
        self.starts.append(
            {
                "kind": kind,
                "args": list(args),
                "max_steps": max_steps,
                "extra_env": dict(extra_env or {}),
                "initial_progress": initial_progress,
            }
        )
        return {"id": "fake-job", "kind": kind, "status": "running"}


class FakeGeneration:
    def __init__(self) -> None:
        self.unload_calls = 0

    def unload(self) -> None:
        self.unload_calls += 1


def _scaffold(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "night.yaml").write_text(MINIMAL_YAML, encoding="utf-8")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "processed").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "checkpoints" / "best.pt").write_bytes(b"fake-checkpoint")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "tokenizer.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")


def _make_app(tmp_path: Path, monkeypatch):
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    jobs = FakeJobs()
    gen = FakeGeneration()
    monkeypatch.setattr(app.state, "jobs", jobs)
    monkeypatch.setattr(app.state, "generation", gen)
    # Preflight must pass so training routes reach the job start. The real
    # build_preflight is called via to_thread(root, config_path=..., mode=...),
    # so a variadic stub matches its signature.
    monkeypatch.setattr(
        "godot_coder.ui.routers.training.build_preflight",
        lambda *a, **kw: {"can_start": True, "blockers": [], "warnings": []},
    )
    return app, jobs, gen


def _client(app):
    return TestClient(app)


def test_start_hardware_probe_launches_job(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post("/api/jobs/hardware/probe")
        # Exactly one unload - the handler's. The TestClient teardown adds
        # another one later, so the count is checked inside the context.
        assert gen.unload_calls == 1
    assert response.status_code == 200
    assert response.json()["id"] == "fake-job"
    assert len(jobs.starts) == 1
    call = jobs.starts[0]
    assert call["kind"] == "hardware-profile-probe"
    assert call["args"] == ["-m", "godot_coder.profile_probe", "--root", str(tmp_path)]
    assert call["max_steps"] == 3


def test_start_hardware_autotune_launches_job(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post("/api/jobs/hardware/autotune")
        assert gen.unload_calls == 1
    assert response.status_code == 200
    assert response.json()["id"] == "fake-job"
    assert len(jobs.starts) == 1
    call = jobs.starts[0]
    assert call["kind"] == "hardware-autotune"
    assert call["args"] == ["-m", "godot_coder.autotune", "--root", str(tmp_path), "--full"]
    assert call["max_steps"] == 80


def test_start_training_launches_job_with_config(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post("/api/jobs/train", json={"config": "configs/night.yaml"})
        assert gen.unload_calls == 1
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "fake-job"
    assert len(jobs.starts) == 1
    call = jobs.starts[0]
    assert call["kind"] == "training"
    assert call["args"] == [
        "-m",
        "godot_coder.train",
        "--config",
        str(tmp_path / "configs" / "night.yaml"),
    ]
    # max_steps comes from the MINIMAL_YAML fixture (5) and is forwarded to
    # the job - keep the two in sync when editing the fixture.
    assert call["max_steps"] == 5


def test_start_training_appends_resume_checkpoint(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post(
            "/api/jobs/train",
            json={"config": "configs/night.yaml", "resume": "checkpoints/best.pt"},
        )
    assert response.status_code == 200, response.text
    assert len(jobs.starts) == 1
    call = jobs.starts[0]
    assert call["args"][-2:] == ["--resume", str(tmp_path / "checkpoints" / "best.pt")]
    assert call["initial_progress"]["resumed_from"] == "checkpoints/best.pt"
    # Der Fake-Checkpoint ist kein echtes .pt -> Peek schlaegt fehl, der Run
    # darf davon aber nicht blockiert werden (nur resumed_from, kein step).
    assert "resume_step" not in call["initial_progress"]


def test_start_training_rejects_resume_outside_checkpoints(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post(
            "/api/jobs/train",
            json={"config": "configs/night.yaml", "resume": "../artifacts/tokenizer.json"},
        )
    assert response.status_code == 400
    assert jobs.starts == []


def test_start_training_blocked_by_preflight(tmp_path: Path, monkeypatch) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    jobs = FakeJobs()
    monkeypatch.setattr(app.state, "jobs", jobs)
    monkeypatch.setattr(
        "godot_coder.ui.routers.training.build_preflight",
        lambda *a, **kw: {"can_start": False, "blockers": ["Dataset missing"], "warnings": []},
    )
    with _client(app) as client:
        response = client.post("/api/jobs/train", json={"config": "configs/night.yaml"})
    assert response.status_code == 400
    assert "Dataset missing" in response.json()["detail"]
    assert jobs.starts == []


def test_start_benchmark_launches_job(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post("/api/jobs/benchmark", json={"checkpoint": "checkpoints/best.pt"})
        assert gen.unload_calls == 1
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "fake-job"
    assert len(jobs.starts) == 1
    call = jobs.starts[0]
    assert call["kind"] == "benchmark"
    assert call["args"] == [
        "-m",
        "godot_coder.benchmark",
        "--project-root",
        str(tmp_path),
        "--checkpoint",
        "checkpoints/best.pt",
    ]
    assert call["max_steps"] == 16


def test_start_benchmark_rejects_missing_checkpoint(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post("/api/jobs/benchmark", json={"checkpoint": "checkpoints/nope.pt"})
    assert response.status_code == 400
    assert jobs.starts == []


def test_start_prepare_launches_job(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post(
            "/api/jobs/prepare",
            json={
                "input_dir": "data/raw",
                "output_dir": "data/processed",
                "tokenizer": "artifacts/tokenizer.json",
                "val_ratio": 0.2,
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "fake-job"
    assert len(jobs.starts) == 1
    call = jobs.starts[0]
    assert call["kind"] == "prepare-data"
    assert call["args"] == [
        "-m",
        "godot_coder.prepare_data",
        "--input",
        str(tmp_path / "data" / "raw"),
        "--output",
        str(tmp_path / "data" / "processed"),
        "--tokenizer",
        str(tmp_path / "artifacts" / "tokenizer.json"),
        "--val-ratio",
        "0.2",
    ]


def test_start_prepare_rejects_paths_outside_data(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post(
            "/api/jobs/prepare",
            json={
                "input_dir": "../configs",
                "output_dir": "data/processed",
                "tokenizer": "artifacts/tokenizer.json",
            },
        )
    assert response.status_code == 400
    assert jobs.starts == []


def test_start_hardware_probe_409_when_busy(tmp_path: Path, monkeypatch) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    jobs = FakeJobs()
    jobs._busy = True
    gen = FakeGeneration()
    monkeypatch.setattr(app.state, "jobs", jobs)
    monkeypatch.setattr(app.state, "generation", gen)
    with _client(app) as client:
        response = client.post("/api/jobs/hardware/probe")
        # The handler unloads the model first, then the busy JobManager
        # refuses the launch - both halves of the contract.
        assert gen.unload_calls == 1
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]
    assert jobs.starts == []


def test_start_training_resume_surfaces_step(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    checkpoint_root = tmp_path / "checkpoints" / "v06"
    checkpoint_root.mkdir(parents=True)
    checkpoint = checkpoint_root / "step_00000005.pt"
    checkpoint.write_bytes(b"dummy")
    monkeypatch.setattr(
        "godot_coder.ui.routers.training.load_checkpoint",
        lambda path, map_location: {"step": 5, "best_val_loss": 1.2},
    )
    with _client(app) as client:
        response = client.post(
            "/api/jobs/train",
            json={"config": "configs/night.yaml", "resume": "checkpoints/v06/step_00000005.pt"},
        )
    assert response.status_code == 200, response.text
    call = jobs.starts[-1]
    assert "--resume" in call["args"]
    assert call["initial_progress"]["resume_step"] == 5
    assert call["initial_progress"]["resumed_from"] == "checkpoints/v06/step_00000005.pt"

def test_start_verify_launches_job_with_two_checkpoints(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    (tmp_path / "checkpoints" / "v06_a").mkdir(parents=True, exist_ok=True)
    (tmp_path / "checkpoints" / "v06_b").mkdir(parents=True, exist_ok=True)
    (tmp_path / "checkpoints" / "v06_a" / "best.pt").write_bytes(b"a")
    (tmp_path / "checkpoints" / "v06_b" / "best.pt").write_bytes(b"b")
    with _client(app) as client:
        response = client.post(
            "/api/jobs/verify",
            json={"checkpoint_a": "checkpoints/v06_a/best.pt", "checkpoint_b": "checkpoints/v06_b/best.pt"},
        )
    assert response.status_code == 200, response.text
    call = jobs.starts[0]
    assert call["kind"] == "verify"
    assert call["args"][1] == "godot_coder.verify"
    assert "--checkpoint-a" in call["args"]
    assert "--checkpoint-b" in call["args"]
    assert "--work-dir" in call["args"]
    work_index = call["args"].index("--work-dir")
    work_dir = Path(call["args"][work_index + 1])
    # The work dir must never live inside the project tree.
    try:
        work_dir.resolve().relative_to(tmp_path.resolve())
    except ValueError:
        pass
    else:
        raise AssertionError("verify work dir must be outside the project tree")


def test_start_verify_single_checkpoint_omits_b_flag(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post(
            "/api/jobs/verify",
            json={"checkpoint_a": "checkpoints/best.pt"},
        )
    assert response.status_code == 200, response.text
    call = jobs.starts[0]
    assert "--checkpoint-b" not in call["args"]


def test_start_verify_rejects_checkpoint_outside_vault(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post(
            "/api/jobs/verify",
            json={"checkpoint_a": "../artifacts/tokenizer.json"},
        )
    assert response.status_code == 400
    assert jobs.starts == []
