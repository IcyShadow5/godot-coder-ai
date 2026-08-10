"""Tests for the corpus router's source management and job-starting handlers.

Source management (``PUT /api/corpus/sources``) runs through the real
``save_registry`` validation so create/delete/validation semantics are covered
end to end. Every job-starting handler only records its launch contract via a
recording FakeJobs - never a real subprocess. Status-reading helpers that the
handlers depend on (``corpus_status``, ``local_source_status``,
``load_registry``) are stubbed where the test cares about a specific value.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from godot_coder.ui.server import create_app

GIT_SOURCE = {
    "id": "my-proj",
    "title": "My Project",
    "url": "https://github.com/name/project.git",
    "branch": "main",
    "kind": "godot_projects",
    "license": "MIT",
}


class FakeJobs:
    """Records start() calls instead of launching real subprocesses."""

    def __init__(self) -> None:
        self.starts: list[dict] = []
        self._busy = False

    def stop(self) -> None:
        """Lifespan teardown calls this; nothing is running."""

    def start(self, kind, args, *, max_steps=None, extra_env=None):
        if self._busy:
            raise RuntimeError("another studio task is already running")
        self.starts.append(
            {
                "kind": kind,
                "args": list(args),
                "max_steps": max_steps,
                "extra_env": dict(extra_env or {}),
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
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "processed").mkdir(parents=True)
    (tmp_path / "data" / "local_sources" / "inbox").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")


def _make_app(tmp_path: Path, monkeypatch):
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    jobs = FakeJobs()
    gen = FakeGeneration()
    monkeypatch.setattr(app.state, "jobs", jobs)
    # The corpus handlers never unload the model themselves, but the TestClient
    # teardown does - keep the real GenerationService out of the test env.
    monkeypatch.setattr(app.state, "generation", gen)
    return app, jobs, gen


def _client(app):
    return TestClient(app)


# --- Corpus sources: create / delete / validate via PUT ----------------------


def test_corpus_sources_put_creates_registry(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.put("/api/corpus/sources", json={"sources": [GIT_SOURCE]})
    assert response.status_code == 200
    assert response.json()["sources"][0]["id"] == "my-proj"
    saved = json.loads((tmp_path / "data" / "corpus" / "sources.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in saved["sources"]] == ["my-proj"]


def test_corpus_sources_put_replace_deletes_removed(tmp_path: Path) -> None:
    """Re-PUTting with a smaller list deletes the dropped sources."""
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    second = {**GIT_SOURCE, "id": "second-proj", "url": "https://github.com/name/other.git"}
    with _client(app) as client:
        assert client.put("/api/corpus/sources", json={"sources": [GIT_SOURCE, second]}).status_code == 200
        response = client.put("/api/corpus/sources", json={"sources": [GIT_SOURCE]})
    assert response.status_code == 200
    assert [s["id"] for s in response.json()["sources"]] == ["my-proj"]
    saved = json.loads((tmp_path / "data" / "corpus" / "sources.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in saved["sources"]] == ["my-proj"]


def test_corpus_sources_put_duplicate_id_400(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.put(
            "/api/corpus/sources",
            json={"sources": [GIT_SOURCE, {**GIT_SOURCE, "url": "https://github.com/name/other.git"}]},
        )
    assert response.status_code == 400
    assert "duplicate" in response.json()["detail"]


def test_corpus_sources_put_unsupported_kind_400(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.put(
            "/api/corpus/sources",
            json={"sources": [{**GIT_SOURCE, "kind": "blog"}]},
        )
    assert response.status_code == 400
    assert "kind" in response.json()["detail"]


def test_corpus_sources_put_invalid_git_url_400(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.put(
            "/api/corpus/sources",
            json={"sources": [{**GIT_SOURCE, "url": "ftp://example.com/repo.git"}]},
        )
    assert response.status_code == 400
    assert "URL" in response.json()["detail"]


def test_corpus_sources_put_local_requires_ownership_400(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.put(
            "/api/corpus/sources",
            json={
                "sources": [
                    {
                        **GIT_SOURCE,
                        "url": "local://data/raw/seed_project",
                        "license": "LicenseRef-User-Owned-Private",
                    }
                ]
            },
        )
    assert response.status_code == 400
    assert "ownership" in response.json()["detail"]


def test_corpus_sources_put_local_with_ownership_ok(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    with _client(app) as client:
        response = client.put(
            "/api/corpus/sources",
            json={
                "sources": [
                    {
                        **GIT_SOURCE,
                        "url": "local://data/raw/seed_project",
                        "license": "LicenseRef-User-Owned-Private",
                        "owner_confirmed": True,
                    }
                ]
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["sources"][0]["url"].startswith("local://")


# --- Corpus jobs: launch contract via FakeJobs -------------------------------


def test_corpus_fetch_launches_job_with_enabled_count(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "godot_coder.ui.routers.corpus.load_registry",
        lambda root: {
            "format_version": 3,
            "sources": [
                {"id": "a", "enabled": True},
                {"id": "b", "enabled": True},
                {"id": "c", "enabled": False},
            ],
        },
    )
    with _client(app) as client:
        response = client.post("/api/jobs/corpus/fetch")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "fake-job"
    assert len(jobs.starts) == 1
    call = jobs.starts[0]
    assert call["kind"] == "corpus-download"
    assert call["args"] == ["-m", "godot_coder.corpus", "--root", str(tmp_path), "fetch"]
    assert call["max_steps"] == 2


def test_corpus_build_launches_job(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post("/api/jobs/corpus/build")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "fake-job"
    call = jobs.starts[0]
    assert call["kind"] == "corpus-scan"
    assert call["args"] == ["-m", "godot_coder.corpus", "--root", str(tmp_path), "build"]
    assert call["max_steps"] is None


def test_corpus_validate_launches_job_with_record_count(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "godot_coder.ui.routers.corpus.corpus_status",
        lambda root: {"manifest": {"records": [{}, {}, {}]}},
    )
    with _client(app) as client:
        response = client.post("/api/jobs/corpus/validate")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "fake-job"
    call = jobs.starts[0]
    assert call["kind"] == "corpus-validate"
    assert call["args"] == ["-m", "godot_coder.corpus", "--root", str(tmp_path), "validate"]
    assert call["max_steps"] == 3


def test_corpus_audit_launches_job(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post("/api/jobs/corpus/audit")
    assert response.status_code == 200, response.text
    call = jobs.starts[0]
    assert call["kind"] == "corpus-audit"
    assert call["args"] == ["-m", "godot_coder.corpus_audit", "--root", str(tmp_path), "audit"]


def test_corpus_tokenizer_launches_job_with_defaults(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post("/api/jobs/corpus/tokenizer", json={})
    assert response.status_code == 200, response.text
    call = jobs.starts[0]
    assert call["kind"] == "corpus-tokenizer"
    assert call["args"] == [
        "-m",
        "godot_coder.corpus",
        "--root",
        str(tmp_path),
        "train-bpe",
        "--vocab-size",
        "8192",
        "--min-frequency",
        "2",
    ]


def test_corpus_tokenizer_custom_values(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post(
            "/api/jobs/corpus/tokenizer",
            json={"vocab_size": 4096, "min_frequency": 5},
        )
    assert response.status_code == 200, response.text
    args = jobs.starts[0]["args"]
    assert args[args.index("--vocab-size") + 1] == "4096"
    assert args[args.index("--min-frequency") + 1] == "5"


def test_corpus_instructions_launches_job(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post("/api/jobs/corpus/instructions")
    assert response.status_code == 200, response.text
    call = jobs.starts[0]
    assert call["kind"] == "instruction-seed-build"
    assert call["args"] == ["-m", "godot_coder.instruction_data", "--root", str(tmp_path), "build"]


def test_corpus_prepare_launches_job(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post("/api/jobs/corpus/prepare")
    assert response.status_code == 200, response.text
    call = jobs.starts[0]
    assert call["kind"] == "corpus-prepare"
    assert call["args"] == [
        "-m",
        "godot_coder.prepare_data",
        "--input",
        str(tmp_path / "data" / "corpus" / "audited"),
        "--output",
        str(tmp_path / "data" / "processed" / "corpus_v06"),
        "--tokenizer",
        str(tmp_path / "artifacts" / "tokenizer_bpe_godot.json"),
    ]


def test_corpus_build_409_when_busy(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    jobs._busy = True
    with _client(app) as client:
        response = client.post("/api/jobs/corpus/build")
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]
    assert jobs.starts == []


def test_corpus_validate_409_when_busy(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    jobs._busy = True
    with _client(app) as client:
        response = client.post("/api/jobs/corpus/validate")
    assert response.status_code == 409
    assert jobs.starts == []


# --- Local sources -----------------------------------------------------------


def test_local_import_requires_ownership_400(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.post("/api/jobs/corpus/local-import", json={"confirm_owned": False})
    assert response.status_code == 400
    assert "confirm" in response.json()["detail"]
    assert jobs.starts == []


def test_local_import_launches_job(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "godot_coder.ui.routers.corpus.local_source_status",
        lambda root: {"inbox_items": [{"name": "a"}, {"name": "b"}]},
    )
    with _client(app) as client:
        response = client.post(
            "/api/jobs/corpus/local-import",
            json={"confirm_owned": True},
        )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "fake-job"
    call = jobs.starts[0]
    assert call["kind"] == "local-source-import"
    assert call["args"] == [
        "-m",
        "godot_coder.local_sources",
        "--root",
        str(tmp_path),
        "import",
        "--confirm-owned",
    ]
    # max_steps is the number of inbox items (2), extra_env empty by default.
    assert call["max_steps"] == 2
    assert call["extra_env"] == {}


def test_local_import_forwards_toggles(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "godot_coder.ui.routers.corpus.local_source_status",
        lambda root: {"inbox_items": []},
    )
    with _client(app) as client:
        response = client.post(
            "/api/jobs/corpus/local-import",
            json={
                "confirm_owned": True,
                "skip_project_import": True,
                "fast_static": True,
                "error_abort_threshold": 100,
            },
        )
    assert response.status_code == 200, response.text
    assert jobs.starts[0]["max_steps"] is None  # empty inbox -> no progress cap
    assert jobs.starts[0]["extra_env"] == {
        "GODOT_CODER_SKIP_PROJECT_IMPORT": "1",
        "GODOT_CODER_FAST_STATIC": "1",
        "GODOT_CODER_ERROR_ABORT_THRESHOLD": "100",
    }


def test_local_import_409_when_busy(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    jobs._busy = True
    with _client(app) as client:
        response = client.post(
            "/api/jobs/corpus/local-import",
            json={"confirm_owned": True},
        )
    assert response.status_code == 409
    assert jobs.starts == []


def test_local_open_opens_inbox(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    monkeypatch.setattr("godot_coder.ui.routers.corpus.open_local_inbox", lambda root: None)
    with _client(app) as client:
        response = client.post("/api/corpus/local/open")
    assert response.status_code == 200
    body = response.json()
    assert body["opened"] is True
    assert body["path"] == str(tmp_path / "data" / "local_sources" / "inbox")


def test_local_open_500_on_error(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)

    def boom(root):
        raise RuntimeError("no file manager available")

    monkeypatch.setattr("godot_coder.ui.routers.corpus.open_local_inbox", boom)
    with _client(app) as client:
        response = client.post("/api/corpus/local/open")
    assert response.status_code == 500
    assert "no file manager" in response.json()["detail"]


# --- Status readouts (real helpers on a fresh project) -----------------------


def test_corpus_status_200(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.get("/api/corpus/status")
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


def test_corpus_local_status_200(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.get("/api/corpus/local")
    assert response.status_code == 200
    body = response.json()
    assert body["inbox_items"] == []
    assert "inbox" in body


def test_corpus_scale_plan_200(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.get("/api/corpus/scale-plan")
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


def test_corpus_fetch_400_on_invalid_registry(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)

    def bad_registry(root):
        raise ValueError("unsupported corpus source registry version")

    monkeypatch.setattr("godot_coder.ui.routers.corpus.load_registry", bad_registry)
    with _client(app) as client:
        response = client.post("/api/jobs/corpus/fetch")
    assert response.status_code == 400
    assert "registry version" in response.json()["detail"]
    assert jobs.starts == []


def test_corpus_tokenizer_409_when_busy(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    jobs._busy = True
    with _client(app) as client:
        response = client.post("/api/jobs/corpus/tokenizer", json={})
    assert response.status_code == 409
    assert jobs.starts == []


def test_preflight_with_config_param(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    (tmp_path / "configs" / "night.yaml").write_text("profile:\n  id: test\n", encoding="utf-8")
    monkeypatch.setattr(
        "godot_coder.ui.routers.corpus.build_preflight",
        lambda *a, **kw: {"can_start": True, "blockers": [], "warnings": []},
    )
    with _client(app) as client:
        response = client.get(
            "/api/preflight",
            params={"config": "configs/night.yaml", "mode": "smoke"},
        )
    assert response.status_code == 200
    assert response.json()["can_start"] is True


def test_preflight_rejects_config_outside_configs_400(tmp_path: Path, monkeypatch) -> None:
    app, jobs, gen = _make_app(tmp_path, monkeypatch)
    with _client(app) as client:
        response = client.get(
            "/api/preflight",
            params={"config": "../pyproject.toml", "mode": "smoke"},
        )
    assert response.status_code == 400
