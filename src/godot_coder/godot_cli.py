from __future__ import annotations
"""Build Godot CLI command lines for --check, --import, and --script runs."""

from pathlib import Path


def build_check_command(
    executable: str | Path,
    project: str | Path,
    script: str | Path,
) -> list[str]:
    """Build a non-interactive Godot GDScript parser command.

    XR is forced off because corpus demo projects may enable OpenXR in their
    project settings. A parser-only validation must never initialize an HMD
    runtime or display an OpenXR startup alert.
    """

    return [
        str(executable),
        "--headless",
        "--xr-mode",
        "off",
        "--path",
        str(project),
        "--script",
        str(script),
        "--check-only",
    ]


def build_project_validation_command(
    executable: str | Path,
    project: str | Path,
) -> list[str]:
    """Build one editor import command for a complete Godot project.

    Unlike ``--script --check-only``, this keeps every ordinary Node/Resource
    script in its real project context and does not require it to inherit from
    SceneTree or MainLoop. ``--import`` starts the editor headlessly, waits for
    resources and scripts to be scanned, and quits automatically.
    """

    return [
        str(executable),
        "--headless",
        "--xr-mode",
        "off",
        "--disable-crash-handler",
        "--path",
        str(project),
        "--import",
    ]


def build_project_script_command(
    executable: str | Path,
    project: str | Path,
    checker: str | Path,
) -> list[str]:
    """Run a generated SceneTree checker inside a project's real context."""

    return [
        str(executable),
        "--headless",
        "--xr-mode",
        "off",
        "--disable-crash-handler",
        "--path",
        str(project),
        "--script",
        str(checker),
    ]
