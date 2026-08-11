from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Iterable

from .checkpoint import load_checkpoint
from .provenance import PromptPart, compose_prompt
from .tokenizer import TokenizerLike, load_tokenizer
from .ui.services import GenerationService, validate_code
from .golden_tasks import GOLDEN_TASKS

# These prompts remain useful for the old curriculum model. They are not called
# "exact" for corpus-trained models because they were never part of that corpus.
CURRICULUM_PROMPTS = (
    {
        "id": "energy_spend_curriculum",
        "topic": "functions",
        "prompt": "extends Node\n\n@export var maximum_energy: float = 103.0\nvar energy: float = maximum_energy\n\nfunc can_spend(amount: float) -> bool:\n\treturn amount > 0.0 and energy >= amount\n\nfunc spend(amount: float) -> bool:\n\t",
    },
    {
        "id": "health_damage_curriculum",
        "topic": "signals",
        "prompt": "extends Node\n\nsignal health_changed(current: float, maximum: float)\nsignal died\n\n@export var maximum_health: float = 73.0\nvar health: float = maximum_health\n\nfunc take_damage(amount: float) -> void:\n\t",
    },
    {
        "id": "inventory_add_curriculum",
        "topic": "collections",
        "prompt": "extends Node\n\n@export var capacity: int = 7\nvar items: Array[String] = []\n\nfunc add_item(item_id: String) -> bool:\n\t",
    },
    {
        "id": "cooldown_trigger_curriculum",
        "topic": "gameplay",
        "prompt": "extends Node\n\n@export var cooldown_duration: float = 0.65\nvar cooldown_remaining: float = 0.0\n\nfunc _process(delta: float) -> void:\n\tcooldown_remaining = maxf(cooldown_remaining - delta, 0.0)\n\nfunc is_ready() -> bool:\n\treturn cooldown_remaining <= 0.0\n\nfunc trigger() -> bool:\n\t",
    },
    {
        "id": "node_move_curriculum",
        "topic": "nodes",
        "prompt": "extends Node2D\n\n@export var speed: float = 5.15\nvar target_position: Vector2 = Vector2.ZERO\nvar moving: bool = false\n\nfunc move_to(next_target: Vector2) -> void:\n\ttarget_position = next_target\n\tmoving = true\n\nfunc _process(delta: float) -> void:\n\t",
    },
    {
        "id": "state_change_curriculum",
        "topic": "state",
        "prompt": "extends Node\n\nenum State { IDLE, ACTIVE, DISABLED }\n\nvar state: State = State.IDLE\nvar transition_count: int = 1\n\nfunc set_state(next_state: State) -> bool:\n\t",
    },
    {
        "id": "clamp_value_curriculum",
        "topic": "basics",
        "prompt": "extends Node\n\n@export var maximum_value: int = 33\nvar current_value: int = 0\n\nfunc set_value(next_value: int) -> void:\n\tcurrent_value = clampi(next_value, 0, maximum_value)\n\nfunc add_value(amount: int) -> int:\n\t",
    },
    {
        "id": "stat_component_curriculum",
        "topic": "architecture",
        "prompt": "extends Node\n\nsignal value_changed(value: float)\n\n@export var base_value: float = 33.0\n@export var multiplier: float = 1.50\nvar bonus: float = 0.0\n\nfunc get_value() -> float:\n\t",
    },
)

# Task prompts measure transfer. A base next-token model was not instruction
# tuned for these tasks, so this tier is diagnostic rather than a pass/fail gate.
TASK_TRANSFER_PROMPTS = (
    {
        "id": "energy_spend",
        "topic": "functions",
        "prompt": "extends Node\n\n@export var energy: float = 100.0\n\nfunc spend_energy(amount: float) -> bool:\n\t",
    },
    {
        "id": "health_damage",
        "topic": "signals",
        "prompt": "extends Node\n\nsignal health_changed(current: float)\n@export var health: float = 100.0\n\nfunc take_damage(amount: float) -> void:\n\t",
    },
    {
        "id": "inventory_add",
        "topic": "collections",
        "prompt": "extends Node\n\nvar items: Array[String] = []\n\nfunc add_item(item_id: String) -> bool:\n\t",
    },
    {
        "id": "cooldown_trigger",
        "topic": "gameplay",
        "prompt": "extends Node\n\nvar cooldown_remaining: float = 0.0\n\nfunc trigger_cooldown(duration: float) -> bool:\n\t",
    },
    {
        "id": "node_move",
        "topic": "nodes",
        "prompt": "extends Node2D\n\n@export var speed: float = 4.0\nvar target: Vector2 = Vector2.ZERO\n\nfunc _process(delta: float) -> void:\n\t",
    },
    {
        "id": "state_change",
        "topic": "state",
        "prompt": "extends Node\n\nenum State { IDLE, ACTIVE, DISABLED }\nvar state: State = State.IDLE\n\nfunc set_state(next_state: State) -> bool:\n\t",
    },
    {
        "id": "clamp_value",
        "topic": "basics",
        "prompt": "extends Node\n\n@export var maximum_value: int = 10\nvar current_value: int = 0\n\nfunc add_value(amount: int) -> int:\n\t",
    },
    {
        "id": "stat_component",
        "topic": "architecture",
        "prompt": "extends Node\n\n@export var base_value: float = 10.0\nvar bonus: float = 0.0\n\nfunc get_value() -> float:\n\t",
    },
)

# Backwards-compatible exports used by older tests/imports.
EXACT_PROMPTS = CURRICULUM_PROMPTS
TRANSFER_PROMPTS = TASK_TRANSFER_PROMPTS

_ERROR_PATTERN = re.compile(r"(?:Parse Error|SCRIPT ERROR):?\s*(.*)", re.IGNORECASE)
_FUNCTION_LINE = re.compile(
    r"^(?:static\s+)?func\s+[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)\s*(?:->\s*[^:]+)?\s*:\s*(?:#.*)?$"
)


def _first_error(output: str) -> str | None:
    for line in output.splitlines():
        match = _ERROR_PATTERN.search(line)
        if match:
            return match.group(1).strip() or line.strip()
    return next((line.strip() for line in output.splitlines() if line.strip()), None)


def _failure_kind(error: str | None) -> str | None:
    if not error:
        return None
    lowered = error.lower()
    if "tab character" in lowered or "indent" in lowered:
        return "indentation"
    if "not declared" in lowered or "not found in base self" in lowered:
        return "unresolved_symbol"
    if "opening counterpart" in lowered or "closing" in lowered or "parenth" in lowered:
        return "delimiter"
    if "found \"...\"" in lowered or "found '...'" in lowered:
        return "ellipsis"
    return "syntax_or_static_check"


def _summary(results: Iterable[dict[str, object]]) -> dict[str, object]:
    materialized = list(results)
    passed = sum(1 for item in materialized if item["parser_passed"])
    return {
        "cases": len(materialized),
        "parser_passed": passed,
        "parser_failed": len(materialized) - passed,
        "parser_pass_rate": passed / len(materialized) if materialized else 0.0,
    }


def _nearest_project(path: Path, boundary: Path) -> Path | None:
    current = path.parent
    boundary = boundary.resolve()
    while True:
        if (current / "project.godot").exists():
            return current
        if current.resolve() == boundary or current.parent == current:
            return None
        current = current.parent


def _last_function_completion(text: str, tokenizer: TokenizerLike, max_new_tokens: int) -> tuple[str, str, int] | None:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines(keepends=True)
    for index in range(len(lines) - 1, -1, -1):
        if not _FUNCTION_LINE.match(lines[index].rstrip("\n")):
            continue
        prefix = "".join(lines[: index + 1])
        suffix = "".join(lines[index + 1 :])
        suffix_ids = tokenizer.encode(suffix)
        if not 8 <= len(suffix_ids) <= max_new_tokens:
            continue
        if not any(line.startswith(("\t", "    ")) and line.strip() for line in lines[index + 1 :]):
            continue
        return prefix, suffix, len(suffix_ids)
    return None


def _corpus_heldout_prompts(
    root: Path,
    tokenizer: TokenizerLike,
    *,
    max_new_tokens: int,
    limit: int = 8,
) -> tuple[dict[str, object], ...]:
    manifest_path = root / "data" / "corpus" / "corpus_manifest.json"
    if not manifest_path.exists():
        return ()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    downloads = root / "data" / "corpus" / "downloads"
    records = sorted(
        (
            item
            for item in manifest.get("records", [])
            if item.get("split") in {"val", "test"}
            and item.get("source_id") == "godot-demo-projects"
            and item.get("validation_status") == "passed"
        ),
        key=lambda item: (item.get("split", ""), item.get("record_id", "")),
    )
    cases: list[dict[str, object]] = []
    for record in records:
        source = downloads / str(record["source_id"]) / str(record["original_path"])
        if not source.exists():
            continue
        project = _nearest_project(source, downloads / str(record["source_id"]))
        if project is None:
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        completion = _last_function_completion(text, tokenizer, max_new_tokens)
        if completion is None:
            continue
        prompt, reference_suffix, expected_tokens = completion
        cases.append(
            {
                "id": f"heldout_{record['record_id']}",
                "topic": "heldout_file_completion",
                "prompt": prompt,
                "reference_suffix": reference_suffix,
                "reference_tokens": expected_tokens,
                "source_path": str(record["original_path"]),
                "validation_project": project.relative_to(root).as_posix(),
                "split": record.get("split"),
            }
        )
        if len(cases) >= limit:
            break
    return tuple(cases)


def _load_checkpoint_context(root: Path, checkpoint: str) -> tuple[dict[str, object], TokenizerLike, str]:
    checkpoint_path = (root / checkpoint).resolve()
    payload = load_checkpoint(checkpoint_path, map_location="cpu")
    train_config = payload.get("train_config", {})
    tokenizer_path = Path(str(train_config.get("tokenizer_path", "artifacts/tokenizer.json")))
    if not tokenizer_path.is_absolute():
        tokenizer_path = root / tokenizer_path
    tokenizer = load_tokenizer(tokenizer_path)
    data_dir = str(train_config.get("data_dir", ""))
    kind = "corpus" if "corpus" in data_dir.lower() else "curriculum"
    return payload, tokenizer, kind


def _token_prefix_accuracy(tokenizer: TokenizerLike, generated: str, reference: str) -> float:
    generated_ids = tokenizer.encode(generated)
    reference_ids = tokenizer.encode(reference)
    if not reference_ids:
        return 0.0
    matching = sum(left == right for left, right in zip(generated_ids, reference_ids))
    return matching / len(reference_ids)


# Benchmarking is deliberately greedy (temperature 0 / top-k 0): evaluation
# must be reproducible, so it does not share the interactive defaults in
# sampling.py. The benchmark CLI uses the same deterministic profile.
def run_benchmark(
    project_root: str | Path,
    checkpoint: str,
    *,
    validation_project: str = "data/raw/seed_project",
    max_new_tokens: int = 256,
    temperature: float = 0.0,
    top_k: int = 0,
    top_p: float = 1.0,
    repetition_penalty: float = 1.0,
    mode: str = "auto",
) -> dict[str, object]:
    root = Path(project_root).resolve()
    payload, tokenizer, checkpoint_kind = _load_checkpoint_context(root, checkpoint)
    model_max_seq = int((payload.get("model_config") or {}).get("max_seq_len") or 0)
    selected_mode = checkpoint_kind if mode == "auto" else mode
    if selected_mode not in {"corpus", "curriculum", "golden", "task"}:
        raise ValueError("mode must be auto, corpus, curriculum, golden, or task")

    if selected_mode == "corpus":
        heldout = _corpus_heldout_prompts(root, tokenizer, max_new_tokens=max_new_tokens)
        tiers: tuple[tuple[str, tuple[dict[str, object], ...]], ...] = (
            ("heldout_completion", heldout),
            ("golden_tasks", GOLDEN_TASKS),
            ("task_transfer", TASK_TRANSFER_PROMPTS),
        )
    elif selected_mode == "curriculum":
        tiers = (("curriculum_like", CURRICULUM_PROMPTS), ("golden_tasks", GOLDEN_TASKS), ("task_transfer", TASK_TRANSFER_PROMPTS))
    elif selected_mode == "golden":
        tiers = (("golden_tasks", GOLDEN_TASKS),)
    else:
        tiers = (("task_transfer", TASK_TRANSFER_PROMPTS),)

    generator = GenerationService(root)
    all_results: list[dict[str, object]] = []
    tier_reports: dict[str, dict[str, object]] = {}
    total_cases = sum(len(cases) for _, cases in tiers)
    overall_index = 0

    for tier_name, cases in tiers:
        tier_results: list[dict[str, object]] = []
        for case in cases:
            overall_index += 1
            case_max_tokens = min(max_new_tokens, int(case.get("reference_tokens", max_new_tokens)))
            prompt_text = str(case.get("prompt", case.get("scaffold", "")))
            generated = generator.generate(
                checkpoint,
                prompt_text,
                max_new_tokens=case_max_tokens,
                temperature=temperature,
                top_k=top_k,
                top_p=top_p,
                repetition_penalty=repetition_penalty,
                device_name="auto",
            )
            case_project = str(case.get("validation_project", validation_project))
            # generate() returns only the completion (the chat flow strips the
            # prompt), so re-attach the scaffold before validating - a bare
            # completion is not parseable as a standalone script.
            validation = validate_code(root, prompt_text + generated, case_project)
            prompt = prompt_text
            suffix = generated[len(prompt) :] if generated.startswith(prompt) else generated
            reference = case.get("reference_suffix")
            item: dict[str, object] = {
                **case,
                "tier": tier_name,
                "generated": generated,
                "generated_suffix": suffix,
                "parser_passed": validation["passed"],
                "parser_output": validation["output"],
                "first_error": _first_error(validation["output"]),
            }
            item["failure_kind"] = _failure_kind(item["first_error"] if isinstance(item["first_error"], str) else None)
            if model_max_seq >= 2:
                _, case_context = compose_prompt(
                    [PromptPart(str(tier_name), prompt_text, meta={"id": str(case.get("id", ""))})],
                    tokenizer,
                    max_seq_len=model_max_seq,
                    max_new_tokens=case_max_tokens,
                )
                item["prompt_tokens"] = case_context.prompt_tokens
                item["context_truncated"] = case_context.truncated
                item["kv_cache_possible"] = case_context.kv_cache_possible
            if isinstance(reference, str):
                item["token_prefix_accuracy"] = _token_prefix_accuracy(tokenizer, suffix, reference)
                item["reference_exact_match"] = suffix == reference
            tier_results.append(item)
            all_results.append(item)
            status = "pass" if validation["passed"] else "fail"
            print(
                f"benchmark={overall_index}/{total_cases} tier={tier_name} "
                f"case={case['id']} parser={status}"
            )
            if isinstance(reference, str):
                print(f"  token_prefix_accuracy={float(item['token_prefix_accuracy']):.3f}")
            if not validation["passed"]:
                preview = "\\n".join(suffix.replace("\r", "").splitlines()[:6])[:500]
                print(f"  first_error={item['first_error'] or 'unknown'}")
                print(f"  failure_kind={item['failure_kind'] or 'unknown'}")
                print(f"  generated_preview={preview!r}")

        tier_reports[tier_name] = {**_summary(tier_results), "results": tier_results}

    overall = _summary(all_results)
    report: dict[str, object] = {
        "format": "godot-coder-benchmark",
        "format_version": 3,
        "checkpoint": checkpoint,
        "checkpoint_kind": checkpoint_kind,
        "mode": selected_mode,
        "created_at": time.time(),
        **overall,
        "settings": {
            "max_new_tokens": max_new_tokens,
            "temperature": temperature,
            "top_k": top_k,
            "top_p": top_p,
            "repetition_penalty": repetition_penalty,
            "deterministic": temperature == 0.0,
        },
        "tiers": tier_reports,
        "results": all_results,
        "interpretation": {
            "heldout_completion": "Continuation of real held-out demo scripts from the corpus; appropriate for a base next-token model.",
            "curriculum_like": "Continuation of the synthetic v0.3 curriculum structures.",
            "task_transfer": "Hand-written task scaffolds. This becomes a primary metric only after instruction/post-training.",
            "golden_tasks": "30 hand-written GDScript challenges with reference solutions. Parser pass rate + token-prefix accuracy.",
        },
    }
    destination = root / "reports" / "benchmarks"
    destination.mkdir(parents=True, exist_ok=True)
    safe_name = Path(checkpoint).parent.name + "_" + Path(checkpoint).stem
    output = destination / f"{safe_name}_{int(report['created_at'])}.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report: {output.relative_to(root).as_posix()}")
    printable = {"overall": overall}
    for tier_name, tier_report in tier_reports.items():
        printable[tier_name] = {key: value for key, value in tier_report.items() if key != "results"}
    print(json.dumps(printable, indent=2))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run benchmark tiers appropriate for the checkpoint training data.")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--validation-project", default="data/raw/seed_project")
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-k", type=int, default=0)
    parser.add_argument("--top-p", type=float, default=None)
    parser.add_argument("--repetition-penalty", type=float, default=1.0)
    parser.add_argument("--mode", choices=("auto", "corpus", "curriculum", "golden", "task"), default="auto")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_benchmark(
        args.project_root,
        args.checkpoint,
        validation_project=args.validation_project,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        top_k=args.top_k,
        top_p=args.top_p if args.top_p is not None else 1.0,
        repetition_penalty=args.repetition_penalty,
        mode=args.mode,
    )


if __name__ == "__main__":
    main()
