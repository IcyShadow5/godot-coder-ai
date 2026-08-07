import json
from pathlib import Path

from godot_coder.corpus import load_registry, verify_declared_license


def test_v07_registry_adds_verified_catalog_without_enabling_it(tmp_path: Path) -> None:
    registry = load_registry(tmp_path)
    sources = {item["id"]: item for item in registry["sources"]}
    assert registry["format_version"] == 3
    assert {"godot-demo-projects", "godot-docs", "gdquest-learn-gdscript", "gdquest-getting-started-godot4", "gdquest-open-rpg", "gdquest-godot4-new-features", "gdquest-third-person-controller"} <= set(sources)
    assert sources["godot-demo-projects"]["enabled"] is True
    assert sources["godot-docs"]["enabled"] is True
    assert sources["gdquest-learn-gdscript"]["enabled"] is False
    assert sources["gdquest-learn-gdscript"]["verified"] is True
    assert sources["gdquest-learn-gdscript"]["catalog_tier"] == "verified-community"


def test_v02_registry_migration_preserves_choices_and_adds_catalog(tmp_path: Path) -> None:
    path = tmp_path / "data" / "corpus" / "sources.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "format_version": 2,
        "sources": [{
            "id": "godot-demo-projects",
            "title": "Demo",
            "url": "https://github.com/godotengine/godot-demo-projects.git",
            "branch": "4.7-6ad6167",
            "kind": "godot_projects",
            "license": "MIT",
            "attribution": "Godot",
            "enabled": False,
        }],
    }), encoding="utf-8")
    registry = load_registry(tmp_path)
    sources = {item["id"]: item for item in registry["sources"]}
    assert sources["godot-demo-projects"]["enabled"] is False
    assert "gdquest-open-rpg" in sources
    assert "gdquest-godot4-new-features" in sources
    assert "gdquest-third-person-controller" in sources
    assert registry["format_version"] == 3


def test_local_mit_license_verification(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text(
        'MIT License\nPermission is hereby granted, free of charge, to any person obtaining a copy.\n'
        'THE SOFTWARE IS PROVIDED "AS IS".\n',
        encoding="utf-8",
    )
    result = verify_declared_license(repo, {"license": "MIT"})
    assert result["verified"] is True
    assert result["license_file"] == "LICENSE"


def test_missing_license_is_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = verify_declared_license(repo, {"license": "MIT"})
    assert result["verified"] is False
    assert result["reason_code"] == "license-file-not-found"


def test_v076_catalog_has_real_five_million_candidate_tier() -> None:
    from godot_coder.corpus import OFFICIAL_PRESETS

    core = [item for item in OFFICIAL_PRESETS if item.get("expansion_tier") == "core-5m"]
    extended = [item for item in OFFICIAL_PRESETS if item.get("expansion_tier") == "extended-20m"]
    assert len(core) >= 8
    assert len(extended) >= 14
    assert sum(int(item.get("estimated_unique_tokens") or 0) for item in core) >= 5_000_000
    assert sum(int(item.get("estimated_unique_tokens") or 0) for item in core + extended) >= 12_000_000
    assert all(item.get("enabled") is False for item in core + extended)
    assert all(item.get("license") in {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "CC-BY-3.0"} for item in core + extended)


def test_v076_registry_migration_adds_large_sources_disabled(tmp_path: Path) -> None:
    path = tmp_path / "data" / "corpus" / "sources.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "format_version": 3,
        "sources": [{
            "id": "godot-demo-projects", "title": "Demos", "description": "",
            "url": "https://github.com/godotengine/godot-demo-projects.git",
            "branch": "4.7-6ad6167", "kind": "godot_projects", "license": "MIT",
            "attribution": "Godot", "enabled": True,
        }],
    }), encoding="utf-8")
    sources = {item["id"]: item for item in load_registry(tmp_path)["sources"]}
    assert sources["godot-demo-projects"]["enabled"] is True
    assert sources["pixelorama"]["enabled"] is False
    assert sources["pixelorama"]["expansion_tier"] == "core-5m"
    assert sources["gdunit4"]["enabled"] is False
    assert sources["gdunit4"]["expansion_tier"] == "extended-20m"


def test_v076_save_registry_preserves_expansion_and_sparse_metadata(tmp_path: Path) -> None:
    from godot_coder.corpus import save_registry

    payload = save_registry(tmp_path, [{
        "id": "sample-source", "title": "Sample", "description": "Sample source",
        "url": "https://github.com/example/sample.git", "branch": "main",
        "kind": "godot_projects", "license": "MIT", "attribution": "Example",
        "enabled": False, "verified": True, "expansion_tier": "core-5m",
        "estimated_unique_tokens": 123456, "source_only": True, "allow_addons": True,
        "include_paths": ["game", "addons/sample"],
    }])
    source = payload["sources"][0]
    assert source["expansion_tier"] == "core-5m"
    assert source["estimated_unique_tokens"] == 123456
    assert source["source_only"] is True
    assert source["allow_addons"] is True
    assert source["include_paths"] == ["game", "addons/sample"]
