from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

from .config import load_config
from .corpus import ALLOWED_LICENSES, corpus_root

AUDIT_FORMAT_VERSION = 1
FILTER_VERSION = "professional-audit-v2"
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[^\s]", re.UNICODE)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _scan_gdscript(text: str, *, mask_strings: bool) -> str:
    """Remove comments safely and optionally mask string contents.

    The previous regex treated ``#`` and delimiters inside strings as code,
    which could create false duplicates and false structural quarantines.
    """
    output: list[str] = []
    index = 0
    length = len(text)
    quote: str | None = None
    triple = False
    escaped = False
    while index < length:
        char = text[index]
        if quote is None:
            if char == "#":
                while index < length and text[index] != "\n":
                    output.append(" ")
                    index += 1
                continue
            if char in {"'", '"'}:
                quote = char
                triple = text[index:index + 3] == char * 3
                count = 3 if triple else 1
                output.extend(" " * count if mask_strings else char * count)
                index += count
                escaped = False
                continue
            output.append(char)
            index += 1
            continue

        if triple and text[index:index + 3] == quote * 3:
            output.extend(" " * 3 if mask_strings else quote * 3)
            index += 3
            quote = None
            triple = False
            escaped = False
            continue
        if not triple and char == quote and not escaped:
            output.append(" " if mask_strings else char)
            index += 1
            quote = None
            escaped = False
            continue
        if char == "\n":
            output.append("\n")
            if not triple:
                quote = None
            escaped = False
            index += 1
            continue
        output.append(" " if mask_strings else char)
        if char == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
        index += 1
    return "".join(output)


def _normalize_for_duplicate(text: str) -> str:
    text = _scan_gdscript(text.replace("\r\n", "\n").replace("\r", "\n"), mask_strings=False)
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            lines.append(re.sub(r"\s+", " ", line))
    return "\n".join(lines)

def _tokens(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text)]


def _simhash(tokens: list[str]) -> int:
    if not tokens:
        return 0
    features = Counter("\x1f".join(tokens[index:index + 5]) for index in range(max(1, len(tokens) - 4)))
    vector = [0] * 64
    for feature, weight in features.items():
        value = int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big")
        for bit in range(64):
            vector[bit] += weight if value & (1 << bit) else -weight
    result = 0
    for bit, value in enumerate(vector):
        if value >= 0:
            result |= 1 << bit
    return result


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _fragment_reasons(text: str) -> list[str]:
    reasons: list[str] = []
    stripped = text.strip()
    code = _scan_gdscript(text, mask_strings=True)
    code_lines = [line.strip() for line in code.splitlines() if line.strip()]
    if any(line == "..." for line in code_lines):
        reasons.append("placeholder_ellipsis")
    # ``pass`` is valid GDScript. Only flag tiny pass-only teaching stubs as a
    # warning; never treat every legitimate empty callback as damaged code.
    substantive = [
        line for line in code_lines
        if not re.match(r"^(?:@\w+|extends\b|class_name\b|func\b|pass$)", line)
        and not line.endswith(":")
    ]
    if any(line == "pass" for line in code_lines) and len(code_lines) <= 12 and not substantive:
        reasons.append("pass_only_stub")
    if "\x00" in text or "\ufffd" in text:
        reasons.append("encoding_damage")
    if len(stripped) < 24:
        reasons.append("too_short")
    if code.count("(") != code.count(")") or code.count("[") != code.count("]") or code.count("{") != code.count("}"):
        reasons.append("unbalanced_delimiters")
    if re.search(r"(?m)^\s*(?:using System|namespace |public class )", code):
        reasons.append("non_gdscript_fragment")
    return reasons

def _source_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id")): item for item in manifest.get("sources", [])}


def _quality(record: dict[str, Any], source: dict[str, Any], fragment_reasons: list[str]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    license_name = str(record.get("license") or source.get("license") or "UNKNOWN")
    if license_name not in ALLOWED_LICENSES:
        reasons.append("unknown_or_disallowed_license")
    validation = record.get("validation_status")
    if validation == "failed":
        reasons.append("godot_validation_failed")
    if validation == "pending":
        reasons.append("validation_pending")
    reasons.extend(fragment_reasons)
    if any(item in reasons for item in ("unknown_or_disallowed_license", "godot_validation_failed", "encoding_damage", "non_gdscript_fragment", "unbalanced_delimiters", "missing_staged_file")):
        return "quarantine", reasons
    if reasons:
        return "warning", reasons
    return "accepted", reasons


def _replace_directory(temporary: Path, destination: Path) -> None:
    backup = destination.with_name(destination.name + ".previous")
    if backup.exists():
        shutil.rmtree(backup)
    if destination.exists():
        destination.replace(backup)
    try:
        temporary.replace(destination)
    except Exception:
        if backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def audit_corpus(project_root: Path, *, near_duplicate_distance: int = 3) -> dict[str, Any]:
    root = corpus_root(project_root)
    manifest_path = root / "corpus_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("Corpus manifest is missing. Run scan and Godot validation first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_by_id = _source_map(manifest)
    staged = root / "staged"
    audited_work = root / "audited.building"
    audited = root / "audited"
    if audited_work.exists():
        shutil.rmtree(audited_work)
    audited_work.mkdir(parents=True)

    enriched: list[dict[str, Any]] = []
    exact_seen: dict[str, str] = {}
    simhash_buckets: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    near_pairs: list[dict[str, Any]] = []
    group_splits: dict[str, set[str]] = defaultdict(set)
    normalized_hash_splits: dict[str, set[str]] = defaultdict(set)

    total_records = len(manifest.get("records", []))
    snapshot_interval = max(500, total_records // 10)  # Every 500 records or 10% of total
    snapshot_path = root / "audit_checkpoint.json"

    for record_index, record in enumerate(manifest.get("records", []), start=1):
        source = source_by_id.get(str(record.get("source_id")), {})
        path = staged / str(record["staged_path"])
        if not path.exists():
            text = ""
            fragment = ["missing_staged_file"]
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            fragment = _fragment_reasons(text)
        normalized = _normalize_for_duplicate(text)
        normalized_sha = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        lexical = _tokens(normalized)
        simhash = _simhash(lexical)
        quality, reasons = _quality(record, source, fragment)
        duplicate_of = exact_seen.get(normalized_sha)
        if duplicate_of is not None:
            quality = "quarantine"
            reasons.append("normalized_exact_duplicate")
        else:
            exact_seen[normalized_sha] = str(record["record_id"])

        # Bucket by high bits and length band to avoid quadratic comparisons.
        length_band = int(math.log2(max(1, len(lexical))))
        bucket_key = (simhash >> 48) ^ length_band
        near_of: str | None = None
        for other_hash, other_length, other_id in simhash_buckets[bucket_key]:
            ratio = min(len(lexical), other_length) / max(1, max(len(lexical), other_length))
            distance = _hamming(simhash, other_hash)
            if ratio >= 0.80 and distance <= near_duplicate_distance:
                near_of = other_id
                near_pairs.append({"record_id": record["record_id"], "near_duplicate_of": other_id, "hamming": distance, "length_ratio": round(ratio, 4)})
                if quality == "accepted":
                    quality = "warning"
                reasons.append("near_duplicate")
                break
        simhash_buckets[bucket_key].append((simhash, len(lexical), str(record["record_id"])))

        split = str(record.get("split"))
        group_id = str(record.get("group_id"))
        group_splits[group_id].add(split)
        normalized_hash_splits[normalized_sha].add(split)
        enriched_record = {
            **record,
            "source_url": source.get("url"),
            "source_commit": source.get("commit"),
            "source_ref": source.get("branch"),
            "spdx_license": record.get("license") or source.get("license"),
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "normalized_sha256": normalized_sha,
            "simhash64": f"{simhash:016x}",
            "token_estimate": len(lexical),
            "quality_status": quality,
            "quality_reasons": sorted(set(reasons)),
            "near_duplicate_of": near_of,
            "filter_version": FILTER_VERSION,
        }
        enriched.append(enriched_record)

        # Periodic snapshot so a mid-audit crash doesn't lose all progress.
        if record_index % snapshot_interval == 0:
            _atomic_json(snapshot_path, {
                "format": "godot-coder-audit-checkpoint",
                "progress": {"records": record_index, "total": total_records},
                "enriched": enriched,
                "near_pairs": near_pairs,
            })
            print(f"Audit checkpoint: {record_index}/{total_records} records")

    # Clean up checkpoint on success.
    snapshot_path.unlink(missing_ok=True)

    group_leaks = {group: sorted(splits) for group, splits in group_splits.items() if len(splits) > 1}
    content_leaks = {digest: sorted(splits) for digest, splits in normalized_hash_splits.items() if len(splits) > 1}
    if group_leaks or content_leaks:
        for record in enriched:
            if record["group_id"] in group_leaks or record["normalized_sha256"] in content_leaks:
                record["quality_status"] = "quarantine"
                record["quality_reasons"] = sorted(set(record["quality_reasons"] + ["split_leakage"]))

    accepted_records = [record for record in enriched if record["quality_status"] in {"accepted", "warning"}]
    for record in accepted_records:
        source = staged / str(record["staged_path"])
        if not source.exists():
            continue
        destination = audited_work / str(record["split"]) / str(record["source_id"]) / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        record["audited_path"] = destination.relative_to(audited_work).as_posix()
    _replace_directory(audited_work, audited)

    statuses = Counter(record["quality_status"] for record in enriched)
    splits = Counter(record["split"] for record in accepted_records)
    projects = {split: len({record["group_id"] for record in accepted_records if record["split"] == split}) for split in ("train", "val", "test")}
    parse_candidates = [record for record in enriched if record.get("validation_status") in {"passed", "failed"}]
    parser_passed = sum(record.get("validation_status") == "passed" for record in parse_candidates)
    parser_rate = parser_passed / len(parse_candidates) if parse_candidates else 1.0
    source_commits = {item.get("id"): item.get("commit") for item in manifest.get("sources", [])}
    reason_counts = Counter(reason for item in enriched for reason in item.get("quality_reasons", []))
    source_quality: dict[str, dict[str, int]] = {}
    for source_id in sorted({str(item.get("source_id")) for item in enriched}):
        source_records = [item for item in enriched if str(item.get("source_id")) == source_id]
        source_quality[source_id] = {
            "records": len(source_records),
            "accepted": sum(item["quality_status"] == "accepted" for item in source_records),
            "warning": sum(item["quality_status"] == "warning" for item in source_records),
            "quarantine": sum(item["quality_status"] == "quarantine" for item in source_records),
        }
    corpus_fingerprint = hashlib.sha256(json.dumps({
        "records": [(item["record_id"], item["normalized_sha256"], item["quality_status"], item["split"]) for item in enriched],
        "sources": source_commits,
        "filter": FILTER_VERSION,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    report = {
        "format": "godot-coder-corpus-audit",
        "format_version": AUDIT_FORMAT_VERSION,
        "created_at": time.time(),
        "filter_version": FILTER_VERSION,
        "corpus_fingerprint": corpus_fingerprint,
        "source_commits": source_commits,
        "summary": {
            "records": len(enriched),
            "accepted": statuses["accepted"],
            "warning": statuses["warning"],
            "quarantine": statuses["quarantine"],
            "accepted_train": splits["train"],
            "accepted_val": splits["val"],
            "accepted_test": splits["test"],
            "train_projects": projects["train"],
            "val_projects": projects["val"],
            "test_projects": projects["test"],
            "parser_passed": parser_passed,
            "parser_checked": len(parse_candidates),
            "parser_pass_rate": round(parser_rate, 6),
            "exact_duplicates": sum("normalized_exact_duplicate" in item["quality_reasons"] for item in enriched),
            "near_duplicates": len(near_pairs),
            "group_leaks": len(group_leaks),
            "content_leaks": len(content_leaks),
            "accepted_bytes": sum(int(item.get("bytes", 0)) for item in accepted_records),
            "estimated_lexical_tokens": sum(int(item.get("token_estimate", 0)) for item in accepted_records),
            "reason_counts": dict(sorted(reason_counts.items())),
        },
        "source_quality": source_quality,
        "sources": manifest.get("sources", []),
        "records": enriched,
        "near_duplicate_pairs": near_pairs,
        "group_leaks": group_leaks,
        "content_leaks": content_leaks,
    }
    _atomic_json(root / "audit_report.json", report)
    _atomic_json(project_root / "reports" / "audit" / "corpus_audit_latest.json", report)
    _atomic_text(project_root / "reports" / "audit" / "corpus_audit_latest.md", _audit_markdown(report))
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return report


def _audit_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    return f"""# Corpus Audit\n\n- Fingerprint: `{report['corpus_fingerprint']}`\n- Accepted: {summary['accepted']}\n- Warnings: {summary['warning']}\n- Quarantine: {summary['quarantine']}\n- Parser pass rate: {summary['parser_pass_rate']:.1%}\n- Exact duplicates: {summary['exact_duplicates']}\n- Near duplicates: {summary['near_duplicates']}\n- Group leaks: {summary['group_leaks']}\n- Content leaks: {summary['content_leaks']}\n- Train/Val/Test projects: {summary['train_projects']} / {summary['val_projects']} / {summary['test_projects']}\n\nFiles with unknown licenses, failed parser checks, damaged encoding, incomplete delimiters, or split leakage are excluded from `data/corpus/audited`.\n"""


MIN_PROFILE_TOKENS = {
    "starter": 2_000_000,
    "balanced": 5_000_000,
    "experimental": 10_000_000,
}
SMOKE_MIN_TOKENS = 50_000


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _select_data_manifest(project_root: Path, config_path: Path | None) -> tuple[Path | None, dict[str, Any] | None, dict[str, Any]]:
    raw_config: dict[str, Any] = {}
    if config_path is not None:
        try:
            raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError, TypeError):
            raw_config = {}
        configured_dir = str((raw_config.get("train") or {}).get("data_dir") or "").strip()
        if configured_dir:
            path = (project_root / configured_dir / "manifest.json").resolve()
            return path, _load_json(path), raw_config
    candidates: list[tuple[Path, dict[str, Any]]] = []
    processed = project_root / "data" / "processed"
    if processed.exists():
        for path in processed.glob("corpus*/manifest.json"):
            payload = _load_json(path)
            if payload is not None:
                candidates.append((path, payload))
    if not candidates:
        return None, None, raw_config
    path, payload = max(candidates, key=lambda item: (int(item[1].get("train_tokens") or 0), item[0].stat().st_mtime))
    return path, payload, raw_config


def _is_corpus_stream(data_manifest_path: Path | None) -> bool:
    """True for datasets derived from the corpus pipeline (data/processed/corpus*).
    Synthetic streams such as the curriculum keep their own raw source."""
    return data_manifest_path is not None and data_manifest_path.parent.name.startswith("corpus")


def _pipeline_freshness(project_root: Path, data_manifest_path: Path | None) -> dict[str, Any]:
    corpus = corpus_root(project_root)
    stage_paths = {
        "scan": corpus / "corpus_manifest.json",
        "validation": corpus / "validation_report.json",
        "audit": corpus / "audit_report.json",
        "tokenizer": corpus / "tokenizer_report.json",
        "data_changes": project_root / "data" / "data_lab_state.json",
    }
    # Corpus pipeline stages are genuine inputs only for corpus-derived streams
    # (data/processed/corpus*). Synthetic datasets such as the curriculum keep
    # their own raw source and must not be judged stale by corpus stages they
    # never depend on; tokenizer drift on those datasets is already covered by
    # the tokenizer-fingerprint gate.
    if not _is_corpus_stream(data_manifest_path):
        raw_source = project_root / "data" / "raw" / data_manifest_path.parent.name
        stage_paths = {"raw_source": raw_source} if raw_source.exists() else {}
    mtimes = {name: path.stat().st_mtime if path.exists() else None for name, path in stage_paths.items()}
    processed_mtime = data_manifest_path.stat().st_mtime if data_manifest_path and data_manifest_path.exists() else None
    newest_input = max((value for value in mtimes.values() if value is not None), default=0.0)
    stale = processed_mtime is not None and newest_input > processed_mtime + 0.001
    return {
        "stale": stale,
        "processed_mtime": processed_mtime,
        "newest_pipeline_input_mtime": newest_input or None,
        "stages": {name: {"exists": value is not None, "modified_at": value} for name, value in mtimes.items()},
    }


def build_preflight(project_root: Path, *, config_path: Path | None = None, mode: str = "full") -> dict[str, Any]:
    if mode not in {"full", "smoke"}:
        raise ValueError("preflight mode must be 'full' or 'smoke'")
    audit_path = project_root / "reports" / "audit" / "corpus_audit_latest.json"
    validation_path = corpus_root(project_root) / "validation_report.json"
    tokenizer_report_path = corpus_root(project_root) / "tokenizer_report.json"
    hardware_path = project_root / "reports" / "hardware" / "autotune_latest.json"
    audit = _load_json(audit_path)
    validation = _load_json(validation_path)
    tokenizer_report = _load_json(tokenizer_report_path)
    hardware = _load_json(hardware_path)
    data_manifest_path, data, raw_config = _select_data_manifest(project_root, config_path)
    freshness = _pipeline_freshness(project_root, data_manifest_path)
    blockers: list[str] = []
    warnings: list[str] = []

    profile_id = str((raw_config.get("profile") or {}).get("id") or "legacy")
    train_mapping = raw_config.get("train") or {}
    tokenizer_path = (project_root / str(train_mapping.get("tokenizer_path") or "artifacts/tokenizer_bpe_godot.json")).resolve()
    configured_data_dir = (project_root / str(train_mapping.get("data_dir") or (data_manifest_path.parent if data_manifest_path else "data/processed/corpus_v06"))).resolve()

    if validation is None:
        blockers.append("Project-based corpus validation missing")
    elif not str(validation.get("validator") or "").startswith("project-aware-v"):
        blockers.append("The corpus has not been checked with project-based validation yet")
    elif int(validation.get("prepared") or 0) <= 0:
        blockers.append("Validation did not prepare any usable files")

    if audit is None:
        blockers.append("Corpus audit missing")
    else:
        summary = audit.get("summary") or {}
        if summary.get("group_leaks"):
            blockers.append("Train/validation/test leakage detected")
        elif summary.get("content_leaks"):
            warnings.append(f"Train/validation/test leakage detected in {summary['content_leaks']} file(s). Affected files were quarantined and are not in the training data.")
        if mode == "full" and (int(summary.get("train_projects") or 0) < 10 or int(summary.get("val_projects") or 0) < 2 or int(summary.get("test_projects") or 0) < 2):
            blockers.append("Too few independent projects in the splits")
        parser_rate = float(summary.get("parser_pass_rate") or 0)
        if parser_rate < 0.65:
            warnings.append("Many files have context or parser issues; check the cause report")
        near_duplicates = int(summary.get("near_duplicates") or 0)
        records = int(summary.get("records") or 0)
        if near_duplicates > max(25, records * 0.20):
            warnings.append("High share of similar files")

    if data is None or data_manifest_path is None:
        blockers.append("Tokenized corpus training data missing")
    else:
        if not configured_data_dir.exists():
            blockers.append("The data folder specified in the configuration is missing")
        if freshness["stale"]:
            blockers.append("The active token stream is older than the validation, audit or source data")
        train_tokens = int(data.get("train_tokens") or 0)
        # Token minimums guard corpus-derived profiles (starter/balanced/
        # experimental). Synthetic streams such as the curriculum are sized
        # deliberately small, so the guard does not apply to them.
        if mode == "smoke":
            minimum_tokens = SMOKE_MIN_TOKENS
        elif _is_corpus_stream(data_manifest_path):
            minimum_tokens = MIN_PROFILE_TOKENS.get(profile_id, 500_000)
        else:
            minimum_tokens = 0
        if train_tokens < minimum_tokens:
            label = "Smoke test" if mode == "smoke" else f"Profile {profile_id}"
            blockers.append(f"{label} needs at least {minimum_tokens:,} fresh training tokens; available are {train_tokens:,}".replace(",", "."))
        if int(data.get("val_tokens") or 0) <= 0:
            blockers.append("Validation split contains no tokens")
        if mode == "full" and int(data.get("test_tokens") or 0) <= 0:
            warnings.append("Test split contains no tokens")

    if not tokenizer_path.is_file():
        blockers.append(f"Tokenizer missing: {tokenizer_path.relative_to(project_root) if tokenizer_path.is_relative_to(project_root) else tokenizer_path}")
    elif tokenizer_report is None:
        warnings.append("Tokenizer report missing; the fingerprint cannot be matched against the corpus")
    elif data is not None and data.get("tokenizer_fingerprint") and tokenizer_report.get("fingerprint") and data.get("tokenizer_fingerprint") != tokenizer_report.get("fingerprint"):
        blockers.append("Tokenizer fingerprint and token stream do not match")

    if hardware is None:
        warnings.append("Hardware autotuning missing")

    plan: dict[str, Any] | None = None
    checkpoint_status: dict[str, Any] | None = None
    if config_path is not None and data is not None:
        try:
            model, train = load_config(config_path)
            train_tokens = int(data.get("train_tokens", 0))
            tokens_per_step = train.batch_size * model.max_seq_len * train.gradient_accumulation_steps
            max_steps = 50 if mode == "smoke" else train.resolve_max_steps(train_tokens=train_tokens, tokens_per_optimizer_step=tokens_per_step)
            passes = max_steps * tokens_per_step / max(1, train_tokens)
            planned_tokens = max_steps * tokens_per_step
            measured_tps = float(((hardware or {}).get("recommendation") or {}).get("tokens_per_second") or 0)
            estimated_seconds = planned_tokens / measured_tps if measured_tps > 0 else None
            plan = {
                "max_steps": max_steps,
                "tokens_per_step": tokens_per_step,
                "planned_tokens": planned_tokens,
                "dataset_passes": round(passes, 4),
                "estimated_seconds": round(estimated_seconds, 1) if estimated_seconds else None,
                "minimum_recommended_tokens": minimum_tokens or SMOKE_MIN_TOKENS,
            }
            output = project_root / train.output_dir
            latest = output / "latest.pt"
            best = output / "best.pt"
            checkpoint_status = {
                "output_dir": train.output_dir,
                "latest_exists": latest.exists(),
                "best_exists": best.exists(),
                "resume_recommended": latest.as_posix() if latest.exists() else None,
            }
            if mode == "full":
                if passes > train.max_dataset_passes_block and not train.allow_excessive_dataset_passes:
                    blockers.append(f"Planned dataset passes ({passes:.1f}) exceed the safety limit")
                elif passes > train.max_dataset_passes_warning:
                    warnings.append(f"High number of planned dataset passes: {passes:.1f}")
        except (ValueError, FileNotFoundError, TypeError) as exc:
            blockers.append(str(exc))

    status = "red" if blockers else ("yellow" if warnings else "green")
    report = {
        "format": "godot-coder-training-preflight",
        "format_version": 2,
        "created_at": time.time(),
        "mode": mode,
        "profile_id": profile_id,
        "status": status,
        "can_start": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
        "audit": (audit or {}).get("summary"),
        "validation": {key: (validation or {}).get(key) for key in ("validator", "records", "passed", "failed", "context_warnings", "prepared")},
        "dataset": ({**{key: data.get(key) for key in ("dataset_fingerprint", "tokenizer_fingerprint", "train_tokens", "val_tokens", "test_tokens")}, "manifest_path": str(data_manifest_path.relative_to(project_root))} if data and data_manifest_path else None),
        "freshness": freshness,
        "hardware_recommendation": (hardware or {}).get("recommendation"),
        "training_plan": plan,
        "checkpoint_status": checkpoint_status,
        "config": str(config_path.relative_to(project_root)) if config_path else None,
    }
    _atomic_json(project_root / "reports" / "audit" / f"preflight_{mode}_latest.json", report)
    return report

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the licensed Godot corpus and build a night-training preflight.")
    parser.add_argument("--root", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--near-distance", type=int, default=3)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--config", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    if args.command == "audit":
        audit_corpus(root, near_duplicate_distance=args.near_distance)
    else:
        config = (root / args.config).resolve() if args.config else None
        build_preflight(root, config_path=config)


if __name__ == "__main__":
    main()
