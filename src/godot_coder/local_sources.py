from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .corpus import (
    MAX_ARCHIVE_FILES,
    MAX_ARCHIVE_UNCOMPRESSED,
    MAX_SINGLE_FILE,
    MAX_COMPRESSION_RATIO,
    _archive_preflight,
    _find_godot,
    _json_write,
    _safe_member,
    _source_metadata_path,
    corpus_root,
    load_registry,
    save_registry,
)
from .process_control import FailureKind, classify_failure, process_command_line, process_is_alive, run_managed_process, terminate_process_tree
from .progress_events import EtaEstimator, PHASE_LABELS, ProgressEmitter, mask_secrets

LOCAL_LICENSE = "LicenseRef-User-Owned-Private"
LOCAL_FORMAT_VERSION = 1
TRAIN_FILE_LIMIT = int(os.environ.get("GODOT_CODER_TRAIN_FILE_LIMIT_BYTES", str(4 * 1024**2)))  # default 4 MiB

GENERATED_PARTS = {
    ".git", ".godot", ".import", "node_modules", "bin", "obj", "build", "dist",
    "export", "exports", "coverage", "__pycache__", ".mono", ".cache",
}
DANGEROUS_SUFFIXES = {
    ".exe", ".msi", ".bat", ".cmd", ".ps1", ".dll", ".so", ".dylib", ".pck",
    ".apk", ".aab", ".app", ".jar", ".zip", ".7z", ".rar", ".tar", ".gz",
}
TEXT_SUFFIXES = {
    ".gd", ".gdshader", ".tscn", ".tres", ".godot", ".cfg", ".json", ".md",
    ".txt", ".ini", ".toml", ".yaml", ".yml", ".uid",
}
SECRET_NAME_RE = re.compile(
    r"(?:^|/)(?:\.env(?:\.|$)|id_rsa(?:\.|$)|id_ed25519(?:\.|$)|"
    r"[^/]*(?:secret|credentials?|private[_-]?key|api[_-]?key)[^/]*\.(?:env|pem|key|json|ya?ml|toml|cfg|ini|txt))$",
    re.IGNORECASE,
)
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "generic_secret_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|password)\b"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9_\-/.+=]{16,}"
        ),
    ),
)

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
KNOWN_GODOT_EDITOR_FAILURES: tuple[tuple[str, str], ...] = (
    ("EditorSettings not instantiated yet", "Godot Mono's EditorSettings were torn down too early during the headless import."),
    ("export/android/shutdown_adb_on_exit", "Godot hung during the Android/ADB cleanup of the headless editor."),
)

# Patterns to detect when Godot --import is stuck in an error loop (e.g. broken
# addon resources generating endless parse/resource errors without progress).
_ERROR_LINE_RE = re.compile(
    r"(?i)(?:ERROR:|SCRIPT ERROR:|Parse Error:|failed to load|Failed loading"
    r"|missing resource|invalid UID|can't load|does not exist|resulted in error)"
)
_PROGRESS_LINE_RE = re.compile(r"\[\s*\d{1,3}%\s*\]|first_scan_filesystem|importing assets")


@dataclass
class ImportPlan:
    project: Path
    source_item: Path
    project_name: str
    script_count: int


_PHASE_SEQUENCE = [
    "project_detection", "inventory", "cache_exclusion", "addon_classification",
    "secret_scan", "file_size_check", "static_analysis", "deduplication",
    "corpus_admission", "godot_validation", "quarantine_decision",
]

_PHASE_PROGRESS = {
    "project_detection": 0.03,
    "inventory": 0.10,
    "cache_exclusion": 0.15,
    "addon_classification": 0.20,
    "secret_scan": 0.34,
    "file_size_check": 0.42,
    "static_analysis": 0.62,
    "deduplication": 0.68,
    "corpus_admission": 0.76,
    "godot_validation": 0.90,
    "quarantine_decision": 1.0,
}


@dataclass
class ImportProgress:
    emitter: ProgressEmitter
    plans: list[ImportPlan]
    eta: EtaEstimator = field(default_factory=EtaEstimator)
    completed_scripts: int = 0
    completed_projects: int = 0

    @property
    def total_scripts(self) -> int:
        return sum(plan.script_count for plan in self.plans)

    def context(self, index: int) -> dict[str, Any]:
        plan = self.plans[index - 1]
        next_plan = self.plans[index] if index < len(self.plans) else None
        return {
            "project_index": index,
            "project_total": len(self.plans),
            "project_name": plan.project_name,
            "scripts_found": plan.script_count,
            "file_total": plan.script_count,
            "next_project": next_plan.project_name if next_plan else None,
            "next_project_scripts": next_plan.script_count if next_plan else None,
        }

    def emit(self, event: str, *, index: int, phase: str | None = None, **fields: Any) -> dict[str, Any]:
        context = self.context(index)
        context.update(fields)
        if phase:
            context.setdefault("phase", phase)
            context.setdefault("phase_label", PHASE_LABELS.get(phase, phase.replace("_", " ").title()))
            if phase in _PHASE_SEQUENCE:
                phase_position = _PHASE_SEQUENCE.index(phase)
                if phase_position + 1 < len(_PHASE_SEQUENCE):
                    context.setdefault("next_phase", _PHASE_SEQUENCE[phase_position + 1])
            base = _PHASE_PROGRESS.get(phase, 0.0)
            if phase == "static_analysis" and context.get("file_total"):
                ratio = min(1.0, float(context.get("file_index") or 0) / max(1.0, float(context["file_total"])))
                base = 0.42 + 0.20 * ratio
            elif phase == "godot_validation" and context.get("validation_mode") == "gdscript_check" and context.get("file_total"):
                ratio = min(1.0, float(context.get("file_index") or 0) / max(1.0, float(context["file_total"])))
                base = 0.76 + 0.14 * ratio
            context.setdefault("project_progress", base)
            context.setdefault("overall_progress", min(1.0, ((index - 1) + base) / max(1, len(self.plans))))
        remaining_files = max(0, self.total_scripts - self.completed_scripts)
        remaining_projects = max(0, len(self.plans) - self.completed_projects - 1)
        context.setdefault("remaining_files", remaining_files)
        eta_values = self.eta.estimate(
            remaining_files=remaining_files, remaining_projects=remaining_projects
        )
        context.update({key: value for key, value in eta_values.items() if value is not None})
        context.setdefault(
            "eta_status",
            "estimated" if eta_values.get("estimated_remaining_seconds") is not None else "calculating",
        )
        return self.emitter.emit(event, **context)

    def script_checked(self) -> None:
        self.completed_scripts += 1
        self.eta.observe_file()

    def project_finished(self) -> None:
        self.completed_projects += 1
        self.eta.observe_project()


def _quick_script_count(project: Path) -> int:
    count = 0
    for path in project.rglob("*.gd"):
        if not path.is_file():
            continue
        relative = path.relative_to(project)
        lower_parts = {part.lower() for part in relative.parts}
        if lower_parts & GENERATED_PARTS or "addons" in lower_parts:
            continue
        count += 1
    return count


@dataclass
class ProjectAudit:
    project_name: str
    project_root: str
    source_item: str
    source_sha256: str
    godot_features: list[str]
    gd_files: int
    gd_bytes: int
    gd_lines: int
    estimated_bpe_tokens: int
    trainable_gd_files: int
    trainable_gd_bytes: int
    trainable_estimated_bpe_tokens: int
    oversized_gd_files: list[str]
    addon_files: int
    generated_files: int
    executable_files: int
    secret_hits: list[dict[str, Any]]
    static_warnings: list[dict[str, Any]]
    known_logs: dict[str, Any]
    ownership_confirmed: bool
    redistribution_allowed: bool
    parser_checked_files: int = 0
    parser_failed_files: list[str] = field(default_factory=list)
    validation_mode: str = "not_run"
    validation_status: str = "not_run"
    validation_error: str | None = None
    imported_source_id: str | None = None
    imported_path: str | None = None
    enabled_for_training: bool = False


def local_root(project_root: Path) -> Path:
    return project_root / "data" / "local_sources"


def inbox_path(project_root: Path) -> Path:
    path = local_root(project_root) / "inbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _decode(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def _safe_extract(path: Path, destination: Path) -> None:
    _archive_preflight(path)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            relative = Path(*PurePosixPath(info.filename.replace("\\", "/")).parts)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def _find_projects(root: Path) -> list[Path]:
    projects = sorted(path.parent for path in root.rglob("project.godot") if not any(part.lower() in GENERATED_PARTS for part in path.parts))
    # Do not treat nested fixture projects as independent when a parent project already owns them.
    result: list[Path] = []
    for project in projects:
        if any(parent == project or parent in project.parents for parent in result):
            continue
        result.append(project)
    return result


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (value or "godot-project")[:42]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _source_fingerprint(source_item: Path, project: Path) -> str:
    digest = hashlib.sha256()
    if source_item.is_file():
        digest.update(_sha256_file(source_item).encode())
    else:
        for path in sorted(project.rglob("*")):
            if not path.is_file() or any(part.lower() in GENERATED_PARTS for part in path.relative_to(project).parts):
                continue
            digest.update(path.relative_to(project).as_posix().encode("utf-8"))
            digest.update(str(path.stat().st_size).encode())
            if path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= 2 * 1024**2:
                digest.update(path.read_bytes())
    return digest.hexdigest()


def _project_name(project: Path) -> tuple[str, list[str]]:
    name = project.name
    features: list[str] = []
    config = project / "project.godot"
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return name, features
    match = re.search(r'(?m)^config/name\s*=\s*"([^"]+)"', text)
    if match:
        name = match.group(1).strip() or name
    feature_match = re.search(r'(?m)^config/features\s*=\s*PackedStringArray\((.*?)\)', text)
    if feature_match:
        features = re.findall(r'"([^"]+)"', feature_match.group(1))
    return name, features


def _strip_strings_and_comments(text: str) -> str:
    output: list[str] = []
    quote: str | None = None
    triple = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        nxt3 = text[index:index + 3]
        if quote is not None:
            if char == "\n":
                output.append("\n")
            else:
                output.append(" ")
            if escaped:
                escaped = False
            elif char == "\\" and not triple:
                escaped = True
            elif triple and nxt3 == quote * 3:
                output.extend("  ")
                index += 2
                quote = None
                triple = False
            elif not triple and char == quote:
                quote = None
            index += 1
            continue
        if nxt3 in {'"""', "'''"}:
            quote = nxt3[0]
            triple = True
            output.extend("   ")
            index += 3
            continue
        if char in {'"', "'"}:
            quote = char
            output.append(" ")
            index += 1
            continue
        if char == "#":
            while index < len(text) and text[index] != "\n":
                output.append(" ")
                index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _static_warnings(path: Path, text: str, *, display_path: Path | None = None) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    shown = display_path or path
    if "\ufffd" in text:
        warnings.append({"code": "encoding_replacement", "path": shown.as_posix()})
    if re.search(r"(?m)^(?:<<<<<<<|=======|>>>>>>>)", text):
        warnings.append({"code": "merge_markers", "path": shown.as_posix()})
    if re.search(r"(?m)^\s*\.\.\.\s*$", text):
        warnings.append({"code": "ellipsis_placeholder", "path": shown.as_posix()})
    for number, line in enumerate(text.splitlines(), start=1):
        leading = re.match(r"^[ \t]+", line)
        if leading and " " in leading.group(0) and "\t" in leading.group(0):
            warnings.append({"code": "mixed_leading_indentation", "path": shown.as_posix(), "line": number})
            break
    cleaned = _strip_strings_and_comments(text)
    stack: list[tuple[str, int]] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for number, line in enumerate(cleaned.splitlines(), start=1):
        for char in line:
            if char in "([{":
                stack.append((char, number))
            elif char in ")]}":
                if not stack or stack[-1][0] != pairs[char]:
                    warnings.append({"code": "unbalanced_delimiter", "path": shown.as_posix(), "line": number})
                    return warnings
                stack.pop()
    if stack:
        warnings.append({"code": "unclosed_delimiter", "path": shown.as_posix(), "line": stack[-1][1]})
    function_names = re.findall(r"(?m)^func\s+([A-Za-z_]\w*)\s*\(", cleaned)
    duplicates = [name for name, count in __import__("collections").Counter(function_names).items() if count > 1 and name not in {"_init"}]
    if duplicates:
        warnings.append({"code": "duplicate_top_level_function_name", "path": shown.as_posix(), "names": sorted(duplicates)})
    if path.stat().st_size > TRAIN_FILE_LIMIT:
        warnings.append({"code": "oversized_training_file", "path": shown.as_posix(), "bytes": path.stat().st_size})
    elif path.stat().st_size > 96 * 1024:
        warnings.append({"code": "very_large_script", "path": shown.as_posix(), "bytes": path.stat().st_size})
    return warnings


def _known_log_summary(project: Path) -> dict[str, Any]:
    candidates = [
        path for path in project.rglob("*.txt")
        if re.search(r"(?:import|run)_log", path.name, re.IGNORECASE) and path.stat().st_size < 8 * 1024**2
    ]
    entries: list[dict[str, Any]] = []
    for path in sorted(candidates, key=lambda item: item.name.lower()):
        text = path.read_text(encoding="utf-8", errors="replace")
        entries.append({
            "path": path.relative_to(project).as_posix(),
            "parse_errors": len(re.findall(r"Parse Error", text, re.IGNORECASE)),
            "runtime_errors": len(re.findall(r"(?m)^ERROR:", text)),
        })
    clean = [item for item in entries if item["parse_errors"] == 0 and item["runtime_errors"] == 0]
    preferred = sorted(
        clean,
        key=lambda item: (
            "final" not in item["path"].lower(),
            "fix2" not in item["path"].lower(),
            "fix" not in item["path"].lower(),
            item["path"].lower(),
        ),
    )
    return {
        "files": len(entries),
        "clean_logs": len(clean),
        "preferred_clean_log": preferred[0] if preferred else None,
        "historical_logs_with_parse_errors": sum(item["parse_errors"] > 0 for item in entries),
        "historical_logs_with_runtime_errors": sum(item["runtime_errors"] > 0 for item in entries),
        "entries": entries[:100],
    }

def audit_project(
    project: Path,
    source_item: Path,
    *,
    ownership_confirmed: bool,
    progress: ImportProgress | None = None,
    project_index: int | None = None,
) -> ProjectAudit:
    project_name, features = _project_name(project)
    source_sha = _source_fingerprint(source_item, project)
    all_files = sorted(path for path in project.rglob("*") if path.is_file())
    gd_files = 0
    gd_bytes = 0
    gd_lines = 0
    trainable_gd_files = 0
    trainable_gd_bytes = 0
    addon_files = 0
    generated_files = 0
    executable_files = 0
    oversized: list[str] = []
    secret_hits: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if progress is not None and project_index is not None:
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="inventory",
            phase_status="running",
            project_status="running",
            message=f"Inventorizing {len(all_files)} files.",
        )

    usable_files: list[Path] = []
    script_files: list[Path] = []
    for path in all_files:
        relative = path.relative_to(project)
        lower_parts = {part.lower() for part in relative.parts}
        if lower_parts & GENERATED_PARTS:
            generated_files += 1
            continue
        usable_files.append(path)
        if "addons" in lower_parts:
            addon_files += 1
        elif path.suffix.lower() == ".gd":
            script_files.append(path)
        if path.suffix.lower() in DANGEROUS_SUFFIXES:
            executable_files += 1

    gd_files = len(script_files)
    if progress is not None and project_index is not None:
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="inventory",
            phase_status="completed",
            scripts_found=gd_files,
            file_total=gd_files,
            message=f"{gd_files} trainable GDScript files detected.",
        )
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="cache_exclusion",
            phase_status="completed",
            generated_files=generated_files,
            message=f"{generated_files} cache/import files excluded.",
        )
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="addon_classification",
            phase_status="completed",
            addon_files=addon_files,
            message=f"{addon_files} add-on files classified and excluded from training.",
        )
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="secret_scan",
            phase_status="running",
            message="Checking text files for possible credentials.",
        )

    fast_static = os.environ.get("GODOT_CODER_FAST_STATIC", "").strip() == "1"

    # Secret scan — runs in every mode, no exceptions. I made FAST_STATIC skip
    # this in v0.10.1 and regretted it immediately: a stray .env or hardcoded
    # API key would have sailed straight into the training corpus. It's cheap
    # next to a Godot startup, so there is no reason to ever skip it.
    # FAST_STATIC only skips the slow per-file AST walk below.
    for path in usable_files:
        relative = path.relative_to(project)
        if SECRET_NAME_RE.search(relative.as_posix()):
            secret_hits.append({"path": relative.as_posix(), "kind": "suspicious_filename"})
        if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 4 * 1024**2:
            continue
        text_value, encoding = _decode(path.read_bytes())
        if encoding == "utf-8-replace":
            warnings.append({"code": "encoding_damage", "path": relative.as_posix()})
        for kind, pattern in SECRET_PATTERNS:
            match = pattern.search(text_value)
            if match:
                secret_hits.append({
                    "path": relative.as_posix(),
                    "kind": kind,
                    "line": text_value.count("\n", 0, match.start()) + 1,
                })

    if progress is not None and project_index is not None:
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="secret_scan",
            phase_status="passed" if not secret_hits else "passed_with_warnings",
            warnings=len(secret_hits),
            message="Secret scan passed." if not secret_hits else f"{len(secret_hits)} possible secrets detected; contents are not shown.",
            level="info" if not secret_hits else "warning",
        )
    if progress is not None and project_index is not None:
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="file_size_check",
            phase_status="running",
            message="GDScript file sizes are being checked.",
        )

    # Per-script pass. Counting always; the AST warning walk only in normal
    # mode. FAST_STATIC saves the slow part, nothing else — and every file is
    # processed exactly once either way.
    passed_files = 0
    warning_files = 0
    for file_index, path in enumerate(script_files, start=1):
        relative = path.relative_to(project)
        size = path.stat().st_size
        if size > TRAIN_FILE_LIMIT:
            oversized.append(relative.as_posix())
        text_value, encoding = _decode(path.read_bytes())
        raw = text_value.encode("utf-8")
        gd_bytes += len(raw)
        gd_lines += text_value.count("\n") + 1
        if len(raw) <= TRAIN_FILE_LIMIT:
            trainable_gd_files += 1
            trainable_gd_bytes += len(raw)
        file_warnings: list[dict[str, Any]] = []
        if not fast_static:
            file_warnings = _static_warnings(path, text_value, display_path=relative)
            warnings.extend(file_warnings)
        if encoding == "utf-8-replace":
            # Mirrored into the per-file tally so the progress events don't
            # claim "passed" for a damaged file. The record itself already
            # lives in `warnings` via the secret scan above — no double entries.
            file_warnings.append({"code": "encoding_damage", "path": relative.as_posix()})
        if file_warnings:
            warning_files += 1
        else:
            passed_files += 1
        if progress is not None and project_index is not None:
            progress.script_checked()
            progress.emit(
                "local_project_progress",
                index=project_index,
                phase="static_analysis",
                phase_status="running",
                current_file=relative.as_posix(),
                file_index=file_index,
                file_total=gd_files,
                passed=passed_files,
                warnings=warning_files,
                failed=0,
                scripts_found=gd_files,
                trainable_scripts=trainable_gd_files,
                level="warning" if file_warnings else "info",
                message=f"{relative.as_posix()} checked" + (f" · {len(file_warnings)} warning(s)" if file_warnings else " · passed"),
            )

    if progress is not None and project_index is not None:
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="file_size_check",
            phase_status="passed" if not oversized else "passed_with_warnings",
            warnings=len(oversized),
            message="All script sizes are trainable." if not oversized else f"{len(oversized)} oversized scripts are excluded.",
            level="info" if not oversized else "warning",
        )
    if progress is not None and project_index is not None:
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="static_analysis",
            phase_status="skipped" if fast_static else ("passed_with_warnings" if warning_files else "passed"),
            file_index=gd_files,
            file_total=gd_files,
            passed=passed_files,
            warnings=warning_files,
            failed=0,
            scripts_found=gd_files,
            trainable_scripts=trainable_gd_files,
            message=(
                f"Static AST check skipped (GODOT_CODER_FAST_STATIC=1): {passed_files} scripts adopted; secrets and file sizes were still checked."
                if fast_static
                else f"Static check finished: {passed_files} without warnings, {warning_files} with warnings."
            ),
            level="info" if fast_static else ("warning" if warning_files else "info"),
        )

    return ProjectAudit(
        project_name=project_name,
        project_root=project.name,
        source_item=source_item.name,
        source_sha256=source_sha,
        godot_features=features,
        gd_files=gd_files,
        gd_bytes=gd_bytes,
        gd_lines=gd_lines,
        estimated_bpe_tokens=round(gd_bytes / 3.11),
        trainable_gd_files=trainable_gd_files,
        trainable_gd_bytes=trainable_gd_bytes,
        trainable_estimated_bpe_tokens=round(trainable_gd_bytes / 3.11),
        oversized_gd_files=oversized,
        addon_files=addon_files,
        generated_files=generated_files,
        executable_files=executable_files,
        secret_hits=secret_hits,
        static_warnings=warnings,
        known_logs=_known_log_summary(project),
        ownership_confirmed=ownership_confirmed,
        redistribution_allowed=False,
    )



def _safe_remove_tree(path: Path, *, attempts: int = 6, delay_seconds: float = 0.25) -> str | None:
    if not path.exists():
        return None
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return None
        except FileNotFoundError:
            return None
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay_seconds * (attempt + 1))
    return f"{type(last_error).__name__}: {last_error}" if last_error else "unknown cleanup error"


def _cleanup_generated_copy(destination: Path) -> list[str]:
    locked: list[str] = []
    if not destination.exists():
        return locked
    for name in sorted(GENERATED_PARTS):
        candidate = destination / name
        if not candidate.exists():
            continue
        error = _safe_remove_tree(candidate, attempts=3, delay_seconds=0.15)
        if error:
            locked.append(f"{candidate}: {error}")
    return locked


def _copy_project(
    source: Path,
    destination: Path,
    *,
    progress: ImportProgress | None = None,
    project_index: int | None = None,
    reuse_existing: bool = False,
) -> None:
    if progress is not None and project_index is not None:
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="corpus_admission",
            phase_status="running",
            message="A cleaned working copy is created separately.",
        )
    if reuse_existing and destination.is_dir() and (destination / "project.godot").is_file():
        locked_generated = _cleanup_generated_copy(destination)
        if progress is not None and project_index is not None:
            progress.emit(
                "local_project_phase",
                index=project_index,
                phase="corpus_admission",
                phase_status="passed_with_warnings" if locked_generated else "completed",
                message=(
                    "The existing deterministic working copy is reused; a locked cache file stays excluded."
                    if locked_generated
                    else "The existing deterministic working copy is reused."
                ),
                level="warning" if locked_generated else "info",
                locked_generated_paths=len(locked_generated),
            )
        return

    work = destination.with_name(destination.name + f".building-{uuid.uuid4().hex[:8]}")
    work.mkdir(parents=True, exist_ok=False)
    copied = 0
    try:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            lower_parts = {part.lower() for part in relative.parts}
            if lower_parts & GENERATED_PARTS:
                continue
            if path.suffix.lower() in DANGEROUS_SUFFIXES:
                continue
            if SECRET_NAME_RE.search(relative.as_posix()):
                continue
            target = work / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
        if destination.exists():
            error = _safe_remove_tree(destination)
            if error:
                raise OSError(
                    f"The existing working copy is still locked by a process: {destination}. "
                    f"Details: {error}"
                )
        work.replace(destination)
    finally:
        if work.exists():
            _safe_remove_tree(work, attempts=2)
    if progress is not None and project_index is not None:
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="corpus_admission",
            phase_status="completed",
            message=f"Created a cleaned working copy with {copied} files.",
        )


@dataclass
class ProjectValidationResult:
    status: str
    error: str | None
    output: str
    failed_files: list[str] = field(default_factory=list)
    checked_files: int = 0
    mode: str = "project_import"
    infrastructure_failure: str | None = None
    failure_kind: FailureKind = FailureKind.NONE

    def __iter__(self) -> Iterator[str | None]:
        yield self.status
        yield self.error
        yield self.output


def _strip_ansi(value: str) -> str:
    return ANSI_ESCAPE_RE.sub("", str(value)).replace("\r", "").strip()


def _known_editor_failure(line: str) -> str | None:
    clean = _strip_ansi(line)
    for needle, explanation in KNOWN_GODOT_EDITOR_FAILURES:
        if needle in clean:
            return explanation
    return None


def _validation_script_paths(project: Path) -> list[Path]:
    scripts: list[Path] = []
    for path in sorted(project.rglob("*.gd")):
        if not path.is_file():
            continue
        relative = path.relative_to(project)
        lower_parts = {part.lower() for part in relative.parts}
        if lower_parts & GENERATED_PARTS or "addons" in lower_parts:
            continue
        if path.stat().st_size > TRAIN_FILE_LIMIT:
            continue
        scripts.append(relative)
    return scripts


def _first_error(output: str) -> str | None:
    for line in output.splitlines():
        stripped = line.strip()
        if "Parse Error:" in stripped or "SCRIPT ERROR:" in stripped or stripped.startswith("ERROR:"):
            return stripped[:600]
    return None


def _validation_timeout_seconds() -> float:
    raw = os.environ.get("GODOT_CODER_VALIDATION_TIMEOUT_SECONDS", "120")
    try:
        return min(1800.0, max(1.0, float(raw)))
    except ValueError:
        return 120.0


def _validation_retry_timeout_seconds(primary_timeout: float) -> float:
    # Retained for backwards-compatible environment configuration. v0.7.5 uses
    # a parser fallback instead of repeating the same hanging editor import.
    raw = os.environ.get("GODOT_CODER_VALIDATION_RETRY_TIMEOUT_SECONDS", "180")
    try:
        return min(primary_timeout, min(900.0, max(1.0, float(raw))))
    except ValueError:
        return min(primary_timeout, 180.0)


def _error_abort_threshold() -> int:
    """Max consecutive error lines before aborting a stuck Godot --import."""
    raw = os.environ.get("GODOT_CODER_ERROR_ABORT_THRESHOLD", "500")
    try:
        return min(10000, max(50, int(raw)))
    except ValueError:
        return 500


def _validation_idle_timeout_seconds() -> float:
    raw = os.environ.get("GODOT_CODER_VALIDATION_IDLE_TIMEOUT_SECONDS", "30")
    try:
        return min(600.0, max(5.0, float(raw)))
    except ValueError:
        return 30.0


def _parser_file_timeout_seconds() -> float:
    raw = os.environ.get("GODOT_CODER_PARSER_FILE_TIMEOUT_SECONDS", "10")
    try:
        return min(180.0, max(2.0, float(raw)))
    except ValueError:
        return 10.0


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _write_active_validation(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _recover_recorded_validation(project_root: Path, emitter: ProgressEmitter | None = None) -> bool:
    record_path = project_root / "reports" / "local_sources" / "active_validation.json"
    if not record_path.is_file():
        return False
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
        workspace = str(payload.get("workspace") or "")
    except (OSError, ValueError, TypeError):
        record_path.unlink(missing_ok=True)
        return False
    if not process_is_alive(pid):
        record_path.unlink(missing_ok=True)
        return False
    command_line = process_command_line(pid) or ""
    normalized_command = command_line.replace("\\", "/").lower()
    normalized_workspace = workspace.replace("\\", "/").lower()
    verified = bool(normalized_workspace and normalized_workspace in normalized_command and "godot" in normalized_command)
    if not verified:
        if emitter:
            emitter.emit(
                "local_validation_recovery",
                phase="godot_validation",
                phase_status="skipped",
                level="warning",
                message="An old validation PID was found but could not be reliably identified as a Godot process; it was not terminated.",
            )
        return False
    stopped = terminate_process_tree(pid, force=False, wait_seconds=3.0)
    if not stopped:
        stopped = terminate_process_tree(pid, force=True, wait_seconds=8.0)
    # Never report recovery success while the exact recorded PID is still alive.
    # This matters on Windows where taskkill can return before the process handle
    # is signalled, especially for Python/Godot/Mono process trees.
    if stopped:
        deadline = time.monotonic() + 3.0
        while process_is_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        stopped = not process_is_alive(pid)
    if stopped:
        record_path.unlink(missing_ok=True)
    if emitter:
        emitter.emit(
            "local_validation_recovery",
            phase="godot_validation",
            phase_status="completed" if stopped else "failed",
            level="warning" if stopped else "error",
            message=(
                "Safely terminated the leftover Godot validation process."
                if stopped else "The leftover Godot validation process could not be terminated."
            ),
        )
    return stopped


def _run_gdscript_parser_fallback(
    godot: str,
    workspace: Path,
    *,
    progress: ImportProgress | None,
    project_index: int | None,
    active_record_path: Path,
    reason: str,
    static_warning_paths: set[str] | None = None,
) -> ProjectValidationResult:
    all_scripts = _validation_script_paths(workspace)
    if not all_scripts:
        return ProjectValidationResult(
            status="failed",
            error="No trainable GDScript files found for the parser fallback check.",
            output="",
            mode="gdscript_check",
            infrastructure_failure=reason,
        )

    warned_paths = static_warning_paths  # None means "check everything" (old behavior)
    # Only run Godot per-file checks on scripts that had static warnings.
    # Files that passed static analysis cleanly are very unlikely to have
    # Godot parse errors, and skipping them eliminates the per-file Godot
    # process startup overhead (3-8s per file).
    # When static_warning_paths is None (not provided), check all files.
    check_scripts: list[Path]
    skip_passed: list[Path]
    if warned_paths is not None:
        check_scripts = []
        skip_passed = []
        for relative in all_scripts:
            if relative.as_posix() in warned_paths:
                check_scripts.append(relative)
            else:
                skip_passed.append(relative)
    else:
        check_scripts = list(all_scripts)
        skip_passed = []

    if progress is not None and project_index is not None:
        skipped_message = ""
        if skip_passed:
            skipped_message = f" {len(skip_passed)} statically inconspicuous scripts are adopted directly."
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="godot_validation",
            phase_status="running",
            validation_mode="gdscript_check",
            validation_fallback=True,
            eta_status="calculating",
            file_index=0,
            file_total=len(check_scripts),
            passed=len(skip_passed),
            failed=0,
            warnings=1,
            statically_passed=len(skip_passed),
            godot_checked=len(check_scripts),
            message=(
                "The full Godot Mono editor import was stopped because it no longer made reliable progress. "
                f"Godot now checks {len(check_scripts)} suspicious GDScript file(s) individually without the editor, plugins, hot reload or ADB cleanup."
                + skipped_message
            ),
            level="warning" if check_scripts else "info",
        )

    # Fast path: no files need Godot checking
    if not check_scripts:
        return ProjectValidationResult(
            status="passed_with_warnings",
            error=None,
            output=f"Skipped Godot per-file check: all {len(skip_passed)} script(s) passed static analysis cleanly.\n--- reason ---\n{reason}",
            failed_files=[],
            checked_files=len(all_scripts),
            mode="gdscript_check",
            infrastructure_failure=reason,
        )

    passed = len(skip_passed)
    failed_files: list[str] = []
    output_parts: list[str] = [
        f"--- project import fallback reason ---\n{reason}",
        f"--- pre-skipped {len(skip_passed)} statically clean script(s) ---",
    ]
    timeout = _parser_file_timeout_seconds()

    for file_index, relative in enumerate(check_scripts, start=1):
        resource_path = "res://" + relative.as_posix()
        command = [
            godot,
            "--headless",
            "--xr-mode",
            "off",
            "--disable-crash-handler",
            "--path",
            str(workspace),
            "--check-only",
            "--script",
            resource_path,
        ]
        current_lines: list[str] = []

        def _on_start(pid: int) -> None:
            _write_active_validation(active_record_path, {
                "format": "godot-coder-active-validation",
                "format_version": 2,
                "pid": pid,
                "workspace": str(workspace),
                "command": command,
                "started_at": time.time(),
                "mode": "gdscript_check",
                "current_file": relative.as_posix(),
                "file_index": file_index,
                "file_total": len(check_scripts),
            })

        def _on_line(line: str) -> None:
            safe = str(mask_secrets(line))
            current_lines.append(safe)
            print(f"GODOT_PARSER_OUTPUT file={relative.as_posix()} {safe}", flush=True)

        result = run_managed_process(
            command,
            cwd=workspace,
            env=os.environ.copy(),
            timeout_seconds=timeout,
            idle_timeout_seconds=min(timeout, 10.0),
            heartbeat_seconds=5.0,
            on_line=_on_line,
            on_start=_on_start,
        )
        active_record_path.unlink(missing_ok=True)
        text = "\n".join(current_lines or [result.output])
        output_parts.append(f"--- parser file {relative.as_posix()} ---\n{text}")
        first = _first_error(text)
        file_passed = (
            not result.startup_error
            and not result.timed_out
            and not result.aborted
            and result.return_code == 0
            and first is None
        )
        if file_passed:
            passed += 1
        else:
            failed_files.append(relative.as_posix())

        if progress is not None and project_index is not None:
            detail = "passed" if file_passed else "excluded"
            if result.timed_out:
                detail = "Time limit reached and excluded"
            elif result.startup_error:
                detail = "Godot startup failed and excluded"
            elif first:
                detail = f"Parser error detected and excluded: {_strip_ansi(first)[:180]}"
            progress.emit(
                "local_project_progress",
                index=project_index,
                phase="godot_validation",
                phase_status="running",
                validation_mode="gdscript_check",
                validation_fallback=True,
                eta_status="calculating",
                current_file=relative.as_posix(),
                file_index=file_index,
                file_total=len(check_scripts),
                passed=passed,
                failed=len(failed_files),
                warnings=1,
                statically_passed=len(skip_passed),
                godot_checked_total=len(check_scripts),
                message=f"Parser fallback check {file_index}/{len(check_scripts)} · {relative.as_posix()} · {detail}",
                level="info" if file_passed else "warning",
            )

    output = "\n".join(output_parts)
    if passed <= 0:
        return ProjectValidationResult(
            status="failed",
            error=f"Parser fallback check: all {len(all_scripts)} trainable scripts failed.",
            output=output,
            failed_files=failed_files,
            checked_files=len(all_scripts),
            mode="gdscript_check",
            infrastructure_failure=reason,
        )
    return ProjectValidationResult(
        status="passed_with_warnings",
        error=None,
        output=output,
        failed_files=failed_files,
        checked_files=len(all_scripts),
        mode="gdscript_check",
        infrastructure_failure=reason,
    )


def _validate_project(
    project: Path,
    *,
    progress: ImportProgress | None = None,
    project_index: int | None = None,
    workspace_root: Path | None = None,
    active_record_path: Path | None = None,
    static_warning_paths: set[str] | None = None,
) -> ProjectValidationResult:
    godot = _find_godot()
    if not godot:
        if progress is not None and project_index is not None:
            progress.emit(
                "local_project_phase",
                index=project_index,
                phase="godot_validation",
                phase_status="skipped",
                project_status="disabled",
                validation_status="not_run",
                validation_mode="not_run",
                eta_status="calculating",
                message="Godot was not found; the project-wide parser check could not run.",
                level="warning",
            )
        return ProjectValidationResult("not_run", "Godot executable not found", "", mode="not_run", failure_kind=FailureKind.ENVIRONMENT_ERROR)

    validation_root = workspace_root or (Path(tempfile.gettempdir()) / "godot-coder-validation")
    validation_root.mkdir(parents=True, exist_ok=True)
    workspace = validation_root / f"{_slug(project.name)}-{uuid.uuid4().hex[:10]}"
    ignore_names = set(GENERATED_PARTS)

    def _ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name.lower() in ignore_names}

    shutil.copytree(project, workspace, ignore=_ignore)
    timeout = _validation_timeout_seconds()
    idle_timeout = _validation_idle_timeout_seconds()
    record_path = active_record_path or (validation_root.parent / "active_validation.json")

    def _cleanup_workspace() -> None:
        """Remove the isolated validation working copy (both validation paths)."""
        record_path.unlink(missing_ok=True)
        cleanup_error = _safe_remove_tree(workspace)
        if cleanup_error and (
            progress is not None and project_index is not None
        ):
            progress.emit(
                "local_validation_cleanup",
                index=project_index,
                phase="godot_validation",
                message="The isolated validation working copy remained behind due to a file lock; it will be cleaned again on the next run.",
                level="warning",
                validation_cleanup_error=cleanup_error,
            )

    command = [
        godot,
        "--headless",
        "--xr-mode",
        "off",
        "--disable-crash-handler",
        "--path",
        str(workspace),
        "--import",
    ]
    lines: list[str] = []
    infrastructure_failure: str | None = None
    error_count: int = 0  # tracked by _abort_on_line to detect stuck error loops

    skip_import = os.environ.get("GODOT_CODER_SKIP_PROJECT_IMPORT", "").strip() == "1"
    # SKIP_PROJECT_IMPORT always wins now. In v0.10.1 it was silently ignored
    # when FAST_STATIC=1 was set too — imports ended up slower than I wanted
    # and it was genuinely confusing. FAST_STATIC only skips the AST walk
    # during auditing; it has nothing to do with which Godot validation path
    # we take.
    if skip_import:
        try:
            reason = "GODOT_CODER_SKIP_PROJECT_IMPORT=1 — project import skipped, using direct per-file parser."
            if progress is not None and project_index is not None:
                progress.emit(
                    "local_project_phase",
                    index=project_index,
                    phase="godot_validation",
                    phase_status="running",
                    validation_mode="gdscript_check",
                    validation_skip_import=True,
                    eta_status="calculating",
                    message="Godot project import skipped; direct per-file parser check starts.",
                )
            fallback = _run_gdscript_parser_fallback(
                godot,
                workspace,
                progress=progress,
                project_index=project_index,
                active_record_path=record_path,
                reason=reason,
                static_warning_paths=static_warning_paths,
            )
            fallback.output = f"--- skip import reason ---\n{reason}\n" + fallback.output
            if progress is not None and project_index is not None:
                accepted = fallback.status in {"passed", "passed_with_warnings"}
                failed_count = len(fallback.failed_files)
                progress.emit(
                    "local_project_phase",
                    index=project_index,
                    phase="godot_validation",
                    phase_status="passed" if fallback.status == "passed" else ("passed_with_warnings" if accepted else "failed"),
                    project_status="running" if accepted else "failed",
                    validation_status=fallback.status,
                    validation_mode=fallback.mode,
                    validation_fallback=fallback.mode == "gdscript_check",
                    validation_infrastructure_failure=fallback.infrastructure_failure,
                    parser_checked_files=fallback.checked_files,
                    parser_failed_files=fallback.failed_files,
                    file_index=fallback.checked_files if fallback.mode == "gdscript_check" else None,
                    file_total=fallback.checked_files if fallback.mode == "gdscript_check" else None,
                    passed=max(0, fallback.checked_files - failed_count) if fallback.checked_files else None,
                    failed=failed_count,
                    eta_status="calculating",
                    parser_output=fallback.output[-6000:],
                    message=(
                        "Godot project import skipped; parser fallback check finished."
                        if fallback.status == "passed"
                        else (
                            f"Parser fallback check finished: {fallback.checked_files - failed_count}/{fallback.checked_files} scripts passed; {failed_count} excluded."
                            if accepted else str(fallback.error)
                        )
                    ),
                    level="info" if fallback.status == "passed" else ("warning" if accepted else "error"),
                )
            return fallback
        finally:
            _cleanup_workspace()

    try:
        if progress is not None and project_index is not None:
            progress.emit(
                "local_project_phase",
                index=project_index,
                phase="godot_validation",
                phase_status="running",
                command=command,
                validation_attempt=1,
                validation_mode="project_import",
                validation_timeout_seconds=timeout,
                validation_idle_timeout_seconds=idle_timeout,
                eta_status="calculating",
                message="Godot imports and checks an isolated working copy. On a detected Mono/editor hang, the process automatically switches to a per-file parser check.",
            )

        def _on_start(pid: int) -> None:
            _write_active_validation(record_path, {
                "format": "godot-coder-active-validation",
                "format_version": 2,
                "pid": pid,
                "workspace": str(workspace),
                "project": str(project),
                "command": command,
                "started_at": time.time(),
                "attempt": 1,
                "mode": "project_import",
            })

        def _on_line(line: str) -> None:
            safe = str(mask_secrets(line))
            lines.append(safe)
            print(f"GODOT_VALIDATION_OUTPUT attempt=1 {safe}", flush=True)

        def _abort_on_line(line: str) -> str | None:
            nonlocal infrastructure_failure, error_count
            detected = _known_editor_failure(line)
            if detected:
                infrastructure_failure = detected
                return detected
            # Detect Godot stuck in an error loop (e.g. broken addon resources).
            # If Godot produces >N consecutive error lines without showing any
            # import progress, the editor is likely looping on unresolvable
            # resources and will never finish.
            clean = _strip_ansi(line)
            is_error = bool(_ERROR_LINE_RE.search(clean))
            is_progress = bool(_PROGRESS_LINE_RE.search(clean))
            if is_error and not is_progress:
                error_count += 1
            elif is_progress:
                error_count = 0
            if error_count > _error_abort_threshold():
                return (
                    f"Godot --import produced {error_count} consecutive error lines "
                    f"without progress; likely stuck on broken addon resources or "
                    f"unresolvable dependencies. Aborting to trigger parser fallback."
                )
            return None

        def _on_heartbeat(elapsed: float, last_line: str | None) -> None:
            if not progress or not project_index:
                return
            detail = _strip_ansi(str(mask_secrets(last_line or "no new technical output yet")))
            if len(detail) > 180:
                detail = "…" + detail[-179:]
            progress.emit(
                "local_project_progress",
                index=project_index,
                phase="godot_validation",
                phase_status="running",
                validation_mode="project_import",
                eta_status="calculating",
                validation_attempt=1,
                phase_elapsed_seconds=round(elapsed, 1),
                message=f"Godot project import running for {_format_elapsed(elapsed)} · {detail}",
            )

        result = run_managed_process(
            command,
            cwd=validation_root,
            env=os.environ.copy(),
            timeout_seconds=timeout,
            idle_timeout_seconds=idle_timeout,
            heartbeat_seconds=5.0,
            on_line=_on_line,
            on_heartbeat=_on_heartbeat,
            on_start=_on_start,
            abort_on_line=_abort_on_line,
        )
        record_path.unlink(missing_ok=True)
        output = "\n".join(lines or [result.output])

        if result.startup_error:
            error = f"Godot could not be started: {result.startup_error}"
            validation = ProjectValidationResult("failed", error, output, mode="project_import", failure_kind=FailureKind.STARTUP_ERROR)
        else:
            first = _first_error(output)
            passed = (
                not result.timed_out
                and not result.aborted
                and result.return_code == 0
                and first is None
            )
            if passed:
                validation = ProjectValidationResult(
                    "passed", None, output,
                    checked_files=len(_validation_script_paths(workspace)),
                    mode="project_import",
                    failure_kind=classify_failure(result),
                )
            else:
                if infrastructure_failure:
                    reason = infrastructure_failure
                elif result.idle_timed_out:
                    reason = f"Godot produced no new output for {idle_timeout:.0f} seconds."
                elif result.timed_out:
                    reason = f"The Godot project import exceeded the time limit of {timeout:.0f} seconds."
                elif first:
                    reason = f"The full project import reported: {_strip_ansi(first)}"
                elif result.aborted:
                    reason = result.abort_reason or "The Godot project import was stopped in a controlled manner."
                else:
                    reason = f"Godot ended the project import with exit code {result.return_code}."
                if progress is not None and project_index is not None:
                    progress.emit(
                        "local_project_phase",
                        index=project_index,
                        phase="godot_validation",
                        phase_status="passed_with_warnings",
                        validation_mode="project_import",
                        validation_fallback=True,
                        eta_status="calculating",
                        validation_process_tree_terminated=result.termination_attempted,
                        message=f"{reason} The process tree was terminated; the safe GDScript parser fallback check now starts.",
                        level="warning",
                    )
                fallback = _run_gdscript_parser_fallback(
                    godot,
                    workspace,
                    progress=progress,
                    project_index=project_index,
                    active_record_path=record_path,
                    reason=reason,
                    static_warning_paths=static_warning_paths,
                )
                fallback.output = output + "\n" + fallback.output
                validation = fallback

        if progress is not None and project_index is not None:
            accepted = validation.status in {"passed", "passed_with_warnings"}
            failed_count = len(validation.failed_files)
            progress.emit(
                "local_project_phase",
                index=project_index,
                phase="godot_validation",
                phase_status="passed" if validation.status == "passed" else ("passed_with_warnings" if accepted else "failed"),
                project_status="running" if accepted else "failed",
                validation_status=validation.status,
                validation_mode=validation.mode,
                validation_fallback=validation.mode == "gdscript_check",
                validation_infrastructure_failure=validation.infrastructure_failure,
                parser_checked_files=validation.checked_files,
                parser_failed_files=validation.failed_files,
                file_index=validation.checked_files if validation.mode == "gdscript_check" else None,
                file_total=validation.checked_files if validation.mode == "gdscript_check" else None,
                passed=max(0, validation.checked_files - failed_count) if validation.checked_files else None,
                failed=failed_count,
                eta_status="calculating",
                parser_output=validation.output[-6000:],
                message=(
                    "Godot project import and parser check passed."
                    if validation.status == "passed"
                    else (
                        f"Safe parser fallback check finished: {validation.checked_files - failed_count}/{validation.checked_files} scripts passed; {failed_count} excluded."
                        if accepted else str(validation.error)
                    )
                ),
                level="info" if validation.status == "passed" else ("warning" if accepted else "error"),
            )
        return validation
    finally:
        _cleanup_workspace()

def _registry_source(audit: ProjectAudit, source_id: str) -> dict[str, Any]:
    excluded = list(dict.fromkeys(["addons", *audit.oversized_gd_files, *audit.parser_failed_files]))
    return {
        "id": source_id,
        "title": f"Private · {audit.project_name}",
        "description": "A local Godot project confirmed by the user. Train locally only; do not redistribute.",
        "url": f"local://{source_id}",
        "branch": audit.source_sha256[:16],
        "kind": "godot_projects",
        "license": LOCAL_LICENSE,
        "license_scope": "user-owned-private-source-code",
        "attribution": "Local user-owned project",
        "catalog_tier": "local-private",
        "verified": True,
        "owner_confirmed": audit.ownership_confirmed,
        "redistribution_allowed": False,
        "enabled": audit.enabled_for_training,
        "beginner_recommended": False,
        "split_policy": "train",
        "exclude_paths": excluded,
    }


def _import_one(
    project_root: Path,
    project: Path,
    source_item: Path,
    *,
    ownership_confirmed: bool,
    progress: ImportProgress | None = None,
    project_index: int | None = None,
) -> ProjectAudit:
    audit = audit_project(
        project,
        source_item,
        ownership_confirmed=ownership_confirmed,
        progress=progress,
        project_index=project_index,
    )
    if progress is not None and project_index is not None:
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="deduplication",
            phase_status="running",
            message="Matching the project fingerprint against the existing local source.",
        )
    source_id = f"local-{_slug(audit.project_name)}-{audit.source_sha256[:8]}"
    destination = corpus_root(project_root) / "downloads" / source_id
    existing = destination.exists()
    if progress is not None and project_index is not None:
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="deduplication",
            phase_status="completed",
            message="The existing source is updated deterministically." if existing else "No identical local source exists.",
        )
    _copy_project(
        project, destination, progress=progress, project_index=project_index, reuse_existing=existing
    )
    # Collect static warning paths to skip Godot per-file checks on clean scripts
    static_warned: set[str] = set()
    for entry in audit.static_warnings:
        path_value = entry.get("path")
        if isinstance(path_value, str):
            static_warned.add(path_value)

    validation_parameters = inspect.signature(_validate_project).parameters
    validation_kwargs: dict[str, Any] = {}
    if "progress" in validation_parameters:
        validation_kwargs["progress"] = progress
    if "project_index" in validation_parameters:
        validation_kwargs["project_index"] = project_index
    if "workspace_root" in validation_parameters:
        validation_kwargs["workspace_root"] = project_root / "reports" / "local_sources" / "validation_work"
    if "active_record_path" in validation_parameters:
        validation_kwargs["active_record_path"] = project_root / "reports" / "local_sources" / "active_validation.json"
    if "static_warning_paths" in validation_parameters:
        validation_kwargs["static_warning_paths"] = static_warned
    validation_result = _validate_project(destination, **validation_kwargs)
    validation_status, validation_error, validation_output = validation_result
    parser_failed_files = list(getattr(validation_result, "failed_files", []) or [])
    parser_checked_files = int(getattr(validation_result, "checked_files", 0) or 0)
    validation_mode = str(getattr(validation_result, "mode", "project_import") or "project_import")
    audit.validation_status = str(validation_status)
    audit.validation_error = validation_error
    audit.parser_checked_files = parser_checked_files
    audit.parser_failed_files = parser_failed_files
    audit.validation_mode = validation_mode
    audit.trainable_gd_files = max(0, audit.trainable_gd_files - len(parser_failed_files))
    audit.imported_source_id = source_id
    audit.imported_path = str(destination)
    audit.enabled_for_training = bool(
        ownership_confirmed
        and not audit.secret_hits
        and validation_status in {"passed", "passed_with_warnings"}
        and audit.trainable_gd_files > 0
    )
    if progress is not None and project_index is not None:
        has_warnings = bool(audit.static_warnings or audit.parser_failed_files or audit.validation_status == "passed_with_warnings")
        project_status = "passed_with_warnings" if audit.enabled_for_training and has_warnings else "passed"
        if not audit.enabled_for_training:
            project_status = "quarantined" if audit.gd_files > 0 else "disabled"
        progress.emit(
            "local_project_phase",
            index=project_index,
            phase="quarantine_decision",
            phase_status=project_status,
            project_status=project_status,
            validation_status=validation_status,
            enabled_for_training=audit.enabled_for_training,
            passed=max(0, audit.gd_files - len({item.get('path') for item in audit.static_warnings if item.get('path')})),
            warnings=len(audit.static_warnings) + len(audit.secret_hits),
            failed=len(audit.parser_failed_files) if audit.enabled_for_training else (1 if validation_status == "failed" else 0),
            quarantined=len(audit.parser_failed_files) if audit.enabled_for_training else audit.gd_files,
            addon_files=audit.addon_files,
            generated_files=audit.generated_files,
            trainable_scripts=audit.trainable_gd_files,
            message=(
                f"Import enabled for training; {len(audit.parser_failed_files)} script(s) with parser errors stay excluded."
                if audit.enabled_for_training and audit.parser_failed_files
                else ("Import enabled for training." if audit.enabled_for_training else "The project stays disabled or quarantined.")
            ),
            level="info" if audit.enabled_for_training else "warning",
        )
    metadata = {
        "url": f"local://{source_id}",
        "ref": audit.source_sha256[:16],
        "commit": audit.source_sha256,
        "updated_at": time.time(),
        "local_private": True,
        "owner_confirmed": ownership_confirmed,
        "redistribution_allowed": False,
        "source_item": source_item.name,
        "license_verification": {
            "verified": ownership_confirmed,
            "declared": LOCAL_LICENSE,
            "file": None,
            "license_file": None,
            "reason_code": None if ownership_confirmed else "ownership-not-confirmed",
            "checked_at": time.time(),
        },
        "local_audit": asdict(audit),
    }
    _json_write(_source_metadata_path(destination), metadata)
    log_dir = project_root / "reports" / "local_sources"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{source_id}_godot.log").write_text(validation_output, encoding="utf-8")
    return audit



def import_inbox(project_root: Path, *, ownership_confirmed: bool) -> dict[str, Any]:
    if not ownership_confirmed:
        raise ValueError("First confirm that you are allowed to use the imported code.")
    inbox = inbox_path(project_root)
    items = sorted(path for path in inbox.iterdir() if path.is_dir() or path.suffix.lower() == ".zip")
    if not items:
        raise FileNotFoundError(f"No ZIPs or project folders in {inbox}")

    emitter = ProgressEmitter()
    _recover_recorded_validation(project_root, emitter)
    emitter.emit(
        "local_import_started",
        job_status="running",
        phase="input_detection",
        phase_label=PHASE_LABELS["input_detection"],
        phase_status="running",
        message=f"{len(items)} import item(s) detected.",
    )
    audits: list[ProjectAudit] = []
    failures: list[dict[str, str]] = []
    plans: list[ImportPlan] = []

    with tempfile.TemporaryDirectory(prefix="godot-local-import-") as temporary:
        temp_root = Path(temporary)
        for item_index, item in enumerate(items, start=1):
            print(f"local_import={item_index}/{len(items)} item={item.name} phase=inspect")
            emitter.emit(
                "local_import_item",
                phase="input_detection",
                phase_label=PHASE_LABELS["input_detection"],
                phase_status="running",
                source_item=item.name,
                source_item_index=item_index,
                source_item_total=len(items),
                message=f"Detecting {item.name}.",
            )
            try:
                if item.is_file():
                    extraction = temp_root / hashlib.sha256(str(item).encode()).hexdigest()[:12]
                    emitter.emit(
                        "local_import_item",
                        phase="secure_extract",
                        phase_label=PHASE_LABELS["secure_extract"],
                        phase_status="running",
                        source_item=item.name,
                        message=f"Safely extracting {item.name}.",
                    )
                    preflight = _archive_preflight(item)
                    _safe_extract(item, extraction)
                    emitter.emit(
                        "local_import_item",
                        phase="secure_extract",
                        phase_label=PHASE_LABELS["secure_extract"],
                        phase_status="completed",
                        source_item=item.name,
                        archive_entries=preflight["entries"],
                        archive_uncompressed_bytes=preflight["uncompressed_bytes"],
                        message=f"Safely extracted {item.name}.",
                    )
                    source_root = extraction
                else:
                    source_root = item
                    emitter.emit(
                        "local_import_item",
                        phase="input_detection",
                        phase_label=PHASE_LABELS["input_detection"],
                        phase_status="completed",
                        source_item=item.name,
                        message="The local folder is only read; original files stay unchanged.",
                    )
                emitter.emit(
                    "local_import_item",
                    phase="project_detection",
                    phase_label=PHASE_LABELS["project_detection"],
                    phase_status="running",
                    source_item=item.name,
                    message="Searching for project.godot files.",
                )
                projects = _find_projects(source_root)
                if not projects:
                    raise ValueError("No project.godot found")
                for project in projects:
                    project_name, _ = _project_name(project)
                    plans.append(ImportPlan(
                        project=project,
                        source_item=item,
                        project_name=project_name,
                        script_count=_quick_script_count(project),
                    ))
                emitter.emit(
                    "local_import_item",
                    phase="project_detection",
                    phase_label=PHASE_LABELS["project_detection"],
                    phase_status="completed",
                    source_item=item.name,
                    detected_projects=len(projects),
                    message=f"{len(projects)} Godot project(s) detected.",
                )
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                failures.append({"item": item.name, "error": str(exc)})
                emitter.emit(
                    "local_import_item",
                    phase="project_detection",
                    phase_label=PHASE_LABELS["project_detection"],
                    phase_status="failed",
                    source_item=item.name,
                    message=str(exc),
                    level="error",
                )
                print(f"local_import item={item.name} phase=failed error={exc}", file=sys.stderr)

        if not plans:
            emitter.emit(
                "local_import_failed",
                job_status="failed",
                phase="project_detection",
                phase_label=PHASE_LABELS["project_detection"],
                phase_status="failed",
                message="No importable Godot project found.",
                level="error",
            )
            raise ValueError("No importable Godot project found")

        progress = ImportProgress(emitter=emitter, plans=plans)
        emitter.emit(
            "local_import_plan",
            job_status="running",
            project_total=len(plans),
            total_scripts=progress.total_scripts,
            projects=[{
                "project_index": index,
                "project_total": len(plans),
                "project_name": plan.project_name,
                "scripts_found": plan.script_count,
                "file_total": plan.script_count,
                "project_status": "waiting",
                "phase_status": "waiting",
                "phases": [],
            } for index, plan in enumerate(plans, start=1)],
            phase="project_detection",
            phase_label=PHASE_LABELS["project_detection"],
            phase_status="completed",
            message=f"{len(plans)} projects planned with {progress.total_scripts} scripts in total.",
        )

        for project_index, plan in enumerate(plans, start=1):
            progress.emit(
                "local_project_started",
                index=project_index,
                phase="project_detection",
                phase_status="completed",
                project_status="running",
                message=f"Started project {project_index}/{len(plans)}.",
            )
            try:
                audit = _import_one(
                    project_root,
                    plan.project,
                    plan.source_item,
                    ownership_confirmed=ownership_confirmed,
                    progress=progress,
                    project_index=project_index,
                )
                audits.append(audit)
                progress.project_finished()
                final_status = (
                    "passed_with_warnings"
                    if audit.enabled_for_training and (audit.static_warnings or audit.parser_failed_files or audit.validation_status == "passed_with_warnings")
                    else "passed"
                )
                if not audit.enabled_for_training:
                    final_status = "quarantined" if audit.gd_files else "disabled"
                progress.emit(
                    "local_project_completed",
                    index=project_index,
                    phase="quarantine_decision",
                    phase_status=final_status,
                    project_status=final_status,
                    validation_status=audit.validation_status,
                    enabled_for_training=audit.enabled_for_training,
                    file_index=audit.gd_files,
                    file_total=audit.gd_files,
                    scripts_found=audit.gd_files,
                    trainable_scripts=audit.trainable_gd_files,
                    passed=max(0, audit.gd_files - len({item.get('path') for item in audit.static_warnings if item.get('path')})),
                    warnings=len(audit.static_warnings) + len(audit.secret_hits),
                    failed=len(audit.parser_failed_files) if audit.enabled_for_training else (1 if audit.validation_status == "failed" else 0),
                    quarantined=len(audit.parser_failed_files) if audit.enabled_for_training else audit.gd_files,
                    addon_files=audit.addon_files,
                    generated_files=audit.generated_files,
                    overall_progress=project_index / len(plans),
                    project_progress=1.0,
                    message=(
                        f"Project import finished; {len(audit.parser_failed_files)} script(s) with parser errors were excluded."
                        if audit.parser_failed_files else "Project import finished."
                    ),
                    level="warning" if final_status in {"passed_with_warnings", "quarantined", "disabled"} else "info",
                )
                print(
                    f"local_project={audit.project_name!r} scripts={audit.gd_files} "
                    f"validation={audit.validation_status} enabled={audit.enabled_for_training}"
                )
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                failures.append({"item": plan.source_item.name, "project": plan.project_name, "error": str(exc)})
                progress.project_finished()
                progress.emit(
                    "local_project_failed",
                    index=project_index,
                    phase="quarantine_decision",
                    phase_status="failed",
                    project_status="failed",
                    failed=1,
                    message=str(exc),
                    level="error",
                )
                print(f"local_project={plan.project_name!r} phase=failed error={exc}", file=sys.stderr)

    emitter.emit(
        "local_import_registry",
        phase="registry_update",
        phase_label=PHASE_LABELS["registry_update"],
        phase_status="running",
        job_status="running",
        project_total=len(plans),
        message="Updating local private sources in the corpus registry.",
    )
    registry = load_registry(project_root)
    by_id = {str(source.get("id")): source for source in registry.get("sources", [])}
    for audit in audits:
        assert audit.imported_source_id
        by_id[audit.imported_source_id] = _registry_source(audit, audit.imported_source_id)
    non_local = [source for source in registry.get("sources", []) if not str(source.get("url", "")).startswith("local://")]
    local_sources = sorted((source for source in by_id.values() if str(source.get("url", "")).startswith("local://")), key=lambda item: item["id"])
    saved = save_registry(project_root, non_local + local_sources)
    emitter.emit(
        "local_import_registry",
        phase="registry_update",
        phase_label=PHASE_LABELS["registry_update"],
        phase_status="completed",
        job_status="running",
        project_total=len(plans),
        message=f"Saved corpus registry with {len(saved.get('sources', []))} sources.",
    )

    report = {
        "format": "godot-coder-local-source-import",
        "format_version": LOCAL_FORMAT_VERSION,
        "progress_event_schema_version": 1,
        "created_at": time.time(),
        "inbox": str(inbox),
        "ownership_confirmed": ownership_confirmed,
        "projects": [asdict(audit) for audit in audits],
        "failures": failures,
        "summary": {
            "items": len(items),
            "projects": len(audits),
            "planned_projects": len(plans),
            "enabled": sum(audit.enabled_for_training for audit in audits),
            "quarantined": sum(not audit.enabled_for_training for audit in audits),
            "failed": len(failures),
            "gd_files": sum(audit.gd_files for audit in audits),
            "gd_bytes": sum(audit.gd_bytes for audit in audits),
            "estimated_bpe_tokens": sum(audit.estimated_bpe_tokens for audit in audits),
            "trainable_estimated_bpe_tokens": sum(audit.trainable_estimated_bpe_tokens for audit in audits),
            "secret_hits": sum(len(audit.secret_hits) for audit in audits),
            "oversized_scripts": sum(len(audit.oversized_gd_files) for audit in audits),
        },
        "registry_sources": len(saved.get("sources", [])),
    }
    emitter.emit(
        "local_import_report",
        phase="report_writing",
        phase_label=PHASE_LABELS["report_writing"],
        phase_status="running",
        job_status="running",
        project_total=len(plans),
        message="Writing JSON and Markdown final reports.",
    )
    _json_write(local_root(project_root) / "import_report.json", report)
    reports = project_root / "reports" / "local_sources"
    reports.mkdir(parents=True, exist_ok=True)
    _json_write(reports / "import_latest.json", report)
    lines = [
        "# Local Godot Project Import", "",
        f"- Projects: {report['summary']['projects']}",
        f"- Enabled for training: {report['summary']['enabled']}",
        f"- Quarantined: {report['summary']['quarantined']}",
        f"- Failed: {report['summary']['failed']}",
        f"- GDScript files: {report['summary']['gd_files']}",
        f"- Estimated BPE tokens: {report['summary']['estimated_bpe_tokens']:,}",
        f"- Estimated trainable BPE tokens: {report['summary']['trainable_estimated_bpe_tokens']:,}",
        f"- Secret hits: {report['summary']['secret_hits']}",
        "",
    ]
    for audit in audits:
        lines.extend([
            f"## {audit.project_name}",
            f"- Source: `{audit.source_item}`",
            f"- Scripts / lines: {audit.gd_files} / {audit.gd_lines:,}",
            f"- Trainable scripts / estimated tokens: {audit.trainable_gd_files} / {audit.trainable_estimated_bpe_tokens:,}",
            f"- Validation: {audit.validation_status}",
            f"- Training enabled: {audit.enabled_for_training}",
            f"- Add-on files excluded from training: {audit.addon_files}",
            f"- Generated/cache files excluded: {audit.generated_files}",
            f"- Oversized scripts excluded: {len(audit.oversized_gd_files)}",
            f"- Static warnings: {len(audit.static_warnings)}",
            "",
        ])
    (reports / "import_latest.md").write_text("\n".join(lines), encoding="utf-8")
    emitter.emit(
        "local_import_completed",
        phase="report_writing",
        phase_label=PHASE_LABELS["report_writing"],
        phase_status="completed",
        job_status="completed",
        project_index=len(plans),
        project_total=len(plans),
        overall_progress=1.0,
        passed=sum(audit.enabled_for_training for audit in audits),
        warnings=sum(bool(audit.static_warnings) for audit in audits),
        failed=len(failures),
        quarantined=sum(not audit.enabled_for_training for audit in audits),
        message="Private project import and final report are done.",
        level="warning" if failures or any(not audit.enabled_for_training for audit in audits) else "info",
    )
    print("LOCAL_IMPORT_SUMMARY_JSON=" + json.dumps(report["summary"], ensure_ascii=True))
    return report



def status(project_root: Path) -> dict[str, Any]:
    inbox = inbox_path(project_root)
    report_path = local_root(project_root) / "import_report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    except (OSError, ValueError):
        report = None
    items = []
    for path in sorted(inbox.iterdir()):
        if path.is_dir() or path.suffix.lower() == ".zip":
            items.append({"name": path.name, "kind": "folder" if path.is_dir() else "zip", "size_bytes": path.stat().st_size if path.is_file() else None})
    return {"inbox": str(inbox), "inbox_items": items, "report": report}


def open_inbox(project_root: Path) -> None:
    path = inbox_path(project_root)
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
        return
    for command in (["xdg-open", str(path)], ["open", str(path)]):
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue
    raise RuntimeError(f"Could not open folder: {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Securely import private user-owned Godot projects")
    parser.add_argument("--root", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    import_command = sub.add_parser("import")
    import_command.add_argument("--confirm-owned", action="store_true")
    sub.add_parser("status")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    if args.command == "import":
        import_inbox(root, ownership_confirmed=args.confirm_owned)
    else:
        print(json.dumps(status(root), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
