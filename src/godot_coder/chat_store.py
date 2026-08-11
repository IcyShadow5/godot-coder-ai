"""Persistent chat sessions for the Studio.

The chat feed used to live only in the browser DOM: a refresh or a new
browser lost every conversation. Sessions now persist as JSONL under
reports/chat/<session_id>.jsonl - one message per line, appended with a
process-local lock so a slow write never interleaves with another one -
and the UI can restore the last conversation and switch between them
like a real chat product.

The Studio keeps only the most recent sessions (rotation by last write)
so an old experiment chat cannot grow the folder without bound.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")
_TITLE_LIMIT = 48
_DEFAULT_SESSION_LIMIT = 20


class ChatStore:
    """JSONL-backed chat history under <root>/reports/chat."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        session_limit: int = _DEFAULT_SESSION_LIMIT,
    ) -> None:
        self.root = Path(project_root) / "reports" / "chat"
        self.root.mkdir(parents=True, exist_ok=True)
        self.session_limit = session_limit
        self._lock = threading.Lock()

    @staticmethod
    def _safe_id(session_id: str) -> str:
        """Reject anything that could escape the chat folder or look odd."""
        if not _SESSION_ID_RE.match(session_id):
            raise ValueError("invalid session id")
        return session_id

    def _path(self, session_id: str) -> Path:
        return self.root / f"{self._safe_id(session_id)}.jsonl"

    def append(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        checkpoint: str | None = None,
        sampling: dict[str, object] | None = None,
        context: dict[str, object] | None = None,
        validation: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Append one message to the session, creating the file on first use.

        The session id is created lazily: an empty session never touches the
        disk, it only materializes once the first real message arrives.
        """
        record: dict[str, object] = {
            "role": role,
            "content": content,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if checkpoint:
            record["checkpoint"] = checkpoint
        if sampling:
            record["sampling"] = sampling
        if context:
            record["context"] = context
        if validation:
            record["validation"] = validation
        path = self._path(session_id)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
            self._rotate_locked()
        return record

    def _rotate_locked(self) -> None:
        """Drop the oldest sessions beyond the limit (oldest last-write first)."""
        sessions = self.list_sessions()
        if len(sessions) <= self.session_limit:
            return
        overflow = sorted(sessions, key=lambda item: item["updated_at"])[: len(sessions) - self.session_limit]
        for item in overflow:
            self._path(item["id"]).unlink(missing_ok=True)

    def list_sessions(self) -> list[dict[str, object]]:
        """Metadata for every session, newest last-write first."""
        result: list[dict[str, object]] = []
        for path in sorted(self.root.glob("*.jsonl")):
            try:
                lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            except OSError:
                continue
            if not lines:
                path.unlink(missing_ok=True)
                continue
            try:
                first = json.loads(lines[0])
                last = json.loads(lines[-1])
            except ValueError:
                continue
            title = ""
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("role") == "user" and record.get("content"):
                    title = record["content"].replace("\n", " ").strip()[:_TITLE_LIMIT]
                    break
            result.append(
                {
                    "id": path.stem,
                    "title": title or "New conversation",
                    "created_at": str(first.get("ts") or ""),
                    "updated_at": str(last.get("ts") or ""),
                    "message_count": len(lines),
                }
            )
        result.sort(key=lambda item: str(item["updated_at"]))
        return result

    def load(self, session_id: str) -> list[dict[str, object]]:
        """All messages of one session in order, or [] for an unknown id."""
        path = self._path(session_id)
        if not path.exists():
            return []
        records: list[dict[str, object]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return records

    def attach_validation(self, session_id: str, validation: dict[str, object]) -> None:
        """Attach a Godot-check result to the last assistant message.

        JSONL is append-only, so this rewrites the file with the one
        enriched line. Cheap here - sessions are small text files - and
        keeps the check result with the completion that produced it.
        """
        path = self._path(session_id)
        if not path.exists():
            return
        with self._lock:
            lines = path.read_text(encoding="utf-8").splitlines()
            for index in range(len(lines) - 1, -1, -1):
                try:
                    record = json.loads(lines[index])
                except ValueError:
                    continue
                if record.get("role") == "assistant":
                    record["validation"] = validation
                    lines[index] = json.dumps(record, ensure_ascii=False)
                    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
                    return

    def delete(self, session_id: str) -> bool:
        """Remove one session; returns True if a file was deleted."""
        with self._lock:
            path = self._path(session_id)
            existed = path.exists()
            path.unlink(missing_ok=True)
            return existed
