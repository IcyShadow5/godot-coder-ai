"""Adversarial, read-only verification of checkpoint claims.

The benchmark answers "how often does the output parse?". This module asks
the harder question: is the measured success real, or does it survive only
because of artefacts (trivial outputs, memorized prompts, fragile phrase
matching, sampling noise, hollow completions)?

Every check tries to falsify the claim "checkpoint A is better than B"
(or, with a single checkpoint, "this checkpoint is good") and reports
VERIFIED / FALSIFIED / UNRESOLVED with the evidence that led to it.

The verifier is read-only with respect to the project: it never writes
into reports/, data/, checkpoints/ or artifacts/. Godot parser checks
write their temporary scripts into a caller-provided work directory
outside the project tree, which is removed again afterwards.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .golden_tasks import GOLDEN_TASKS

VERDICT_VERIFIED = "VERIFIED"
VERDICT_FALSIFIED = "FALSIFIED"
VERDICT_UNRESOLVED = "UNRESOLVED"


# ---------------------------------------------------------------------------
# Pure check helpers (unit-testable without a model or Godot)
# ---------------------------------------------------------------------------

_TRIVIAL_PATTERNS = (
    re.compile(r"^\s*$"),                        # empty / whitespace only
    re.compile(r"^\s*(#.*\n?)*$"),               # comments only
    # Function or class header without a body.
    re.compile(r"^\s*(?:func\s+\w+\([^)]*\)(?:\s*->\s*\w+\s*)?|class\s+\w+)\s*:\s*$", re.MULTILINE),
)


def is_trivial_completion(completion: str) -> bool:
    """A completion that "parses" but contains no real code.

    Empty output, comment-only output, and function headers without a body
    are the classic hollow successes of a parser-pass metric.
    """
    text = completion.replace("\r", "")
    if _TRIVIAL_PATTERNS[0].match(text) or _TRIVIAL_PATTERNS[1].match(text):
        return True
    stripped = [line.strip() for line in text.splitlines() if line.strip()]
    code_lines = [line for line in stripped if not line.startswith("#")]
    if not code_lines:
        return True
    # Bare pass statements only.
    if all(line == "pass" for line in code_lines):
        return True
    # Headers whose only body statements are bare passes (or none at all).
    body = [line for line in code_lines if not _TRIVIAL_PATTERNS[2].match(line)]
    if not body or all(line == "pass" for line in body):
        return True
    return False


def substance_score(completion: str) -> float:
    """0.0 for hollow output, up to 1.0 for output full of real statements."""
    text = completion.replace("\r", "")
    if is_trivial_completion(text):
        return 0.0
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    code_lines = [line for line in lines if not line.startswith("#")]
    if not code_lines:
        return 0.0
    return min(1.0, len(code_lines) / 3.0)  # 3+ statements = full substance


def leak_index(prompt: str, corpus_texts: list[str], n: int = 16, rare_limit: int = 2) -> float:
    """Share of the prompt's *distinctive* n-grams that occur in the corpus.

    Only n-grams that are rare in the corpus (present in at most ``rare_limit``
    distinct texts) carry signal: generic GDScript boilerplate shows up in many
    files and is ignored, while a near-verbatim copy of a task scaffold shows
    up in one or two and counts. A high value means the prompt (or a near copy
    of it) was part of the training data, so high scores on it prove memory
    rather than transfer.
    """
    normalized = re.sub(r"\s+", " ", prompt).strip().lower()
    if len(normalized) < n:
        return 0.0
    prompt_ngrams = {normalized[index:index + n] for index in range(len(normalized) - n + 1)}
    if not prompt_ngrams:
        return 0.0
    occurrence = {gram: 0 for gram in prompt_ngrams}
    for text in corpus_texts:
        clean = re.sub(r"\s+", " ", text).strip().lower()
        if len(clean) < n:
            continue
        text_ngrams = {clean[index:index + n] for index in range(len(clean) - n + 1)}
        for gram in prompt_ngrams & text_ngrams:
            occurrence[gram] += 1
    distinctive = [gram for gram, count in occurrence.items() if count <= rare_limit]
    if not distinctive:
        return 0.0
    present = [gram for gram in distinctive if occurrence[gram] > 0]
    return len(present) / len(distinctive)


def _matching_close_paren(text: str, open_index: int) -> int:
    """Index of the ')' matching the '(' at open_index, or -1 if unbalanced."""
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _mask_triple_quoted_lines(text: str) -> str:
    """Blank out GDScript triple-quoted string spans.

    A docstring can contain a line that reads exactly like a function
    definition, and the def scan must not see it. The mask is
    character for character and keeps newlines, so offsets in the
    masked text map one to one onto the original scaffold. A marker
    inside a comment would open an unclosed span and mask the rest -
    safe (no broken variant), but such scaffolds lose the rename.
    """
    masked: list[str] = []
    marker: str | None = None
    for line in text.splitlines(keepends=True):
        core = line.rstrip("\r\n")
        newline = line[len(core):]
        if marker is not None:
            end = line.find(marker)
            if end == -1:
                masked.append(" " * len(core) + newline)
                continue
            masked.append(" " * (end + 3) + line[end + 3:])
            marker = None
            continue
        opening: tuple[str, int] | None = None
        for candidate in ('"""', "'''"):
            position = line.find(candidate)
            if position != -1:
                opening = (candidate, position)
                break
        if opening is None:
            masked.append(line)
            continue
        candidate, position = opening
        closer = line.find(candidate, position + 3)
        if closer != -1:
            masked.append(line[:position] + " " * (closer + 3 - position) + line[closer + 3:])
        else:
            masked.append(line[:position] + " " * (len(core) - position) + newline)
            marker = candidate
    return "".join(masked)


def mutation_variants(scaffold: str) -> list[str]:
    """Syntactically valid perturbations of a scaffold.

    A model that only learned the exact training phrasing collapses when
    the prompt is rephrased even slightly; the mutations keep the task
    semantically identical so a drop in pass rate means fragility.
    """
    variants: list[str] = []
    # 1) A leading comment changes the token stream without changing the task.
    variants.append("# verify: rephrased prompt\n" + scaffold)
    # 2) Rename the last declared function (identifier case/shape change).
    # Anchored to a real definition line, and triple-quoted spans are
    # masked out, so a comment or docstring that merely mentions
    # "func foo" cannot hijack the rename.
    masked = _mask_triple_quoted_lines(scaffold)
    match = list(re.finditer(r"^\s*func\s+([A-Za-z_]\w*)", masked, re.MULTILINE))
    if match:
        last = match[-1]
        name = last.group(1)
        name_start, name_end = last.span(1)
        parts = name.split("_")
        camel = parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
        renamed = camel + "Impl"
        # Rename the definition itself, not the first textual occurrence:
        # the name can appear earlier in comments or call sites.
        variants.append(scaffold[:name_start] + renamed + scaffold[name_end:])
        # 3) An extra parameter that is not used by the body, inserted into
        #    this function's signature - the last ')' in the scaffold may
        #    belong to a call after the definition, and an empty parameter
        #    list must not get a leading comma (that would be a syntax error).
        open_paren = scaffold.find("(", name_end)
        close_paren = _matching_close_paren(scaffold, open_paren) if open_paren != -1 else -1
        if close_paren != -1:
            params = scaffold[open_paren + 1:close_paren].strip()
            extra = "_unused: int = 0"
            insertion = ", " + extra if params else extra
            variants.append(scaffold[:close_paren] + insertion + scaffold[close_paren:])
    return variants


def trivial_pass_fraction(results: list[dict[str, object]]) -> float:
    """Share of parser-passed completions that are hollow."""
    passed = [item for item in results if item.get("parser_passed")]
    if not passed:
        return 0.0
    trivial = sum(1 for item in passed if is_trivial_completion(str(item.get("generated_suffix") or "")))
    return trivial / len(passed)


def substance_average(results: list[dict[str, object]]) -> float:
    passed = [item for item in results if item.get("parser_passed")]
    if not passed:
        return 0.0
    return sum(substance_score(str(item.get("generated_suffix") or "")) for item in passed) / len(passed)


def pass_rate(results: list[dict[str, object]]) -> float:
    if not results:
        return 0.0
    return sum(1 for item in results if item.get("parser_passed")) / len(results)


# ---------------------------------------------------------------------------
# The five falsification checks
# ---------------------------------------------------------------------------

def check_trivial_pass(
    a_results: list[dict[str, object]],
    b_results: list[dict[str, object]],
    expected_rate: float | None = None,
) -> dict[str, object]:
    """If A's advantage vanishes once hollow passes are removed, it is fake.

    With ``expected_rate`` (single-checkpoint runs) the check is absolute: a
    raw pass rate below the claimed threshold falsifies the claim outright.
    """
    a_rate = pass_rate(a_results)
    b_rate = pass_rate(b_results)
    a_trivial = trivial_pass_fraction(a_results)
    b_trivial = trivial_pass_fraction(b_results)
    a_substance = substance_average(a_results)
    b_substance = substance_average(b_results)
    # A hollow pass is not worth more than a hollow pass - recalculate the
    # comparison on substantive completions only.
    a_substantive_rate = a_rate * (1.0 - a_trivial)
    b_substantive_rate = b_rate * (1.0 - b_trivial)
    evidence = {
        "raw_pass_rate": {"a": a_rate, "b": b_rate},
        "trivial_fraction": {"a": a_trivial, "b": b_trivial},
        "substance_average": {"a": a_substance, "b": b_substance},
        "substantive_pass_rate": {"a": a_substantive_rate, "b": b_substantive_rate},
    }
    if expected_rate is not None and a_rate < expected_rate:
        verdict = VERDICT_FALSIFIED
        reason = f"raw pass rate {a_rate:.2f} is below the claimed {expected_rate:.2f}."
    elif a_rate > b_rate and a_substantive_rate <= b_substantive_rate:
        verdict = VERDICT_FALSIFIED
        reason = "A's parser advantage disappears once hollow passes are removed."
    elif a_trivial > 0.5:
        verdict = VERDICT_FALSIFIED
        reason = "More than half of A's passes are empty or comment-only output."
    else:
        verdict = VERDICT_VERIFIED
        reason = "A's passes survive a substance check."
    return {"verdict": verdict, "reason": reason, "evidence": evidence}


def check_leak(a_results: list[dict[str, object]], b_results: list[dict[str, object]], corpus_texts: list[str]) -> dict[str, object]:
    """If the tasks A wins on are near-copies of training data, A remembers."""
    if not corpus_texts:
        return {"verdict": VERDICT_UNRESOLVED, "reason": "No corpus text available to scan for leaks.", "evidence": {}}
    leaking: list[dict[str, object]] = []
    seen_prompts: set[str] = set()
    for item in a_results + b_results:
        prompt = str(item.get("prompt") or item.get("scaffold") or "")
        # In comparison mode a and b hold the same golden tasks, so
        # the same prompt appears twice; count each prompt once or
        # the leak tally doubles.
        if not prompt or prompt in seen_prompts:
            continue
        seen_prompts.add(prompt)
        index = leak_index(prompt, corpus_texts)
        if index >= 0.6:
            leaking.append({"id": item.get("id"), "leak_index": round(index, 3)})
    evidence = {
        "leaking_cases": leaking[:10],
        "leaking_count": len(leaking),
        "corpus_texts": len(corpus_texts),
    }
    if leaking:
        return {
            "verdict": VERDICT_FALSIFIED,
            "reason": f"{len(leaking)} prompt(s) are near-copies of training data; wins there prove memory, not transfer.",
            "evidence": evidence,
        }
    return {"verdict": VERDICT_VERIFIED, "reason": "No prompt is a near-copy of the corpus.", "evidence": evidence}


def check_mutation(original: dict[str, object], mutated: dict[str, object]) -> dict[str, object]:
    """If A wins on the exact prompt but loses on rephrasings, A is fragile."""
    a_original = float(original.get("a_rate", 0.0))
    b_original = float(original.get("b_rate", 0.0))
    a_mutated = float(mutated.get("a_rate", 0.0))
    b_mutated = float(mutated.get("b_rate", 0.0))
    a_advantage_original = a_original - b_original
    a_advantage_mutated = a_mutated - b_mutated
    evidence = {
        "original_pass_rate": {"a": a_original, "b": b_original},
        "mutated_pass_rate": {"a": a_mutated, "b": b_mutated},
    }
    if a_advantage_original > 0.05 and a_advantage_mutated <= 0.0:
        return {
            "verdict": VERDICT_FALSIFIED,
            "reason": "A's advantage disappears when prompts are rephrased - it only handles the exact training phrasing.",
            "evidence": evidence,
        }
    if a_original > b_original and a_mutated <= b_mutated:
        return {
            "verdict": VERDICT_FALSIFIED,
            "reason": "A loses its edge on semantically identical variants.",
            "evidence": evidence,
        }
    return {"verdict": VERDICT_VERIFIED, "reason": "A keeps its advantage on rephrased prompts.", "evidence": evidence}


def check_temperature(cold: dict[str, object], warm: dict[str, object]) -> dict[str, object]:
    """If A's edge vanishes under sampling noise, it is not a real difference."""
    a_cold = float(cold.get("a_rate", 0.0))
    b_cold = float(cold.get("b_rate", 0.0))
    a_warm = float(warm.get("a_rate", 0.0))
    b_warm = float(warm.get("b_rate", 0.0))
    evidence = {
        "temperature_0_pass_rate": {"a": a_cold, "b": b_cold},
        "temperature_07_pass_rate": {"a": a_warm, "b": b_warm},
    }
    if (a_cold - b_cold) > 0.05 and (a_warm - b_warm) <= 0.0:
        return {
            "verdict": VERDICT_FALSIFIED,
            "reason": "A's advantage only exists at temperature 0 and disappears under sampling - it is noise, not skill.",
            "evidence": evidence,
        }
    return {"verdict": VERDICT_VERIFIED, "reason": "A's advantage is stable across sampling temperatures.", "evidence": evidence}


def check_repetition(a_results: list[dict[str, object]], b_results: list[dict[str, object]]) -> dict[str, object]:
    """If A looks like the reference (high token-prefix) but does nothing, it is a shell."""
    def prefix_of(results: list[dict[str, object]]) -> float:
        values = [float(item["token_prefix_accuracy"]) for item in results if "token_prefix_accuracy" in item]
        return sum(values) / len(values) if values else 0.0

    a_prefix = prefix_of(a_results)
    b_prefix = prefix_of(b_results)
    a_substance = substance_average(a_results)
    b_substance = substance_average(b_results)
    evidence = {
        "token_prefix_accuracy": {"a": a_prefix, "b": b_prefix},
        "substance_average": {"a": a_substance, "b": b_substance},
    }
    if a_prefix > b_prefix + 0.05 and a_substance <= b_substance:
        return {
            "verdict": VERDICT_FALSIFIED,
            "reason": "A scores higher on token-prefix but has no more substance - it echoes the reference shape without solving it.",
            "evidence": evidence,
        }
    return {"verdict": VERDICT_VERIFIED, "reason": "A's reference similarity is backed by comparable substance.", "evidence": evidence}


# ---------------------------------------------------------------------------
# Model / Godot plumbing (injectable for tests)
# ---------------------------------------------------------------------------

_generation_services: dict[str, object] = {}


def _default_generate(root: Path, checkpoint: str, prompt: str, *, max_new_tokens: int, temperature: float) -> str:
    from .ui.services import GenerationService

    # Reuse one service per project root: a fresh instance would reload the
    # model weights for every single generation, which makes a full verify
    # run (hundreds of generations) impractically slow. The service itself
    # reloads only when the checkpoint actually changes.
    key = str(root)
    service = _generation_services.get(key)
    if service is None:
        service = GenerationService(root)
        _generation_services[key] = service
    return service.generate(
        checkpoint,
        prompt,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_k=0,
        top_p=1.0,
        repetition_penalty=1.0,
        device_name="auto",
        record_metrics=False,
    )


def _default_validate(root: Path, work_dir: Path, code: str, project_path: str) -> dict[str, object]:
    """Godot parser check with the temp script in the external work dir.

    Unlike the Studio's validate_code this never touches data/generated or
    reports/ - the read-only promise lives here.
    """
    from .godot_cli import build_check_command
    from .process_control import run_managed_process
    from .ui.services import find_godot, safe_child

    godot = find_godot()
    if not godot:
        return {"passed": False, "error": "godot not found", "timed_out": False}
    project = safe_child(root, project_path, must_exist=True)
    if not (project / "project.godot").exists():
        return {"passed": False, "error": "project.godot not found", "timed_out": False}
    script = work_dir / f"verify_{uuid.uuid4().hex[:12]}.gd"
    script.write_text(code, encoding="utf-8", newline="\n")
    try:
        result = run_managed_process(build_check_command(godot, project, script), timeout_seconds=30)
        return {
            "passed": result.return_code == 0 and not result.timed_out,
            "return_code": result.return_code,
            "output": result.output,
            "timed_out": bool(result.timed_out),
        }
    finally:
        script.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _evaluate_checkpoint(
    root: Path,
    work_dir: Path,
    checkpoint: str,
    *,
    max_new_tokens: int,
    temperature: float,
    validation_project: str,
    generate,
    validate,
    prompts: list[tuple[str, str]],
) -> list[dict[str, object]]:
    """Run every prompt through the model and the Godot parser."""
    results: list[dict[str, object]] = []
    for task_id, prompt in prompts:
        generated = generate(root, checkpoint, prompt, max_new_tokens=max_new_tokens, temperature=temperature)
        suffix = generated[len(prompt):] if generated.startswith(prompt) else generated
        validation = validate(root, work_dir, prompt + suffix, validation_project)
        results.append(
            {
                "id": task_id,
                "prompt": prompt,
                "generated_suffix": suffix,
                "parser_passed": bool(validation.get("passed")),
                "parser_output": str(validation.get("output") or validation.get("error") or ""),
            }
        )
    return results


def _with_reference_scores(tokenizer, results: list[dict[str, object]]) -> None:
    from .benchmark import _token_prefix_accuracy

    for item in results:
        reference = next((case.get("reference_suffix") for case in GOLDEN_TASKS if case.get("id") == item.get("id")), None)
        if isinstance(reference, str):
            item["token_prefix_accuracy"] = _token_prefix_accuracy(tokenizer, str(item.get("generated_suffix") or ""), reference)


def _load_tokenizer(root: Path, checkpoint: str):
    from .checkpoint import load_checkpoint
    from .tokenizer import load_tokenizer, resolve_tokenizer_for_fingerprint

    payload = load_checkpoint((root / checkpoint).resolve(), map_location="cpu")
    train_config = payload.get("train_config", {})
    tokenizer_path = Path(str(train_config.get("tokenizer_path", "artifacts/tokenizer.json")))
    if not tokenizer_path.is_absolute():
        tokenizer_path = root / tokenizer_path
    tokenizer = load_tokenizer(tokenizer_path)
    if payload.get("tokenizer_fingerprint") and tokenizer.fingerprint() != payload["tokenizer_fingerprint"]:
        tokenizer = resolve_tokenizer_for_fingerprint(tokenizer_path, tokenizer, payload["tokenizer_fingerprint"])
    return payload, tokenizer


def _collect_corpus_texts(root: Path, limit: int = 500) -> list[str]:
    """Read-only scan of the training corpus for the leak check."""
    texts: list[str] = []
    manifest = root / "data" / "corpus" / "corpus_manifest.json"
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            records = payload.get("records", payload) if isinstance(payload, dict) else payload
            if isinstance(records, list):
                for record in records[:limit]:
                    if not isinstance(record, dict):
                        continue
                    inline = next(
                        (record.get(key) for key in ("text", "content") if isinstance(record.get(key), str)),
                        None,
                    )
                    if inline and len(inline) > 40:
                        texts.append(inline)
                        continue
                    # Real manifests stage one file per record under the split;
                    # resolve and read it read-only.
                    staged = record.get("staged_path")
                    split = record.get("split")
                    if isinstance(staged, str) and isinstance(split, str) and staged:
                        if split not in ("train", ""):
                            continue
                        audited_root = (root / "data" / "corpus" / "audited").resolve()
                        staged_file = (root / "data" / "corpus" / "audited" / split / staged).resolve()
                        try:
                            staged_file.relative_to(audited_root)
                        except ValueError:
                            continue
                        try:
                            content = staged_file.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            continue
                        if len(content) > 40:
                            texts.append(content[:20000])
        except (OSError, ValueError):
            pass
    if len(texts) < 20:
        for pattern in (
            "data/corpus/audited/**/*.gd",
            "data/raw/**/*.gd",
            "data/raw/**/*.txt",
            "data/raw/**/*.md",
        ):
            for path in sorted(root.glob(pattern))[:limit]:
                try:
                    texts.append(path.read_text(encoding="utf-8", errors="replace")[:20000])
                except OSError:
                    continue
                if len(texts) >= limit:
                    break
    return texts


def run_verification(
    project_root: str | Path,
    checkpoint_a: str,
    checkpoint_b: str | None = None,
    *,
    work_dir: str | Path | None = None,
    validation_project: str = "data/raw/seed_project",
    max_new_tokens: int = 256,
    expected_pass_rate: float = 0.5,
    generate=None,
    validate=None,
) -> dict[str, object]:
    """Falsify the claim that A is better than B (or that A is good alone)."""
    if not 0.0 <= expected_pass_rate <= 1.0:
        raise ValueError("expected_pass_rate must be between 0 and 1")
    root = Path(project_root).resolve()
    # Only the temp dir this function creates itself gets removed
    # afterwards; a caller-supplied work dir is theirs to manage and
    # must survive the run.
    owned_work_dir = work_dir is None
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="godot-coder-verify-"))
    else:
        work_dir = Path(work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
    _assert_outside_project(work_dir, root)

    generate_fn = generate or _default_generate
    validate_fn = validate or _default_validate

    tasks = [(str(case.get("id", "?")), str(case.get("scaffold", ""))) for case in GOLDEN_TASKS]
    tasks = [(task_id, prompt) for task_id, prompt in tasks if prompt]

    try:
        # Mutation variants per task keep their task id so reference scores work.
        variant_tasks: list[tuple[str, str]] = []
        for task_id, prompt in tasks:
            for index, variant in enumerate(mutation_variants(prompt)):
                variant_tasks.append((f"{task_id}__mut{index}", variant))

        def evaluate(checkpoint: str, *, prompts: list[tuple[str, str]], temperature: float) -> tuple[list[dict[str, object]], str | None]:
            """Run one checkpoint; failures (tokenizer mismatch etc.) are reported,
            never raised - the claim simply cannot be assessed then."""
            try:
                return _evaluate_checkpoint(root, work_dir, checkpoint, max_new_tokens=max_new_tokens,
                                            temperature=temperature, validation_project=validation_project,
                                            generate=generate_fn, validate=validate_fn, prompts=prompts), None
            except Exception as exc:
                return [], f"{checkpoint}: {exc}"

        def unresolved(errors: dict[str, str]) -> dict[str, object]:
            return {
                "format": "godot-coder-verify",
                "format_version": 1,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "claim": f"checkpoint A ({checkpoint_a}) is better than B ({checkpoint_b})" if checkpoint_b
                         else f"checkpoint {checkpoint_a} reaches at least {expected_pass_rate:.0%} pass rate",
                "verdict": VERDICT_UNRESOLVED,
                "reason": "the claim cannot be assessed: " + "; ".join(
                    f"{key} {value}" for key, value in errors.items()
                ),
                "checks": {},
                "summary": {
                    "a_pass_rate": pass_rate(cold_a) if not errors.get("checkpoint_a") else None,
                    "b_pass_rate": pass_rate(cold_b) if not errors.get("checkpoint_b") else None,
                },
                "errors": errors,
            }

        cold_a, a_error = evaluate(checkpoint_a, prompts=tasks, temperature=0.0)
        cold_b, b_error = evaluate(checkpoint_b, prompts=tasks, temperature=0.0) if checkpoint_b else ([], None)
        errors: dict[str, str] = {}
        if a_error:
            errors["checkpoint_a"] = a_error
        if b_error:
            errors["checkpoint_b"] = b_error
        if errors:
            return unresolved(errors)

        if checkpoint_b:
            mut_a, mut_a_error = evaluate(checkpoint_a, prompts=variant_tasks, temperature=0.0)
            mut_b, mut_b_error = evaluate(checkpoint_b, prompts=variant_tasks, temperature=0.0)
            warm_a, warm_a_error = evaluate(checkpoint_a, prompts=tasks, temperature=0.7)
            warm_b, warm_b_error = evaluate(checkpoint_b, prompts=tasks, temperature=0.7)
            for key, error in (("mutation_a", mut_a_error), ("mutation_b", mut_b_error),
                               ("warm_a", warm_a_error), ("warm_b", warm_b_error)):
                if error:
                    errors[key] = error
            if errors:
                return unresolved(errors)
        else:
            mut_a = mut_b = warm_a = warm_b = []

        # Reference-suffix scores need the tokenizer; tolerate a missing one.
        tokenizer = None
        try:
            _, tokenizer = _load_tokenizer(root, checkpoint_a)
        except Exception:
            tokenizer = None
        if tokenizer is not None:
            _with_reference_scores(tokenizer, cold_a)
            if cold_b:
                _with_reference_scores(tokenizer, cold_b)

        corpus_texts = _collect_corpus_texts(root)

        checks: dict[str, object] = {
            # The absolute expected-rate check only belongs to single-checkpoint
            # claims; "A is better than B" is a relative claim and must not be
            # judged against an arbitrary threshold.
            "trivial_pass": (
                check_trivial_pass(cold_a, cold_b) if checkpoint_b
                else check_trivial_pass(cold_a, cold_a, expected_rate=expected_pass_rate)
            ),
            # Single mode passes an empty B so leaks are not double-counted.
            "leak": check_leak(cold_a, cold_b, corpus_texts) if checkpoint_b else check_leak(cold_a, [], corpus_texts),
            "repetition": check_repetition(cold_a, cold_b) if checkpoint_b
                          else {
                              "verdict": VERDICT_UNRESOLVED,
                              "reason": "Single-checkpoint run: repetition needs a baseline B to compare against.",
                              "evidence": {},
                          },
        }
        if checkpoint_b:
            checks["mutation"] = check_mutation(
                {"a_rate": pass_rate(cold_a), "b_rate": pass_rate(cold_b)},
                {"a_rate": pass_rate(mut_a), "b_rate": pass_rate(mut_b)},
            )
            checks["temperature"] = check_temperature(
                {"a_rate": pass_rate(cold_a), "b_rate": pass_rate(cold_b)},
                {"a_rate": pass_rate(warm_a), "b_rate": pass_rate(warm_b)},
            )

        verdicts = [str(check.get("verdict")) for check in checks.values()]
        if VERDICT_FALSIFIED in verdicts:
            overall = VERDICT_FALSIFIED
        elif VERDICT_UNRESOLVED in verdicts and VERDICT_VERIFIED not in verdicts:
            overall = VERDICT_UNRESOLVED
        else:
            overall = VERDICT_VERIFIED

        report: dict[str, object] = {
            "format": "godot-coder-verify",
            "format_version": 1,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "claim": f"checkpoint A ({checkpoint_a}) is better than B ({checkpoint_b})" if checkpoint_b
                     else f"checkpoint {checkpoint_a} reaches at least {expected_pass_rate:.0%} pass rate",
            "verdict": overall,
            "checks": checks,
            "summary": {"a_pass_rate": pass_rate(cold_a), "b_pass_rate": pass_rate(cold_b) if cold_b else None},
        }
        return report
    finally:
        if owned_work_dir:
            _remove_work_dir(work_dir)


def _remove_work_dir(work_dir: Path) -> None:
    """Best-effort removal with a short retry.

    On Windows a Godot child can still hold a handle on the temp scripts the
    moment the run finishes; one retry a moment later succeeds. Leftover empty
    dirs in the system temp are harmless but untidy.
    """
    for attempt in range(3):
        try:
            shutil.rmtree(work_dir)
            return
        except OSError:
            if attempt == 2:
                return
            time.sleep(0.5)


def _assert_outside_project(work_dir: Path, root: Path) -> None:
    """The read-only guarantee: work must never land inside the project tree."""
    try:
        work_dir.resolve().relative_to(root)
    except ValueError:
        return
    raise ValueError("work_dir must be outside the project tree (read-only verify mode)")


def _print_report(report: dict[str, object]) -> None:
    print(f"claim:      {report['claim']}")
    print(f"verdict:    {report['verdict']}")
    for name, check in sorted((report["checks"] or {}).items()):
        print(f"  - {name:<12} {str(check.get('verdict')):<12} {str(check.get('reason'))[:90]}")
    summary = report.get("summary") or {}
    print(f"pass rate:  A={summary.get('a_pass_rate')}  B={summary.get('b_pass_rate')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adversarial read-only verification: try to falsify a checkpoint claim."
    )
    parser.add_argument("--checkpoint-a", required=True, help="Checkpoint A (the claimed winner)")
    parser.add_argument("--checkpoint-b", default=None, help="Optional checkpoint B to compare against")
    parser.add_argument("--expected-pass-rate", type=float, default=0.5,
                        help="Claimed pass rate for single-checkpoint runs (0.0-1.0)")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--validation-project", default="data/raw/seed_project")
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--work-dir", default=None,
                        help="External temp dir, left in place; defaults to a fresh system temp that is removed afterwards")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_verification(
        args.project_root,
        args.checkpoint_a,
        args.checkpoint_b,
        work_dir=args.work_dir,
        validation_project=args.validation_project,
        max_new_tokens=args.max_new_tokens,
        expected_pass_rate=args.expected_pass_rate,
    )
    _print_report(report)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
