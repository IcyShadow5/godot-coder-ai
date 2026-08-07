from __future__ import annotations

from pathlib import Path

from ..project import PROJECT_MARKERS, find_project_root, looks_like_project


def safe_child(root: Path, relative: str | Path, *, must_exist: bool = False) -> Path:
    """Resolve a user-supplied relative path without allowing directory escape."""
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise ValueError("absolute paths are not allowed")
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("path escapes the allowed project area") from exc
    if must_exist and not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate


# Backward-compatible private alias used by older imports/tests.
_looks_like_project = looks_like_project
