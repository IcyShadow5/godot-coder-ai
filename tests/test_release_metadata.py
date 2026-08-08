from pathlib import Path

from godot_coder import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_declares_the_testclient_transport_package() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    # Starlette TestClient imports ``httpx`` (not ``httpx2``, a separate package).
    assert 'httpx>=0.27' in pyproject
    assert 'httpx>=0.27' in requirements


def test_package_and_runtime_version_match() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert f'version = "{__version__}"' in pyproject
