from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from .godot_cli import build_check_command
from .process_control import run_managed_process


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask Godot to parse a generated GDScript file.")
    parser.add_argument("--script", required=True)
    parser.add_argument("--project", required=True, help="Directory containing project.godot")
    parser.add_argument("--godot", default="godot", help="Godot executable name or full path")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    executable = shutil.which(args.godot) or (args.godot if Path(args.godot).exists() else None)
    if executable is None:
        raise FileNotFoundError(f"Godot executable not found: {args.godot}")
    project = Path(args.project).resolve()
    script = Path(args.script).resolve()
    if not (project / "project.godot").exists():
        raise FileNotFoundError(f"project.godot not found under {project}")
    if not script.exists():
        raise FileNotFoundError(script)

    command = build_check_command(executable, project, script)
    # run_managed_process (not subprocess.run) so a hung Mono-Godot child
    # is killed as a tree, the Windows Job Object guarantees no orphans
    # survive a parent crash, and idle/stuck Godot instances are detected.
    result = run_managed_process(
        command,
        cwd=project,
        env=os.environ.copy(),
        timeout_seconds=args.timeout,
        idle_timeout_seconds=min(args.timeout, 10.0),
    )
    combined = result.output.strip()
    if result.startup_error:
        print(f"Godot could not be started: {result.startup_error}")
        raise SystemExit(1)
    if result.timed_out:
        print(combined or f"Timeout after {args.timeout}s")
        raise SystemExit(1)
    if combined:
        print(combined)
    if result.return_code != 0:
        raise SystemExit(result.return_code)
    print("Godot parser check passed.")


if __name__ == "__main__":
    main()
