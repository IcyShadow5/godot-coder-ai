from pathlib import Path

from godot_coder.corpus import build_staging, load_registry, save_registry


def test_official_registry_targets_godot_47_and_excludes_mixed_license_classes(tmp_path: Path) -> None:
    registry = load_registry(tmp_path)
    sources = {item["id"]: item for item in registry["sources"]}
    assert sources["godot-demo-projects"]["branch"] == "4.7-6ad6167"
    assert sources["godot-docs"]["branch"] == "4.7"
    assert sources["godot-docs"]["exclude_paths"] == ["classes"]


def test_corpus_scan_honors_source_excluded_paths(tmp_path: Path) -> None:
    save_registry(tmp_path, [{
        "id": "docs-test",
        "title": "Docs Test",
        "description": "test",
        "url": "https://example.invalid/docs.git",
        "branch": "4.7",
        "kind": "rst_gdscript",
        "license": "CC-BY-3.0",
        "attribution": "Test",
        "exclude_paths": ["classes"],
        "enabled": True,
    }])
    downloaded = tmp_path / "data" / "corpus" / "downloads" / "docs-test"
    (downloaded / "classes").mkdir(parents=True)
    (downloaded / "tutorials").mkdir(parents=True)
    (downloaded / "LICENSE").write_text("Creative Commons Attribution 3.0", encoding="utf-8")
    block = ".. code-block:: gdscript\n\n    extends Node\n    func _ready() -> void:\n        print(\"ok\")\n"
    (downloaded / "classes" / "node.rst").write_text(block, encoding="utf-8")
    (downloaded / "tutorials" / "intro.rst").write_text(block, encoding="utf-8")
    manifest = build_staging(tmp_path)
    assert manifest["summary"]["records"] == 1
    assert manifest["records"][0]["original_path"].startswith("tutorials/")


def test_registry_migrates_broken_demo_branch_to_official_47_tag(tmp_path: Path) -> None:
    registry = tmp_path / "data" / "corpus" / "sources.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        '{"format_version": 1, "sources": [{"id": "godot-demo-projects", "branch": "4.7"}]}',
        encoding="utf-8",
    )
    payload = load_registry(tmp_path)
    assert payload["sources"][0]["branch"] == "4.7-6ad6167"
    persisted = registry.read_text(encoding="utf-8")
    assert "4.7-6ad6167" in persisted


def test_custom_source_preserves_explicit_ref(tmp_path: Path) -> None:
    payload = save_registry(tmp_path, [{
        "id": "custom-demo",
        "title": "Custom Demo",
        "url": "https://example.com/demo.git",
        "ref": "v2.0.1",
        "kind": "godot_projects",
        "license": "MIT",
        "enabled": True,
    }])
    assert payload["sources"][0]["branch"] == "v2.0.1"


def test_partial_download_directory_is_not_reported_ready(tmp_path: Path) -> None:
    from godot_coder.corpus import status

    load_registry(tmp_path)
    partial = tmp_path / "data" / "corpus" / "downloads" / "godot-demo-projects"
    partial.mkdir(parents=True)
    (partial / "README.tmp").write_text("partial", encoding="utf-8")
    state = status(tmp_path)
    demo = next(item for item in state["downloads"] if item["id"] == "godot-demo-projects")
    assert demo["downloaded"] is False
    assert demo["present"] is False


def test_extract_rst_supports_gdscript_code_tabs_without_csharp_sibling(tmp_path: Path) -> None:
    from godot_coder.corpus import _extract_rst_gdscript

    source = tmp_path / "tabbed.rst"
    source.write_text(
        "Example\n=======\n\n"
        ".. tabs::\n\n"
        "   .. code-tab:: gdscript GDScript\n\n"
        "      extends Node\n\n"
        "      func _ready() -> void:\n"
        "          print(\"hello\")\n\n"
        "   .. code-tab:: csharp C#\n\n"
        "      using Godot;\n",
        encoding="utf-8",
    )
    snippets = list(_extract_rst_gdscript(source))
    assert len(snippets) == 1
    assert "extends Node" in snippets[0][1]
    assert "print(\"hello\")" in snippets[0][1]
    assert "using Godot" not in snippets[0][1]
