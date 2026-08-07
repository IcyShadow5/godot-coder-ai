from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

TARGETS = (2_000_000, 5_000_000, 20_000_000)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError, TypeError):
        return None


def build_scale_plan(project_root: Path, *, passes: float = 4.0) -> dict[str, Any]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    processed_root = project_root / "data" / "processed"
    if processed_root.exists():
        for path in processed_root.glob("corpus*/manifest.json"):
            payload = _load_json(path)
            if payload is not None:
                candidates.append((path, payload))
    processed: Path | None = None
    manifest: dict[str, Any] = {}
    if candidates:
        processed, manifest = max(
            candidates,
            key=lambda item: (int(item[1].get("train_tokens") or 0), item[0].stat().st_mtime),
        )
    current = int(manifest.get("train_tokens") or 0)
    autotune = _load_json(project_root / "reports" / "hardware" / "autotune_latest.json") or {}
    recommendation = autotune.get("recommendation") or {}
    tokens_per_second = float(recommendation.get("tokens_per_second") or 0.0)
    effective_batch = int(recommendation.get("tokens_per_step") or 0)
    if not effective_batch:
        config = _load_json(project_root / "reports" / "training" / "latest.json") or {}
        effective_batch = int((config.get("token_accounting") or {}).get("tokens_per_optimizer_step") or 12_288)
    rows = []
    for target in TARGETS:
        processed_tokens = int(round(target * passes))
        seconds = processed_tokens / tokens_per_second if tokens_per_second > 0 else None
        rows.append({
            "target_unique_tokens": target,
            "current_unique_tokens": current,
            "missing_unique_tokens": max(0, target - current),
            "progress": round(min(1.0, current / target), 6) if target else 0.0,
            "passes": passes,
            "planned_processed_tokens": processed_tokens,
            "estimated_steps": math.ceil(processed_tokens / max(1, effective_batch)),
            "estimated_training_seconds": round(seconds, 1) if seconds is not None else None,
            "estimated_training_hours": round(seconds / 3600, 3) if seconds is not None else None,
        })
    report = {
        "format": "godot-coder-scale-plan",
        "format_version": 1,
        "created_at": time.time(),
        "current_train_tokens": current,
        "manifest_path": processed.relative_to(project_root).as_posix() if processed else None,
        "recommended_first_target": 5_000_000,
        "ambitious_target": 20_000_000,
        "passes": passes,
        "autotune_tokens_per_second": tokens_per_second or None,
        "effective_tokens_per_step": effective_batch,
        "targets": rows,
        "note": "Unique corpus tokens and processed training tokens are different quantities.",
    }
    output = project_root / "reports" / "corpus" / "scale_plan_latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan corpus growth and training token budgets")
    parser.add_argument("--root", default=".")
    parser.add_argument("--passes", type=float, default=4.0)
    args = parser.parse_args()
    print(json.dumps(build_scale_plan(Path(args.root).resolve(), passes=args.passes), indent=2))


if __name__ == "__main__":
    main()
