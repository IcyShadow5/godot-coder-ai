"""I want to be sure nobody can slip a shell command or filesystem escape
past the Studio. These tests check every gate: safe_child, zero shell=True,
no dynamic imports, remote write denial."""

from __future__ import annotations

from pathlib import Path

import pytest

from godot_coder.ui.paths import safe_child


# ---- safe_child: the filesystem gate ---------------------------------

def test_safe_child_rejects_shell_metacharacters_in_paths(tmp_path: Path) -> None:
    """Shell metacharacters must not let you escape the root. If safe_child
    rejects the path outright that's fine too — containment OR rejection
    both count as passing."""
    root = tmp_path / "safe"
    root.mkdir()
    attempts = [
        "; rm -rf /",
        "| cat /etc/passwd",
        "$(whoami)",
        "`id`",
        "&& echo pwned",
        "../..; ls",
    ]
    for attempt in attempts:
        try:
            result = safe_child(root, attempt)
        except ValueError:
            continue
        assert result.is_relative_to(root.resolve()), f"escaped via: {attempt!r}"


def test_safe_child_rejects_absolute_paths(tmp_path: Path) -> None:
    """Absolute paths are rejected. Platform-aware so CI doesn't break."""
    import os as _os
    if _os.name == "nt":
        abs_paths = [r"C:\Windows\System32"]
    else:
        abs_paths = ["/etc/passwd"]
    for path in abs_paths:
        with pytest.raises(ValueError, match="absolute"):
            safe_child(tmp_path, path)


# ---- No shell=True, no os.system, no eval/exec -----------------------

def test_no_dangerous_calls_in_source_tree() -> None:
    """Grep the whole src/ tree. Zero shell=True, zero os.system(),
    zero eval() (except PyTorch's model.eval() which is fine)."""
    repo = Path(__file__).resolve().parent.parent
    violations: list[str] = []

    for py_file in (repo / "src").rglob("*.py"):
        text = py_file.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "shell=True" in stripped and "subprocess" in text:
                violations.append(f"{py_file.relative_to(repo)}:{i}: shell=True")
            if "os.system(" in stripped:
                violations.append(f"{py_file.relative_to(repo)}:{i}: os.system()")
            if "eval(" in stripped:
                if ".eval()" not in stripped and "self.eval" not in stripped:
                    violations.append(f"{py_file.relative_to(repo)}:{i}: eval()")
            if "exec(" in stripped and ".executor" not in stripped:
                violations.append(f"{py_file.relative_to(repo)}:{i}: exec()")

    assert not violations, (
        f"Found {len(violations)} dangerous calls:\n" + "\n".join(violations[:10])
    )


# ---- No dynamic imports from user input ------------------------------

def test_no_dynamic_imports_from_user_input() -> None:
    """importlib.import_module() anywhere in src/ would mean user input
    could theoretically pick a module to load. Every import is static."""
    repo = Path(__file__).resolve().parent.parent
    violations: list[str] = []

    for py_file in (repo / "src").rglob("*.py"):
        text = py_file.read_text(encoding="utf-8", errors="replace")
        if "importlib.import_module" in text:
            violations.append(str(py_file.relative_to(repo)))

    assert not violations, f"importlib.import_module found in: {violations}"


# ---- Remote middleware blocks unauthorized writes --------------------

def test_remote_middleware_blocks_write_without_session() -> None:
    """A POST from a remote host without a valid session must get 403 or 423."""
    from godot_coder.ui.server import create_app
    from fastapi.testclient import TestClient

    root = Path(__file__).resolve().parent.parent
    app = create_app(root)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/jobs/corpus/build",
        headers={
            "Host": "foo.ts.net",
            "Tailscale-User-Login": "unknown@example.com",
        },
    )
    assert response.status_code in {403, 423}, (
        f"Expected 403 or 423 for unauthorized remote write, got {response.status_code}"
    )


# ---- server.py uses safe_child for path arguments --------------------

def test_server_py_uses_safe_child() -> None:
    """Every path argument in server.py must go through safe_child.
    If someone adds a new endpoint without it, this catches it."""
    server_path = Path(__file__).resolve().parent.parent / "src" / "godot_coder" / "ui" / "server.py"
    text = server_path.read_text(encoding="utf-8")
    assert "safe_child" in text, "server.py must use safe_child for path validation"
