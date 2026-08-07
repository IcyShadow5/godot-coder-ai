from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable

FUNCTION_START = re.compile(r"^(?P<indent>[ \t]*)(?:static\s+)?func\s+[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)\s*(?:->\s*[^:]+)?\s*:\s*$")


def _functions(text: str) -> Iterable[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    i = 0
    while i < len(lines):
        match = FUNCTION_START.match(lines[i])
        if not match:
            i += 1
            continue
        base = len(match.group("indent").expandtabs(4))
        block = [lines[i]]
        i += 1
        while i < len(lines):
            line = lines[i]
            if line.strip():
                indent = len(line.expandtabs(4)) - len(line.lstrip(" \t").expandtabs(4))
                if indent <= base:
                    break
            block.append(line)
            i += 1
        while block and not block[-1].strip():
            block.pop()
        if 3 <= len(block) <= 180:
            yield "\n".join(block) + "\n"


def _completion_task(code: str, source: str, split: str) -> dict[str, Any] | None:
    lines = code.splitlines()
    if len(lines) < 4:
        return None
    cut = max(2, min(len(lines) - 1, len(lines) // 2))
    prefix = "\n".join(lines[:cut]) + "\n"
    task_id = hashlib.sha256(f"completion\0{source}\0{code}".encode()).hexdigest()[:24]
    return {
        "format_version": 1,
        "task_id": task_id,
        "split": split,
        "task_type": "function_completion",
        "system": "Du bist ein präziser Godot-4-GDScript-Assistent. Antworte nur mit gültigem GDScript.",
        "instruction": "Vervollständige die angefangene GDScript-Funktion. Behalte Signatur, Einrückung und vorhandene Logik bei.",
        "input": prefix,
        "output": code,
        "source_path": source,
        "validation": "output_from_audited_corpus",
    }


def _repair_task(code: str, source: str, split: str) -> dict[str, Any] | None:
    broken = code.replace(":\n", "\n", 1)
    if broken == code:
        return None
    task_id = hashlib.sha256(f"repair\0{source}\0{code}".encode()).hexdigest()[:24]
    return {
        "format_version": 1,
        "task_id": task_id,
        "split": split,
        "task_type": "syntax_repair",
        "system": "Du reparierst Godot-4-GDScript und gibst ausschließlich den korrigierten Code zurück.",
        "instruction": "Repariere den Syntaxfehler im folgenden GDScript, ohne die beabsichtigte Funktion zu verändern.",
        "input": broken,
        "output": code,
        "source_path": source,
        "validation": "output_from_audited_corpus",
    }


def build_instruction_dataset(project_root: Path, *, max_tasks_per_file: int = 2) -> dict[str, Any]:
    source_root = project_root / "data" / "corpus" / "audited"
    if not source_root.exists():
        source_root = project_root / "data" / "corpus" / "prepared"
    if not source_root.exists():
        raise FileNotFoundError("Kein auditierter oder vorbereiteter Corpus gefunden.")
    output_root = project_root / "data" / "instructions" / "v07"
    output_root.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {"train": 0, "val": 0, "test": 0}
    type_counts: dict[str, int] = {}
    fingerprints: list[str] = []
    for split in ("train", "val", "test"):
        records: list[dict[str, Any]] = []
        folder = source_root / split
        for path in sorted(folder.rglob("*.gd")) if folder.exists() else []:
            text = path.read_text(encoding="utf-8", errors="replace")
            relative = path.relative_to(source_root).as_posix()
            for function in _functions(text):
                candidates = (_completion_task(function, relative, split), _repair_task(function, relative, split))
                added = 0
                for task in candidates:
                    if task is None:
                        continue
                    records.append(task)
                    fingerprints.append(task["task_id"])
                    type_counts[task["task_type"]] = type_counts.get(task["task_type"], 0) + 1
                    added += 1
                    if added >= max_tasks_per_file:
                        break
                if added:
                    break
        destination = output_root / f"{split}.jsonl"
        temporary = destination.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        temporary.replace(destination)
        counts[split] = len(records)
    report = {
        "format": "godot-coder-instruction-foundation",
        "format_version": 1,
        "created_at": time.time(),
        "output_dir": output_root.relative_to(project_root).as_posix(),
        "counts": counts,
        "total_tasks": sum(counts.values()),
        "task_types": type_counts,
        "fingerprint": hashlib.sha256("\n".join(sorted(fingerprints)).encode()).hexdigest(),
        "training_ready": False,
        "warning": "This is a seed dataset. Do not replace broad pretraining with it; masked SFT is a later stage.",
    }
    report_path = project_root / "data" / "corpus" / "instruction_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic Godot instruction seed data")
    parser.add_argument("--root", default=".")
    parser.add_argument("build", nargs="?")
    parser.add_argument("--max-tasks-per-file", type=int, default=2)
    args = parser.parse_args()
    build_instruction_dataset(Path(args.root).resolve(), max_tasks_per_file=args.max_tasks_per_file)


if __name__ == "__main__":
    main()
