from pathlib import Path

from godot_coder.godot_cli import build_check_command


def test_godot_check_is_headless_and_forces_xr_off() -> None:
    command = build_check_command(
        "godot.exe",
        Path("demo/project"),
        Path("generated/check.gd"),
    )

    assert command[:5] == [
        "godot.exe",
        "--headless",
        "--xr-mode",
        "off",
        "--path",
    ]
    assert Path(command[5]) == Path("demo/project")
    assert command[6] == "--script"
    assert Path(command[7]) == Path("generated/check.gd")
    assert command[8] == "--check-only"


def test_project_validation_does_not_execute_regular_script() -> None:
    from godot_coder.godot_cli import build_project_validation_command

    command = build_project_validation_command("godot.exe", Path("demo/project"))
    assert command[-1] == "--import"
    assert "--script" not in command
    assert "--check-only" not in command


def test_generated_project_checker_runs_as_the_only_script() -> None:
    from godot_coder.godot_cli import build_project_script_command

    command = build_project_script_command("godot.exe", Path("demo/project"), Path("reports/check.gd"))
    assert command[-2:] == ["--script", str(Path("reports/check.gd"))]
    assert "--check-only" not in command
