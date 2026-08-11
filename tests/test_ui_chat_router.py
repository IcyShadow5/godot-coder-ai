"""Tests for the chat history endpoints and turn persistence in chat.py.

Follows the project convention: a recording FakeJobs/fake generation, no
real subprocess, no real model.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from godot_coder.ui.server import create_app


def _scaffold(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")


def _make_app(tmp_path: Path):
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    # No active job so the 409 guard does not fire.
    app.state.jobs.current = lambda: None  # type: ignore[method-assign]
    return app


def _fake_generation(app, events):
    def fake_generate_stream(checkpoint, prompt, **kwargs):
        yield from events

    # SimpleNamespace (not type()) so unload/generate stay plain functions
    # and are never bound as methods - same pattern as test_ui_server_stream.
    app.state.generation = SimpleNamespace(
        generate_stream=fake_generate_stream,
        unload=lambda: None,
        generate=lambda *a, **k: "dummy",
    )
    return app


def test_sessions_list_empty_then_after_turn(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _fake_generation(app, [{"token": "pass"}, {"done": True, "text": "pass", "tokens": 1}])
    with TestClient(app) as client:
        assert client.get("/api/chat/sessions").json()["sessions"] == []
        response = client.post(
            "/api/chat/generate-stream",
            json={
                "checkpoint": "checkpoints/x.pt",
                "prompt": "extends Node",
                "max_new_tokens": 4,
                "session_id": "a" * 12,
            },
        )
        assert response.status_code == 200
        sessions = client.get("/api/chat/sessions").json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["id"] == "a" * 12
        assert sessions[0]["title"] == "extends Node"
        assert sessions[0]["message_count"] == 2


def test_generate_stream_persists_user_and_assistant_turns(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _fake_generation(
        app,
        [
            {"context": {"prompt_tokens": 10, "truncated": False, "kv_cache_possible": True, "parts": []}},
            {"token": "return "},
            {"token": "true"},
            {"done": True, "text": "return true", "tokens": 2},
        ],
    )
    with TestClient(app) as client:
        client.post(
            "/api/chat/generate-stream",
            json={
                "checkpoint": "checkpoints/x.pt",
                "prompt": "func check():",
                "max_new_tokens": 8,
                "session_id": "b" * 12,
            },
        )
    messages = client.get(f"/api/chat/sessions/{'b' * 12}").json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "func check():"
    assert messages[0]["checkpoint"] == "checkpoints/x.pt"
    assert messages[1]["content"] == "return true"  # cleaned completion wins
    assert messages[1]["context"]["prompt_tokens"] == 10
    assert messages[1]["sampling"]["max_new_tokens"] == 8


def test_generate_stream_persists_cancelled_turn(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _fake_generation(
        app,
        [
            {"token": "partial"},
            {"done": True, "text": "partial", "tokens": 1, "cancelled": True},
        ],
    )
    with TestClient(app) as client:
        client.post(
            "/api/chat/generate-stream",
            json={
                "checkpoint": "checkpoints/x.pt",
                "prompt": "extends Node",
                "max_new_tokens": 4,
                "session_id": "c" * 12,
            },
        )
    messages = client.get(f"/api/chat/sessions/{'c' * 12}").json()["messages"]
    assert messages[1]["content"] == "partial"
    assert messages[1]["cancelled"] is True


def test_generate_stream_persists_cancelled_flag_even_without_text(tmp_path: Path) -> None:
    # A stream cancelled before any token emits done with text="" - the
    # cancelled flag must still be persisted, not swallowed by the empty
    # text. Otherwise history shows a plain empty assistant turn.
    app = _make_app(tmp_path)
    _fake_generation(
        app,
        [
            {"done": True, "text": "", "tokens": 0, "cancelled": True},
        ],
    )
    with TestClient(app) as client:
        client.post(
            "/api/chat/generate-stream",
            json={
                "checkpoint": "checkpoints/x.pt",
                "prompt": "extends Node",
                "max_new_tokens": 4,
                "session_id": "d" * 12,
            },
        )
    messages = client.get(f"/api/chat/sessions/{'d' * 12}").json()["messages"]
    assert messages[1]["cancelled"] is True


def test_generate_stream_persists_partial_turn_on_error(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _fake_generation(
        app,
        [
            {"token": "partial"},
            {"error": "checkpoint and tokenizer do not match"},
        ],
    )
    with TestClient(app) as client:
        client.post(
            "/api/chat/generate-stream",
            json={
                "checkpoint": "checkpoints/x.pt",
                "prompt": "func f():",
                "max_new_tokens": 4,
                "session_id": "c" * 12,
            },
        )
    messages = client.get(f"/api/chat/sessions/{'c' * 12}").json()["messages"]
    assert len(messages) == 2
    assert messages[1]["content"] == "partial"


def test_generate_stream_without_session_id_does_not_persist(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _fake_generation(app, [{"token": "x"}, {"done": True, "text": "x", "tokens": 1}])
    with TestClient(app) as client:
        client.post(
            "/api/chat/generate-stream",
            json={"checkpoint": "checkpoints/x.pt", "prompt": "extends Node", "max_new_tokens": 4},
        )
    assert client.get("/api/chat/sessions").json()["sessions"] == []


def test_stop_endpoint_calls_unload(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    calls: list[str] = []

    def record_unload() -> None:
        calls.append("unload")

    _fake_generation(app, [])
    app.state.generation.unload = record_unload  # type: ignore[attr-defined]
    client = TestClient(app)
    resp = client.post("/api/chat/stop")
    assert resp.status_code == 200
    assert resp.json() == {"stopped": True}
    assert calls == ["unload"]


def test_validate_attaches_result_to_last_assistant(tmp_path: Path, monkeypatch) -> None:
    app = _make_app(tmp_path)
    _fake_generation(app, [{"token": "pass"}, {"done": True, "text": "pass", "tokens": 1}])
    # The real validate_code spawns Godot; stub it at the router import site.

    def fake_validate_code(root, code, project):
        return {"passed": True, "return_code": 0, "output": "ok", "timed_out": False}

    monkeypatch.setattr("godot_coder.ui.routers.chat.validate_code", fake_validate_code)
    with TestClient(app) as client:
        client.post(
            "/api/chat/generate-stream",
            json={
                "checkpoint": "checkpoints/x.pt",
                "prompt": "extends Node",
                "max_new_tokens": 4,
                "session_id": "d" * 12,
            },
        )
        response = client.post(
            "/api/chat/validate",
            json={"code": "pass", "session_id": "d" * 12},
        )
        assert response.status_code == 200
        assert response.json()["passed"] is True
    messages = client.get(f"/api/chat/sessions/{'d' * 12}").json()["messages"]
    assert messages[1]["validation"] == {"passed": True, "timed_out": False}


def test_delete_session_removes_it(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _fake_generation(app, [{"token": "x"}, {"done": True, "text": "x", "tokens": 1}])
    with TestClient(app) as client:
        client.post(
            "/api/chat/generate-stream",
            json={
                "checkpoint": "checkpoints/x.pt",
                "prompt": "extends Node",
                "max_new_tokens": 4,
                "session_id": "e" * 12,
            },
        )
        assert client.delete(f"/api/chat/sessions/{'e' * 12}").json()["deleted"] is True
        assert client.get("/api/chat/sessions").json()["sessions"] == []


def test_sessions_endpoints_reject_invalid_ids(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        # Unknown-but-valid ids are fine (empty), malformed ids are rejected.
        assert client.get("/api/chat/sessions/..%2F..%2Fetc").status_code != 200
