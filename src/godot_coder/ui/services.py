from __future__ import annotations

import gc
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import threading
import uuid
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import torch
import yaml

from .. import __version__
from ..autotune import normalize_autotuned_config
from ..checkpoint import load_checkpoint
from ..config import ModelConfig
from ..godot_cli import build_check_command
from ..model import TinyGPT
from ..process_control import run_managed_process
from ..metrics import MetricEvent, MetricsCollector
from ..runtime import mps_available, resolve_device, rocm_available
from ..sampling import DEFAULT_REPETITION_PENALTY, DEFAULT_TEMPERATURE, DEFAULT_TOP_K, DEFAULT_TOP_P
from ..tokenizer import TokenizerLike, load_tokenizer
from .paths import safe_child


EDITABLE_SUFFIXES = {".gd", ".txt", ".md"}


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _analytical_parameter_count(model: dict[str, Any], vocab_size: int) -> int:
    d_model = int(model.get("d_model", 0))
    d_ff = int(model.get("d_ff", 0))
    n_layers = int(model.get("n_layers", 0))
    tied = bool(model.get("tie_embeddings", True))
    embeddings = vocab_size * d_model
    per_block = 4 * d_model * d_model + 3 * d_model * d_ff + 2 * d_model
    final_norm = d_model
    output = 0 if tied else vocab_size * d_model
    return embeddings + n_layers * per_block + final_norm + output


def list_configs(project_root: Path) -> list[dict[str, Any]]:
    normalize_autotuned_config(project_root)
    result: list[dict[str, Any]] = []
    for path in sorted((project_root / "configs").glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raise TypeError(f"Configuration root must be a mapping, got {type(raw).__name__}")
            model = raw.get("model", {})
            train = raw.get("train", {})
            profile = raw.get("profile", {}) or {}
            tokenizer_relative = str(train.get("tokenizer_path", "artifacts/tokenizer.json"))
            tokenizer_path = project_root / tokenizer_relative
            vocab_size = int(profile.get("probe_vocab_size", model.get("vocab_size", 269)))
            if tokenizer_path.exists():
                try:
                    vocab_size = load_tokenizer(tokenizer_path).vocab_size
                except (OSError, ValueError, TypeError):
                    pass
            batch_size = int(train.get("batch_size", 0))
            accumulation = int(train.get("gradient_accumulation_steps", 0))
            max_seq_len = int(model.get("max_seq_len", 0))
            generated_profile = bool(profile.get("generated", False)) or path.stem == "autotuned_night"
            result.append(
                {
                    "path": relative_posix(path, project_root),
                    "name": path.stem,
                    "max_steps": int(train.get("max_steps") or 0),
                    "max_tokens": train.get("max_tokens"),
                    "target_dataset_passes": train.get("target_dataset_passes"),
                    "output_dir": str(train.get("output_dir", "")),
                    "batch_size": batch_size,
                    "gradient_accumulation_steps": accumulation,
                    "effective_batch_sequences": batch_size * accumulation,
                    "tokens_per_optimizer_step": batch_size * accumulation * max_seq_len,
                    "max_seq_len": max_seq_len,
                    "n_layers": int(model.get("n_layers", 0)),
                    "d_model": int(model.get("d_model", 0)),
                    "d_ff": int(model.get("d_ff", 0)),
                    "n_heads": int(model.get("n_heads", 0)),
                    "parameters": _analytical_parameter_count(model, vocab_size),
                    "vocab_size": vocab_size,
                    "dtype": str(train.get("dtype", "float32")),
                    "gradient_checkpointing": bool(model.get("gradient_checkpointing", False)),
                    "tokenizer_ready": tokenizer_path.exists(),
                    "data_ready": (project_root / str(train.get("data_dir", "data/processed")) / "manifest.json").exists(),
                    "profile_id": profile.get("id"),
                    "profile_title": profile.get("title"),
                    "profile_method": profile.get("method"),
                    "profile_description": profile.get("description"),
                    "profile_order": int(profile.get("beginner_order", 999)),
                    "profile_recommended": bool(profile.get("recommended", False)),
                    "profile_risk": profile.get("risk"),
                    "profile_generated": generated_profile,
                    "profile_base_id": profile.get("base_id"),
                }
            )
        except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
            result.append({"path": relative_posix(path, project_root), "name": path.stem, "error": str(exc)})
    return sorted(result, key=lambda item: (0 if item.get("profile_id") else 1, item.get("profile_order", 999), item["name"]))


def list_checkpoints(project_root: Path) -> list[dict[str, Any]]:
    checkpoint_root = project_root / "checkpoints"
    if not checkpoint_root.exists():
        return []
    result: list[dict[str, Any]] = []
    for path in checkpoint_root.rglob("*.pt"):
        stat = path.stat()
        step = None
        run = path.parent.name
        if path.stem.startswith("step_"):
            try:
                step = int(path.stem.split("_", 1)[1])
            except ValueError:
                pass
        result.append(
            {
                "path": relative_posix(path, project_root),
                "name": path.name,
                "run": run,
                "step": step,
                "kind": "best" if path.name == "best.pt" else "latest" if path.name == "latest.pt" else "step",
                "size_mb": round(stat.st_size / 1024**2, 2),
                "modified_at": stat.st_mtime,
            }
        )
    kind_order = {"best": 0, "latest": 1, "step": 2}
    return sorted(result, key=lambda item: (item["run"], kind_order[item["kind"]], -(item["step"] or 0)))


def _raw_dataset_files(project_root: Path) -> list[Path]:
    data_root = project_root / "data" / "raw"
    if not data_root.exists():
        return []
    return [
        path for path in sorted(data_root.rglob("*"), key=lambda item: item.as_posix().lower())
        if path.is_file() and path.suffix.lower() in EDITABLE_SUFFIXES
    ]


def list_dataset_files(project_root: Path) -> list[dict[str, Any]]:
    """Compatibility view containing editable raw files only."""
    result: list[dict[str, Any]] = []
    for path in _raw_dataset_files(project_root):
        stat = path.stat()
        result.append({
            "path": relative_posix(path, project_root),
            "storage_path": relative_posix(path, project_root),
            "name": path.name,
            "suffix": path.suffix.lower(),
            "size": stat.st_size,
            "modified_at": stat.st_mtime,
            "editable": True,
            "deletable": True,
            "kind": "raw",
            "split": None,
            "tokens": None,
        })
    return result


def _processed_manifest_candidates(project_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    result: list[tuple[Path, dict[str, Any]]] = []
    processed_root = project_root / "data" / "processed"
    if not processed_root.exists():
        return result
    for path in processed_root.rglob("manifest.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if payload.get("format") not in {None, "godot-coder-token-stream"}:
            continue
        if not any(key in payload for key in ("train_tokens", "splits", "dataset_fingerprint")):
            continue
        result.append((path, payload))
    return result


def _manifest_score(item: tuple[Path, dict[str, Any]]) -> tuple[int, int, float]:
    path, payload = item
    corpus_bias = 1 if "corpus" in path.as_posix().lower() else 0
    tokens = int(payload.get("train_tokens") or 0)
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0.0
    return corpus_bias, tokens, modified


def read_manifest(project_root: Path) -> dict[str, Any] | None:
    """Return the most relevant prepared token stream, not a stale curriculum manifest."""
    candidates = _processed_manifest_candidates(project_root)
    if not candidates:
        return None
    path, payload = max(candidates, key=_manifest_score)
    result = dict(payload)
    result["manifest_path"] = relative_posix(path, project_root)
    result["manifest_modified_at"] = path.stat().st_mtime
    return result


def _resolve_document_path(project_root: Path, relative_path: str) -> Path | None:
    relative = Path(*Path(relative_path.replace("\\", "/")).parts)
    candidates = (
        project_root / "data" / "corpus" / "audited" / relative,
        project_root / "data" / "corpus" / "prepared" / relative,
        project_root / "data" / "raw" / relative,
        project_root / "data" / "raw" / "curriculum_v03" / relative,
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _instruction_entries(project_root: Path) -> tuple[list[dict[str, Any]], int]:
    entries: list[dict[str, Any]] = []
    total = 0
    instruction_root = project_root / "data" / "instructions"
    if not instruction_root.exists():
        return entries, total
    report_counts: dict[str, int] = {}
    report_path = project_root / "data" / "corpus" / "instruction_report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report_counts = {str(key): int(value) for key, value in (report.get("counts") or {}).items()}
    except (OSError, ValueError, TypeError):
        report_counts = {}
    for path in sorted(instruction_root.rglob("*.jsonl")):
        split = path.stem if path.stem in {"train", "val", "test"} else None
        count = report_counts.get(split, -1) if split else -1
        if count < 0:
            count = 0
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        if line.strip():
                            count += 1
            except OSError:
                continue
        total += count
        entries.append({
            "path": relative_posix(path, project_root),
            "storage_path": relative_posix(path, project_root),
            "name": path.name,
            "suffix": path.suffix.lower(),
            "size": path.stat().st_size,
            "modified_at": path.stat().st_mtime,
            "editable": False,
            "deletable": False,
            "kind": "instruction",
            "split": split,
            "tokens": None,
            "tasks": count,
            "status": "generated",
        })
    return entries, total


def _latest_input_mtime(project_root: Path) -> float:
    latest = 0.0
    change_state = project_root / "data" / "data_lab_state.json"
    if change_state.is_file():
        try:
            latest = max(latest, change_state.stat().st_mtime)
        except OSError:
            pass
    for base in (project_root / "data" / "raw", project_root / "data" / "corpus" / "audited"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            try:
                latest = max(latest, path.stat().st_mtime)
            except OSError:
                continue
    return latest


def build_data_catalog(project_root: Path) -> dict[str, Any]:
    """Build the live Data Lab view from the actual prepared training manifest."""
    manifest = read_manifest(project_root)
    entries: list[dict[str, Any]] = []
    represented: set[str] = set()
    split_totals = {"train": 0, "val": 0, "test": 0}
    if manifest:
        for split in ("train", "val", "test"):
            split_meta = (manifest.get("splits") or {}).get(split) or {}
            split_totals[split] = int(split_meta.get("tokens") or manifest.get(f"{split}_tokens") or 0)
            for document in split_meta.get("documents", []):
                rel = str(document.get("path") or "")
                storage = _resolve_document_path(project_root, rel)
                storage_relative = relative_posix(storage, project_root) if storage else None
                if storage_relative:
                    represented.add(storage_relative)
                entries.append({
                    "path": f"training/{split}/{rel}",
                    "storage_path": storage_relative,
                    "name": Path(rel).name,
                    "suffix": Path(rel).suffix.lower(),
                    "size": storage.stat().st_size if storage else None,
                    "modified_at": storage.stat().st_mtime if storage else None,
                    "editable": bool(storage_relative and storage_relative.startswith("data/raw/")),
                    "deletable": bool(storage_relative and storage_relative.startswith("data/raw/")),
                    "kind": "training",
                    "split": split,
                    "tokens": int(document.get("tokens") or 0),
                    "shard": document.get("shard"),
                    "offset": document.get("offset"),
                    "global_offset": document.get("global_offset"),
                    "status": "active",
                })
    audited_root = project_root / "data" / "corpus" / "audited"
    if audited_root.exists():
        for path in sorted(audited_root.rglob("*.gd"), key=lambda item: item.as_posix().lower()):
            storage_relative = relative_posix(path, project_root)
            if storage_relative in represented:
                continue
            relative = path.relative_to(audited_root).as_posix()
            split = relative.split("/", 1)[0] if "/" in relative else None
            if split not in {"train", "val", "test"}:
                split = None
            stat = path.stat()
            entries.append({
                "path": f"training/pending/{relative}",
                "storage_path": storage_relative,
                "name": path.name,
                "suffix": path.suffix.lower(),
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "editable": False,
                "deletable": False,
                "kind": "training",
                "split": split,
                "tokens": None,
                "status": "pending",
            })
    for raw in list_dataset_files(project_root):
        if raw["storage_path"] in represented:
            continue
        raw["status"] = "pending" if manifest else "raw"
        entries.append(raw)
    instructions, instruction_tasks = _instruction_entries(project_root)
    entries.extend(instructions)
    manifest_modified = float((manifest or {}).get("manifest_modified_at") or 0)
    latest_input = _latest_input_mtime(project_root)
    stale = bool(manifest and latest_input > manifest_modified + 0.001)
    revision_payload = [
        str((manifest or {}).get("dataset_fingerprint") or "none"),
        str(manifest_modified), str(latest_input),
        *[f"{item.get('path')}:{item.get('modified_at')}:{item.get('tokens')}" for item in entries],
    ]
    revision = hashlib.sha256("\n".join(revision_payload).encode("utf-8")).hexdigest()[:20]
    return {
        "revision": revision,
        "stale": stale,
        "manifest": manifest,
        "entries": entries,
        "summary": {
            "entries": len(entries),
            "raw_files": sum(item.get("kind") == "raw" for item in entries),
            "training_documents": sum(item.get("kind") == "training" and item.get("status") == "active" for item in entries),
            "pending_documents": sum(item.get("kind") in {"training", "raw"} and item.get("status") == "pending" for item in entries),
            "instruction_files": sum(item.get("kind") == "instruction" for item in entries),
            "instruction_tasks": instruction_tasks,
            "train_tokens": split_totals["train"],
            "val_tokens": split_totals["val"],
            "test_tokens": split_totals["test"],
            "total_tokens": sum(split_totals.values()),
        },
    }


def _allowed_data_file(project_root: Path, relative_path: str) -> tuple[Path, bool]:
    path = safe_child(project_root, relative_path, must_exist=True)
    roots = (
        ((project_root / "data" / "raw").resolve(), True),
        ((project_root / "data" / "corpus" / "audited").resolve(), False),
        ((project_root / "data" / "corpus" / "prepared").resolve(), False),
        ((project_root / "data" / "instructions").resolve(), False),
    )
    for allowed_root, editable in roots:
        try:
            path.relative_to(allowed_root)
            return path, editable
        except ValueError:
            continue
    raise ValueError("file is outside the Data Lab roots")


def read_dataset_file(project_root: Path, relative_path: str) -> dict[str, Any]:
    path, editable = _allowed_data_file(project_root, relative_path)
    if path.suffix.lower() not in EDITABLE_SUFFIXES | {".jsonl", ".json"}:
        raise ValueError("unsupported Data Lab file type")
    return {
        "path": relative_posix(path, project_root),
        "content": path.read_text(encoding="utf-8"),
        "editable": editable,
        "deletable": editable,
    }


def _mark_data_changed(project_root: Path, action: str, relative_path: str) -> None:
    state_path = project_root / "data" / "data_lab_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"changed_at": time.time(), "action": action, "path": relative_path, "derived_data_stale": True}
    state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_dataset_file(project_root: Path, relative_path: str, content: str) -> dict[str, Any]:
    allowed_root = (project_root / "data" / "raw").resolve()
    path = safe_child(project_root, relative_path)
    try:
        path.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("files can only be saved under data/raw") from exc
    if path.suffix.lower() not in EDITABLE_SUFFIXES:
        raise ValueError("unsupported editable file type")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup_dir = project_root / ".studio_backups" / "data_lab" / "edited"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path_tag = hashlib.sha256(relative_posix(path, project_root).encode("utf-8")).hexdigest()[:8]
        backup = backup_dir / f"{path.stem}.{path_tag}.{stamp}-{time.time_ns() % 1_000_000_000:09d}{path.suffix}.bak"
        shutil.copy2(path, backup)
    path.write_text(content, encoding="utf-8", newline="\n")
    relative = relative_posix(path, project_root)
    _mark_data_changed(project_root, "write", relative)
    return {"path": relative, "backup": relative_posix(backup, project_root) if backup else None, "bytes": path.stat().st_size}


def delete_dataset_file(project_root: Path, relative_path: str) -> dict[str, Any]:
    path, editable = _allowed_data_file(project_root, relative_path)
    if not editable:
        raise ValueError("only user-editable files under data/raw can be deleted")
    if path.suffix.lower() not in EDITABLE_SUFFIXES:
        raise ValueError("unsupported editable file type")
    relative = relative_posix(path, project_root)
    backup_dir = project_root / ".studio_backups" / "data_lab" / "deleted"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path_tag = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:8]
    backup = backup_dir / f"{path.stem}.{path_tag}.{stamp}-{time.time_ns() % 1_000_000_000:09d}{path.suffix}.deleted"
    shutil.copy2(path, backup)
    path.unlink()
    parent = path.parent
    raw_root = (project_root / "data" / "raw").resolve()
    while parent != raw_root and parent.exists():
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    _mark_data_changed(project_root, "delete", relative)
    return {"deleted": relative, "backup": relative_posix(backup, project_root), "derived_data_stale": True}

def read_curriculum_status(project_root: Path) -> dict[str, Any]:
    root = project_root / "data" / "raw" / "curriculum_v03"
    manifest_path = root / "curriculum_manifest.json"
    validation_path = root / "validation_report.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    validation = json.loads(validation_path.read_text(encoding="utf-8")) if validation_path.exists() else None
    processed_path = project_root / "data" / "processed" / "curriculum_v03" / "manifest.json"
    processed = json.loads(processed_path.read_text(encoding="utf-8")) if processed_path.exists() else None
    return {
        "exists": manifest is not None,
        "root": relative_posix(root, project_root) if root.exists() else "data/raw/curriculum_v03",
        "manifest": manifest,
        "validation": validation,
        "processed": processed,
    }


def read_vram_probe(project_root: Path) -> dict[str, Any] | None:
    path = project_root / "reports" / "hardware" / "vram_probe_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def list_training_reports(project_root: Path, limit: int = 12) -> list[dict[str, Any]]:
    report_root = project_root / "reports" / "training"
    if not report_root.exists():
        return []
    reports: list[dict[str, Any]] = []
    for path in report_root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["report_path"] = relative_posix(path, project_root)
            reports.append(payload)
        except (OSError, ValueError, TypeError):
            continue
    reports.sort(key=lambda item: float(item.get("finished_at") or item.get("started_at") or 0), reverse=True)
    return reports[:limit]


def find_godot() -> str | None:
    for name in ("godot", "godot4", "godot.CMD", "godot.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _compile_status() -> tuple[bool, str | None]:
    """Cheap availability check for torch.compile on this box.

    The autotuner proves compile support with a real probe (profile_probe
    reports compile_enabled). The system panel shows a quick signal instead:
    torch.compile exists and Triton can actually be imported - which is the
    piece that's missing on Windows without the [compile] extra.
    """
    if not hasattr(torch, "compile"):
        return False, None
    try:
        import triton  # noqa: F401
    except ImportError:  # missing extra or broken DLL - both surface as ImportError
        return False, None
    return True, getattr(triton, "__version__", None)


# A probe verdict only stays authoritative for a while - the user may install
# the [compile] extra after a failed run, which the static check would catch.
_AUTOTUNE_PROBE_MAX_AGE_DAYS = 30


def _autotune_compile_signal(project_root: Path) -> tuple[bool | None, str | None]:
    """Best-effort read of the last autotune probe's compile verdict.

    The autotuner proves compile support with a real probe instead of the
    static import check, so its verdict wins when it exists. Returns
    (available_or_None, disabled_reason); both None when no report exists yet
    or the report is too old to trust.
    """
    report = project_root / "reports" / "hardware" / "autotune_latest.json"
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict) or not isinstance(data.get("compile_available"), bool):
        return None, None
    created = data.get("created_at")
    if isinstance(created, (int, float)):
        # autotune.py writes time.time() - a Unix epoch in seconds
        age_days = (time.time() - created) / 86400.0
        if age_days > _AUTOTUNE_PROBE_MAX_AGE_DAYS:
            return None, None
    elif isinstance(created, str):
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(created)
            if age.days > _AUTOTUNE_PROBE_MAX_AGE_DAYS:
                return None, None
        except ValueError:
            return None, None
    reason = data.get("compile_disabled_reason")
    return data["compile_available"], (reason if isinstance(reason, str) and reason else None)


def system_status(project_root: Path) -> dict[str, Any]:
    compile_available, triton_version = _compile_status()
    probe_available, compile_disabled_reason = _autotune_compile_signal(project_root)
    if probe_available is not None:
        # A fresh probe verdict is the ground truth - it actually builds a
        # kernel, which the static import check cannot prove either way.
        compile_available = probe_available
    cuda_available = torch.cuda.is_available()
    mps_present = mps_available()
    rocm_present = rocm_available()
    gpu = None
    if cuda_available:
        props = torch.cuda.get_device_properties(0)
        gpu = {
            "name": torch.cuda.get_device_name(0),
            "compute_capability": ".".join(map(str, torch.cuda.get_device_capability(0))),
            "vram_gib": round(props.total_memory / 1024**3, 2),
        }
    elif mps_present:
        gpu = {
            "name": "Apple Silicon (MPS)",
            "compute_capability": None,
            "vram_gib": None,
        }
    godot = find_godot()
    godot_version = None
    if godot:
        try:
            result = subprocess.run([godot, "--version"], capture_output=True, text=True, timeout=8, check=False)
            godot_version = (result.stdout or result.stderr).strip()
        except (OSError, subprocess.SubprocessError):
            godot_version = None
    return {
        "app_version": __version__,
        "project_root": str(project_root),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "torch_hip": getattr(torch.version, "hip", None),
        "cuda_available": cuda_available,
        "rocm_available": rocm_present,
        "mps_available": mps_present,
        "compile_available": compile_available,
        "compile_disabled_reason": compile_disabled_reason,
        "triton": triton_version,
        "gpu": gpu,
        "godot": godot,
        "godot_version": godot_version,
        "manifest": read_manifest(project_root),
        "checkpoint_count": len(list_checkpoints(project_root)),
        "dataset_file_count": len(list_dataset_files(project_root)),
        "vram_probe": read_vram_probe(project_root),
        "latest_training_report": (list_training_reports(project_root, limit=1) or [None])[0],
    }


@dataclass
class LoadedModel:
    checkpoint: Path
    modified_ns: int
    tokenizer: TokenizerLike
    model: TinyGPT
    device: torch.device


def _normalize_blank_lines(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    return "\n".join(out)


def _collapse_repeated_blocks(text: str) -> str:
    """Cut the completion at the first repeated block.

    Undertrained models fall into repetition loops; keeping only the first
    occurrence of the loop body makes the chat output far more usable.
    Multi-line blocks match exactly; single lines collapse only when clearly
    looping (comments at two repeats, code lines at three) so a legitimate
    identical pair survives.
    """
    lines = text.splitlines()
    n = len(lines)
    max_size = min(n // 2, 64)
    for size in range(max_size, 1, -1):
        for start in range(0, n - size + 1):
            block = lines[start:start + size]
            if lines[start + size:start + 2 * size] == block:
                return "\n".join(lines[:start + size])
    i = 0
    while i < n:
        j = i
        while j < n and lines[j] == lines[i]:
            j += 1
        run = j - i
        line = lines[i]
        if line.strip() and run >= (2 if line.startswith("#") else 3):
            return "\n".join(lines[:i + 1])
        i = j
    return text


def _clean_completion(text: str) -> str:
    """Display-level polish for chat completions before they hit the UI:
    collapse blank-line runs and repetition loops, trim trailing whitespace."""
    return _collapse_repeated_blocks(_normalize_blank_lines(text)).rstrip()


class GenerationService:
    """Caches one checkpoint in memory for responsive local generation."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._loaded: LoadedModel | None = None
        self._lock = threading.RLock()

    def unload(self) -> None:
        """Release cached model VRAM before training or hardware probes."""
        with self._lock:
            loaded = self._loaded
            self._loaded = None
            if loaded is not None:
                loaded.model.to("cpu")
                del loaded
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def generate_stream(
        self,
        checkpoint_path: str,
        prompt: str,
        *,
        max_new_tokens: int,
        temperature: float = DEFAULT_TEMPERATURE,
        top_k: int = DEFAULT_TOP_K,
        top_p: float = DEFAULT_TOP_P,
        repetition_penalty: float = DEFAULT_REPETITION_PENALTY,
        device_name: str = "auto",
    ) -> Iterator[dict[str, Any]]:
        """Yield live token deltas, then a done event with the cleaned text.

        Each yielded event is ``{"token": "<text delta>"}`` while the model
        samples (the KV cache stays alive across the whole stream), and the
        stream ends with ``{"done": True, "text": ..., "tokens": n}``
        carrying the repetition-collapsed final completion. ``generate`` is a
        thin wrapper over this stream.
        """
        if not prompt:
            raise ValueError("prompt cannot be empty")
        if not 1 <= max_new_tokens <= 4096:
            raise ValueError("max_new_tokens must be between 1 and 4096")
        if not 0.0 <= temperature <= 5.0:
            raise ValueError("temperature must be between 0.0 and 5.0")
        if not 0 <= top_k <= 1000:
            raise ValueError("top_k must be between 0 and 1000")
        if not 0.0 < top_p <= 1.0:
            raise ValueError("top_p must be in (0, 1]")
        if repetition_penalty < 1.0:
            raise ValueError("repetition_penalty must be >= 1.0")

        checkpoint = safe_child(self.project_root, checkpoint_path, must_exist=True)
        checkpoint_root = (self.project_root / "checkpoints").resolve()
        try:
            checkpoint.relative_to(checkpoint_root)
        except ValueError as exc:
            raise ValueError("checkpoint must be under checkpoints/") from exc
        with self._lock:
            device = resolve_device(device_name)
            modified_ns = checkpoint.stat().st_mtime_ns
            loaded = self._loaded
            if (
                loaded is None
                or loaded.checkpoint != checkpoint
                or loaded.modified_ns != modified_ns
                or loaded.device != device
            ):
                payload = load_checkpoint(checkpoint, map_location=device)
                configured_path = payload.get("train_config", {}).get("tokenizer_path", "artifacts/tokenizer.json")
                tokenizer_path = Path(configured_path)
                if not tokenizer_path.is_absolute():
                    tokenizer_path = self.project_root / tokenizer_path
                tokenizer = load_tokenizer(tokenizer_path)
                if payload["tokenizer_fingerprint"] != tokenizer.fingerprint():
                    raise ValueError("checkpoint and tokenizer do not match")
                model_config = ModelConfig(**payload["model_config"])
                model = TinyGPT(model_config).to(device)
                model.load_state_dict(payload["model_state"])
                model.eval()
                loaded = LoadedModel(checkpoint, modified_ns, tokenizer, model, device)
                self._loaded = loaded

            prompt_ids = loaded.tokenizer.encode(prompt, add_bos=True)
            input_ids = torch.tensor([prompt_ids], dtype=torch.long, device=loaded.device)
            # The chat panel already shows the user's prompt; only the
            # completion tokens are decoded and streamed.
            accumulated: list[int] = []
            prev_text = ""
            with torch.inference_mode():
                for token in loaded.model.generate_stream(
                    input_ids,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    top_k=top_k,
                    top_p=top_p,
                    repetition_penalty=repetition_penalty,
                    eos_id=loaded.tokenizer.eos_id,
                ):
                    accumulated.append(int(token.item()))
                    text = loaded.tokenizer.decode(accumulated, skip_special_tokens=True)
                    # Byte-level BPE decode is prefix-stable: only the tail
                    # after the previously emitted text is new. Special
                    # tokens decode to "" and are skipped silently.
                    if text.startswith(prev_text) and len(text) > len(prev_text):
                        yield {"token": text[len(prev_text):]}
                    prev_text = text
            raw_text = loaded.tokenizer.decode(accumulated, skip_special_tokens=True)
            gen_metrics = MetricsCollector(self.project_root / "reports" / "studio_metrics.jsonl")
            gen_metrics.record(MetricEvent.TOKEN_USAGE, tokens=len(accumulated))
            gen_metrics.record(MetricEvent.GENERATION_COMPLETE if raw_text else MetricEvent.GENERATION_ERROR)
            yield {"done": True, "text": _clean_completion(raw_text), "tokens": len(accumulated)}

    def generate(
        self,
        checkpoint_path: str,
        prompt: str,
        *,
        max_new_tokens: int,
        temperature: float = DEFAULT_TEMPERATURE,
        top_k: int = DEFAULT_TOP_K,
        top_p: float = DEFAULT_TOP_P,
        repetition_penalty: float = DEFAULT_REPETITION_PENALTY,
        device_name: str = "auto",
    ) -> str:
        """Generate a full completion; a thin wrapper over the live stream."""
        done_text: str | None = None
        for event in self.generate_stream(
            checkpoint_path,
            prompt,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            device_name=device_name,
        ):
            if "done" in event:
                done_text = event["text"]
        if done_text is None:
            raise RuntimeError("generation stream ended without a done event")
        return done_text


def validate_code(project_root: Path, code: str, project_path: str = "data/raw/seed_project") -> dict[str, Any]:
    godot = find_godot()
    if not godot:
        raise FileNotFoundError("Godot executable was not found")
    project = safe_child(project_root, project_path, must_exist=True)
    if not (project / "project.godot").exists():
        raise FileNotFoundError(f"project.godot not found under {project}")
    generated_dir = project_root / "data" / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    script = generated_dir / f"studio_validation_{uuid.uuid4().hex[:12]}.gd"
    script.write_text(code, encoding="utf-8", newline="\n")
    command = build_check_command(godot, project, script)
    try:
        # run_managed_process (not subprocess.run) so a hung Mono-Godot child
        # is terminated as a whole tree instead of surviving the 30s timeout
        # as an orphan - the same protection the corpus path already uses.
        result = run_managed_process(command, timeout_seconds=30)
        validate_metrics = MetricsCollector(project_root / "reports" / "studio_metrics.jsonl")
        validate_metrics.record(
            MetricEvent.PARSE_SUCCESS if result.return_code == 0 and not result.timed_out else MetricEvent.PARSE_ERROR,
            duration_seconds=result.duration_seconds if not result.timed_out else None,
            error=result.output[:200] if result.return_code != 0 else None,
        )
        return {
            "passed": result.return_code == 0,
            "return_code": result.return_code,
            "output": result.output,
            "script": relative_posix(script, project_root),
            "timed_out": result.timed_out,
        }
    finally:
        script.unlink(missing_ok=True)

