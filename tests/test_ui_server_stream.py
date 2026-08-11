"""Tests for the chat streaming endpoint and the system overview payload."""

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from godot_coder import __version__
from godot_coder.ui.server import create_app


def _scaffold(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")


def test_chat_generate_stream_emits_sse_tokens_then_done(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)

    def fake_generate_stream(checkpoint, prompt, *, max_new_tokens, temperature, top_k, top_p=1.0, repetition_penalty=1.15, device_name="auto", task_format=False, strict_context=False):
        # Real token streaming: the service yields live deltas, then the
        # cleaned completion in a done event.
        yield {"token": "return "}
        yield {"token": "a + b"}
        yield {"done": True, "text": "return a + b", "tokens": 2}

    def fake_unload():
        pass

    app.state.generation = SimpleNamespace(generate_stream=fake_generate_stream, unload=fake_unload)
    with TestClient(app) as client:
        response = client.post(
            "/api/chat/generate-stream",
            json={
                "checkpoint": "checkpoints/v06_balanced/best.pt",
                "prompt": "func add(a: int, b: int) -> int:\n",
                "max_new_tokens": 8,
                "temperature": 0.0,
            },
        )
    assert response.status_code == 200
    assert 'data: {"token": "return "}' in response.text
    assert 'data: {"token": "a + b"}' in response.text
    assert '"done": true' in response.text
    assert response.text.strip().endswith("data: [DONE]")


def test_chat_generate_stream_forwards_generation_errors(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)

    def failing_stream(checkpoint, prompt, **kwargs):
        yield {"error": "checkpoint and tokenizer do not match"}

    app.state.generation = SimpleNamespace(generate_stream=failing_stream, unload=lambda: None)
    with TestClient(app) as client:
        response = client.post(
            "/api/chat/generate-stream",
            json={"checkpoint": "checkpoints/x.pt", "prompt": "func f():\n", "max_new_tokens": 4},
        )
    assert response.status_code == 200  # SSE: the error travels inside the stream
    assert "checkpoint and tokenizer do not match" in response.text
    assert response.text.strip().endswith("data: [DONE]")


def test_chat_generate_stream_rejects_while_job_running(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    app = create_app(tmp_path)

    def fake_current():
        return {"kind": "training", "status": "running"}

    app.state.jobs.current = fake_current  # type: ignore[method-assign]
    with TestClient(app) as client:
        response = client.post(
            "/api/chat/generate-stream",
            json={
                "checkpoint": "checkpoints/x.pt",
                "prompt": "func f():\n",
                "max_new_tokens": 4,
            },
        )
    assert response.status_code == 409


def test_overview_reports_environment_fields(tmp_path: Path, monkeypatch) -> None:
    _scaffold(tmp_path)
    props = SimpleNamespace(total_memory=8 * 1024**3)
    monkeypatch.setattr("godot_coder.ui.services.torch.cuda.is_available", lambda: True)
    monkeypatch.setattr("godot_coder.ui.services.torch.cuda.get_device_name", lambda index: "Fake GPU")
    monkeypatch.setattr("godot_coder.ui.services.torch.cuda.get_device_capability", lambda index: (8, 9))
    monkeypatch.setattr("godot_coder.ui.services.torch.cuda.get_device_properties", lambda index: props)
    with TestClient(create_app(tmp_path)) as client:
        overview = client.get("/api/overview")
    payload = overview.json()
    assert payload["app_version"] == __version__
    assert payload["cuda_available"] is True
    assert payload["rocm_available"] is False
    assert payload["gpu"]["name"] == "Fake GPU"
    assert payload["gpu"]["compute_capability"] == "8.9"
    assert payload["gpu"]["vram_gib"] == 8.0
    assert "torch" in payload
    assert "godot_version" in payload



def test_overview_reports_compile_available_with_triton(tmp_path: Path, monkeypatch) -> None:
    _scaffold(tmp_path)
    monkeypatch.setitem(sys.modules, "triton", SimpleNamespace(__version__="3.7.1"))
    with TestClient(create_app(tmp_path)) as client:
        payload = client.get("/api/overview").json()
    assert payload["compile_available"] is True
    assert payload["triton"] == "3.7.1"


def test_overview_reports_compile_unavailable_without_triton(tmp_path: Path, monkeypatch) -> None:
    _scaffold(tmp_path)
    # sys.modules["triton"] = None makes `import triton` raise ImportError.
    monkeypatch.setitem(sys.modules, "triton", None)
    with TestClient(create_app(tmp_path)) as client:
        payload = client.get("/api/overview").json()
    assert payload["compile_available"] is False
    assert payload["triton"] is None


def test_chat_generate_stream_passes_context_and_flags(tmp_path: Path) -> None:
    """The context report travels as the first SSE event and the task-format
    flags reach the service."""
    _scaffold(tmp_path)
    app = create_app(tmp_path)
    received: dict = {}

    def fake_generate_stream(checkpoint, prompt, **kwargs):
        received.update(kwargs)
        yield {"context": {"prompt_tokens": 12, "parts": []}}
        yield {"token": "x"}
        yield {"done": True, "text": "x", "tokens": 1}

    app.state.generation = SimpleNamespace(generate_stream=fake_generate_stream, unload=lambda: None)
    with TestClient(app) as client:
        response = client.post(
            "/api/chat/generate-stream",
            json={
                "checkpoint": "checkpoints/v06/best.pt",
                "prompt": "func f():\n",
                "max_new_tokens": 4,
                "task_format": True,
                "strict_context": True,
            },
        )
    assert response.status_code == 200
    assert '"context"' in response.text
    assert '"prompt_tokens": 12' in response.text
    assert received.get("task_format") is True
    assert received.get("strict_context") is True
