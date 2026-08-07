from __future__ import annotations

from pathlib import Path

PROJECT_MARKERS = ("pyproject.toml", "configs", "src")


def looks_like_project(path: Path) -> bool:
    return path.is_dir() and all((path / marker).exists() for marker in PROJECT_MARKERS)


def find_project_root(
    explicit: str | Path | None = None,
    *,
    start: str | Path | None = None,
) -> Path:
    """Locate a Godot Coder checkout without depending on the current shell directory."""
    if explicit is not None:
        candidate = Path(explicit).expanduser().resolve()
        if looks_like_project(candidate):
            return candidate
        raise FileNotFoundError(f"Godot Coder AI project root not found: {candidate}")

    starting_points = [Path(start).resolve()] if start is not None else []
    starting_points.append(Path.cwd().resolve())
    starting_points.append(Path(__file__).resolve())
    seen: set[Path] = set()
    for point in starting_points:
        candidates = [point] if point.is_dir() else [point.parent]
        candidates.extend(candidates[0].parents)
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if looks_like_project(candidate):
                return candidate
    raise FileNotFoundError(
        "Could not locate the Godot Coder AI project root. Start in the folder "
        "containing pyproject.toml, or pass an explicit root."
    )


def resolve_project_path(project_root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def project_relative(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)
