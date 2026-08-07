from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from .godot_cli import build_check_command


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
    result = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout, check=False)
    combined = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if combined:
        print(combined)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    print("Godot parser check passed.")


if __name__ == "__main__":
    main()
