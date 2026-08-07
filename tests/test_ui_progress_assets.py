from __future__ import annotations

import subprocess
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "godot_coder" / "ui" / "static"


class StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"] or "")
        if tag == "script" and attributes.get("src"):
            self.scripts.append(attributes["src"] or "")


def test_html_progress_structure_has_unique_ids_and_script_order() -> None:
    parser = StructureParser()
    parser.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    assert len(parser.ids) == len(set(parser.ids))
    for required in {
        "local-progress-dashboard", "local-project-grid", "log-auto-follow", "log-filter-project",
        "log-filter-phase", "log-view-simple", "log-view-technical", "export-log-jsonl",
    }:
        assert required in parser.ids
    assert parser.scripts.index("/static/progress.js") < parser.scripts.index("/static/app.js")


def test_javascript_syntax_and_ui_behavior_tests() -> None:
    for script in (STATIC / "progress.js", STATIC / "app.js"):
        subprocess.run(["node", "--check", str(script)], check=True, capture_output=True, text=True)
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "js_progress_test.cjs")],
        check=True, capture_output=True, text=True,
    )
    assert "passed" in result.stdout


def test_responsive_and_status_css_is_present() -> None:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert "@media (max-width: 520px)" in css
    assert ".status-running" in css
    assert ".status-quarantined" in css
    assert "overflow-wrap: anywhere" in css
