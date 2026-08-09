from __future__ import annotations

import argparse
import json
import os
import shutil
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .godot_cli import build_check_command
# Same per-file timeout as the import pipeline (GODOT_CODER_PARSER_FILE_TIMEOUT_SECONDS).
# Both paths can hang on the same broken scenes, so they should agree — and one
# copy of the helper is enough.
from .local_sources import _parser_file_timeout_seconds
from .process_control import run_managed_process


@dataclass
class PerFileResult:
    """Outcome of a single Godot parser check on one GDScript file."""
    path: str
    passed: bool
    return_code: int | None
    output: str


@dataclass
class ValidationReport:
    """Structured report for a validate_dataset run."""
    format: str = "godot-coder-dataset-validation"
    input: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    results: list[PerFileResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "input": self.input,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.pass_rate,
            "results": [
                {
                    "path": r.path,
                    "passed": r.passed,
                    "return_code": r.return_code,
                    "output": r.output,
                }
                for r in self.results
            ],
        }


def find_godot() -> str | None:
    for candidate in ("godot", "godot4", "godot.exe", "godot.CMD"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def validate_dataset(
    input_dir: str | Path,
    *,
    godot: str | None = None,
    timeout: float | None = None,
) -> ValidationReport:
    root = Path(input_dir).resolve()
    project_file = root / "project.godot"
    if not project_file.exists():
        raise FileNotFoundError(f"project.godot not found under {root}")
    executable = godot or find_godot()
    if not executable:
        raise FileNotFoundError("Godot executable was not found")

    per_file_timeout = timeout if timeout is not None else _parser_file_timeout_seconds()
    results: list[PerFileResult] = []
    for script in sorted(root.rglob("*.gd"), key=lambda path: path.as_posix().lower()):
        command = build_check_command(executable, root, script)
        try:
            # Managed process runner: a timed-out Godot check must never leave
            # an orphaned process tree behind on Windows. The job object kills
            # the whole tree, Mono grandchildren included.
            result = run_managed_process(
                command,
                cwd=root,
                env=os.environ.copy(),
                timeout_seconds=per_file_timeout,
                idle_timeout_seconds=min(per_file_timeout, 10.0),
            )
            output = result.output.strip()
            if result.startup_error:
                output = output or f"Godot could not be started: {result.startup_error}"
                passed = False
                return_code: int | None = -1
                warnings.warn(
                    f"Godot failed to start on {script.relative_to(root).as_posix()}: {result.startup_error}"
                )
            elif result.timed_out:
                output = output or f"Timeout after {per_file_timeout:.0f}s"
                passed = False
                return_code = -1
                warnings.warn(f"Godot timed out on {script.relative_to(root).as_posix()}")
            else:
                passed = result.return_code == 0
                return_code = result.return_code
        except Exception as exc:
            output = f"{type(exc).__name__}: {exc}"
            passed = False
            return_code = -1
            warnings.warn(f"Godot crashed on {script.relative_to(root).as_posix()}: {exc}")
        results.append(
            PerFileResult(
                path=script.relative_to(root).as_posix(),
                passed=passed,
                return_code=return_code,
                output=output,
            )
        )
        print(f"[{len(results):03d}] {'PASS' if passed else 'FAIL'} {script.relative_to(root).as_posix()}")

    total_passed = sum(1 for r in results if r.passed)
    report = ValidationReport(
        input=str(root),
        total=len(results),
        passed=total_passed,
        failed=len(results) - total_passed,
        pass_rate=total_passed / len(results) if results else 0.0,
        results=results,
    )
    (root / "validation_report.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate all GDScript lessons with Godot headless mode.")
    parser.add_argument("--input", default="data/raw/curriculum_v03")
    parser.add_argument("--godot", default=None)
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Per-file timeout in seconds (default: GODOT_CODER_PARSER_FILE_TIMEOUT_SECONDS or 10).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = validate_dataset(args.input, godot=args.godot, timeout=args.timeout)
    print(json.dumps({"total": report.total, "passed": report.passed, "failed": report.failed, "pass_rate": report.pass_rate}, indent=2))
    if report.failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
