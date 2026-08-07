from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import warnings
from pathlib import Path

from .godot_cli import build_check_command


def find_godot() -> str | None:
    for candidate in ("godot", "godot4", "godot.exe", "godot.CMD"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def validate_dataset(input_dir: str | Path, *, godot: str | None = None, timeout: float = 30.0) -> dict[str, object]:
    root = Path(input_dir).resolve()
    project_file = root / "project.godot"
    if not project_file.exists():
        raise FileNotFoundError(f"project.godot not found under {root}")
    executable = godot or find_godot()
    if not executable:
        raise FileNotFoundError("Godot executable was not found")

    results: list[dict[str, object]] = []
    for script in sorted(root.rglob("*.gd"), key=lambda path: path.as_posix().lower()):
        command = build_check_command(executable, root, script)
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
            output = "\n".join(part for part in (process.stdout.strip(), process.stderr.strip()) if part)
            passed = process.returncode == 0
            return_code = process.returncode
        except subprocess.TimeoutExpired:
            output = f"Timeout after {timeout}s"
            passed = False
            return_code = -1
            warnings.warn(f"Godot timed out on {script.relative_to(root).as_posix()}")
        except Exception as exc:
            output = f"{type(exc).__name__}: {exc}"
            passed = False
            return_code = -1
            warnings.warn(f"Godot crashed on {script.relative_to(root).as_posix()}: {exc}")
        results.append(
            {
                "path": script.relative_to(root).as_posix(),
                "passed": passed,
                "return_code": return_code,
                "output": output,
            }
        )
        print(f"[{len(results):03d}] {'PASS' if passed else 'FAIL'} {script.relative_to(root).as_posix()}")

    passed = sum(1 for item in results if item["passed"])
    report: dict[str, object] = {
        "format": "godot-coder-dataset-validation",
        "input": str(root),
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": passed / len(results) if results else 0.0,
        "results": results,
    }
    (root / "validation_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate all GDScript lessons with Godot headless mode.")
    parser.add_argument("--input", default="data/raw/curriculum_v03")
    parser.add_argument("--godot", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = validate_dataset(args.input, godot=args.godot)
    print(json.dumps({key: report[key] for key in ("total", "passed", "failed", "pass_rate")}, indent=2))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
