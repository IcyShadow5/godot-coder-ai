from __future__ import annotations

from pathlib import Path

from godot_coder.local_sources import import_inbox, inbox_path
from godot_coder.progress_events import parse_event_line


def _write_project(root: Path, name: str, *, warning: bool = False, secret: bool = False) -> None:
    root.mkdir(parents=True)
    (root / "project.godot").write_text(f'config_version=5\n[application]\nconfig/name="{name}"\n', encoding="utf-8")
    body = "extends Node\n\nfunc _ready() -> void:\n\tprint(1)\n"
    if warning:
        body += "\nfunc broken():\n\t...\n"
    if secret:
        body += '\nvar api_key = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"\n'
    (root / "scripts").mkdir()
    (root / "scripts" / ("x" * 90 + ".gd")).write_text(body, encoding="utf-8")
    (root / ".godot").mkdir()
    (root / ".godot" / "cache.bin").write_bytes(b"cache")
    (root / "addons" / "third_party").mkdir(parents=True)
    (root / "addons" / "third_party" / "plugin.gd").write_text("extends Node\n", encoding="utf-8")


def test_private_import_emits_multi_project_phases_and_quarantine(tmp_path: Path, monkeypatch, capsys) -> None:
    inbox = inbox_path(tmp_path)
    _write_project(inbox / "first", "First", warning=True)
    _write_project(inbox / "second", "Second", secret=True)
    monkeypatch.setattr("godot_coder.local_sources._validate_project", lambda project: ("passed", None, "ok"))

    report = import_inbox(tmp_path, ownership_confirmed=True)
    output = capsys.readouterr().out.splitlines()
    events = [event for line in output if (event := parse_event_line(line)) is not None]

    assert report["summary"]["planned_projects"] == 2
    assert report["summary"]["quarantined"] == 1
    assert {event.get("phase") for event in events} >= {
        "project_detection", "inventory", "cache_exclusion", "addon_classification",
        "secret_scan", "file_size_check", "static_analysis", "deduplication",
        "corpus_admission", "quarantine_decision", "registry_update", "report_writing",
    }
    progress_events = [event for event in events if event["event"] == "local_project_progress"]
    assert progress_events
    assert progress_events[0]["project_total"] == 2
    assert progress_events[0]["current_file"].endswith(".gd")
    assert any(event.get("next_project") == "Second" and event.get("next_project_scripts") == 1 for event in events)
    second = [event for event in events if event.get("project_name") == "Second"]
    assert any(event.get("project_status") == "quarantined" for event in second)
    serialized = "\n".join(output)
    assert "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in serialized
