"""Tests for chat_store.py — the JSONL-backed chat history."""

from __future__ import annotations

from pathlib import Path

import pytest

from godot_coder.chat_store import ChatStore


def _store(tmp_path: Path, **kwargs) -> ChatStore:
    return ChatStore(tmp_path, **kwargs)


def test_append_creates_lazy_file_on_first_message(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = "a" * 12
    assert (tmp_path / "reports" / "chat" / f"{session}.jsonl").exists() is False
    store.append(session, "user", "extends Node")
    assert (tmp_path / "reports" / "chat" / f"{session}.jsonl").exists() is True


def test_load_round_trip_preserves_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = "b" * 12
    store.append(
        session,
        "user",
        "extends Node",
        checkpoint="checkpoints/v06/best.pt",
        sampling={"temperature": 0.7, "max_new_tokens": 64},
    )
    store.append(
        session,
        "assistant",
        "pass",
        context={"prompt_tokens": 12, "truncated": False, "parts": []},
    )
    messages = store.load(session)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["checkpoint"] == "checkpoints/v06/best.pt"
    assert messages[0]["sampling"]["temperature"] == 0.7
    assert messages[1]["context"]["prompt_tokens"] == 12
    assert "ts" in messages[0]


def test_load_unknown_session_returns_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.load("c" * 12) == []


def test_list_sessions_orders_by_last_write_and_titles(tmp_path: Path) -> None:
    store = _store(tmp_path)
    older = "d" * 12
    newer = "e" * 12
    store.append(older, "user", "older conversation")
    store.append(newer, "user", "newer conversation")
    sessions = store.list_sessions()
    assert [item["id"] for item in sessions] == [older, newer]
    assert sessions[0]["title"] == "older conversation"
    assert sessions[1]["title"] == "newer conversation"
    assert sessions[1]["message_count"] == 1


def test_list_sessions_drops_empty_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    empty = "f" * 12
    (tmp_path / "reports" / "chat" / f"{empty}.jsonl").write_text("", encoding="utf-8")
    store.append("g" * 12, "user", "real")
    assert all(item["id"] != empty for item in store.list_sessions())


def test_rotation_keeps_newest_sessions(tmp_path: Path) -> None:
    store = _store(tmp_path, session_limit=3)
    for index in range(5):
        # Distinct prefix per index so the surviving ids are unambiguous.
        store.append(f"h{index}xxxxxxxxxx", "user", f"conversation {index}")
    sessions = store.list_sessions()
    assert len(sessions) == 3
    # The oldest two are gone; the newest three survive.
    assert sorted(item["id"] for item in sessions) == [f"h{i}xxxxxxxxxx" for i in (2, 3, 4)]


def test_delete_removes_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = "i" * 12
    store.append(session, "user", "hello")
    assert store.delete(session) is True
    assert store.load(session) == []
    assert store.delete(session) is False


def test_attach_validation_enriches_last_assistant_message(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = "j" * 12
    store.append(session, "user", "extends Node")
    store.append(session, "assistant", "pass")
    store.append(session, "user", "another prompt")
    store.attach_validation(session, {"passed": True, "timed_out": False})
    messages = store.load(session)
    assert "validation" not in messages[0]
    assert messages[1]["validation"] == {"passed": True, "timed_out": False}
    assert "validation" not in messages[2]


def test_append_persists_cancelled_flag(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append("c" * 12, "assistant", "partial text", cancelled=True)
    loaded = store.load("c" * 12)
    assert loaded[0]["cancelled"] is True
    # The flag is optional: a normal completion has no cancelled key.
    store.append("c" * 12, "assistant", "full text")
    assert "cancelled" not in store.load("c" * 12)[1]


def test_attach_validation_unknown_session_is_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.attach_validation("k" * 12, {"passed": True})
    # No exception, no file.


def test_invalid_session_ids_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.load("../../etc/passwd")
    with pytest.raises(ValueError):
        store.append("..", "user", "x")
    with pytest.raises(ValueError):
        store.delete("a/b")
